"""
Discord ↔ Claude Code Gateway Bot
===================================
Discord 스레드별로 독립된 Claude Code 세션을 유지하고,
메시지를 `claude -p` 로 전달 → 응답을 Discord로 중계하는 게이트웨이.

- 스레드마다 고유 Claude Code 세션 (UUID5 기반, 재시작해도 유지)
- 채널 본문 메시지 → 자동으로 스레드 생성
- --resume 으로 시간이 지나도 이전 대화 이어가기
- 세션별 대화 로깅 (sessions/{thread_id}.md)
- 새 세션 시작 시 이전 세션 파일 → HybridRetriever 지연 인덱싱

pip install discord.py python-dotenv aiohttp
"""

import os
import sys
import json
import uuid
import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv
import discord

from hybrid_retriever import RetrieverConfig, HybridRetriever

# ──────────────────────────────────────────────
# 환경 변수 로드
# ──────────────────────────────────────────────
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
CLAUDE_EXTRA_ARGS = os.getenv("CLAUDE_EXTRA_ARGS", "")

# HybridRetriever 설정
RETRIEVER_CONFIG = RetrieverConfig.from_env()

# ──────────────────────────────────────────────
# 로깅
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("claude-gw")

# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────
DISCORD_MAX_LEN = 1900
STREAM_INTERVAL = 1.5
PROCESS_TIMEOUT = 600

# 세션 매핑 파일 (봇 재시작해도 유지) - 고정 경로 사용
SESSION_MAP_FILE = Path.home() / ".claude" / "gateway-sessions" / "sessions.json"

# UUID5 네임스페이스 (Discord 스레드 ID → Claude 세션 UUID 변환용)
NAMESPACE_DISCORD = uuid.UUID("a3f1b2c4-d5e6-7890-abcd-ef1234567890")


def chunk_text(text: str, limit: int = DISCORD_MAX_LEN) -> list[str]:
    """긴 텍스트를 Discord 글자 수 제한에 맞게 분할"""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def get_default_model() -> str:
    """
    Claude Code의 기본 모델을 확인합니다.
    우선순위: CLAUDE_EXTRA_ARGS --model > settings.json > 기본값
    """
    # 1. CLAUDE_EXTRA_ARGS에서 --model 확인
    if CLAUDE_EXTRA_ARGS:
        args = CLAUDE_EXTRA_ARGS.split()
        for i, arg in enumerate(args):
            if arg == "--model" and i + 1 < len(args):
                return args[i + 1]

    # 2. settings.json 확인
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            if "model" in settings:
                return settings["model"]
        except Exception:
            pass

    # 3. 기본값
    return "sonnet"


# ──────────────────────────────────────────────
# 세션 매핑 관리
# ──────────────────────────────────────────────
class SessionManager:
    """
    Discord 스레드 ID ↔ Claude Code 세션 UUID 매핑.
    - 스레드 ID로 deterministic UUID5 생성
    - 첫 메시지: --session-id 로 새 세션 생성
    - 이후 메시지: --resume 으로 기존 세션 이어가기
    - 매핑을 파일에 저장하여 봇 재시작 후에도 유지
    """

    def __init__(self):
        # thread_id(str) -> {"session_id": str, "initialized": bool}
        self._map: dict[str, dict] = {}
        self._load()

    def _load(self):
        """파일에서 세션 매핑 로드"""
        # 디렉토리 자동 생성
        SESSION_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)

        if SESSION_MAP_FILE.exists():
            try:
                self._map = json.loads(SESSION_MAP_FILE.read_text())
                log.info("세션 매핑 로드: %d개", len(self._map))
            except Exception as e:
                log.warning("세션 매핑 로드 실패: %s", e)
                self._map = {}

    def _save(self):
        """세션 매핑을 파일에 저장"""
        try:
            # 디렉토리 자동 생성
            SESSION_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_MAP_FILE.write_text(json.dumps(self._map, indent=2))
        except Exception as e:
            log.warning("세션 매핑 저장 실패: %s", e)

    def get_session(self, thread_id: int) -> tuple[str, bool]:
        """
        스레드 ID에 대한 Claude 세션 정보를 반환.
        Returns: (session_uuid, is_new)
        """
        key = str(thread_id)

        if key in self._map:
            return self._map[key]["session_id"], False

        # 새 세션 — UUID5로 deterministic하게 생성
        session_id = str(uuid.uuid5(NAMESPACE_DISCORD, key))
        self._map[key] = {
            "session_id": session_id,
            "initialized": False,
        }
        self._save()
        return session_id, True

    def mark_initialized(self, thread_id: int):
        """세션이 첫 메시지를 처리했음을 기록"""
        key = str(thread_id)
        if key in self._map:
            self._map[key]["initialized"] = True
            self._save()

    def is_initialized(self, thread_id: int) -> bool:
        """해당 세션이 이미 시작되었는지 확인"""
        key = str(thread_id)
        return self._map.get(key, {}).get("initialized", False)

    def get_model(self, thread_id: int) -> str | None:
        """해당 세션에 지정된 모델 반환 (없으면 None)"""
        key = str(thread_id)
        return self._map.get(key, {}).get("model")

    def set_model(self, thread_id: int, model: str | None):
        """해당 세션의 모델 지정 (None이면 키 제거)"""
        key = str(thread_id)
        if key in self._map:
            if model:
                self._map[key]["model"] = model
            else:
                self._map[key].pop("model", None)
            self._save()

    def remove_session(self, thread_id: int):
        """세션 매핑 삭제 (리셋용)"""
        key = str(thread_id)
        self._map.pop(key, None)
        self._save()


# ──────────────────────────────────────────────
# Claude Code 게이트웨이
# ──────────────────────────────────────────────
class ClaudeGateway:
    """스레드별로 독립된 Claude Code 세션을 관리하는 게이트웨이"""

    def __init__(self, retriever: HybridRetriever):
        self.sessions = SessionManager()
        self.retriever = retriever
        # thread_id -> True (해당 스레드에서 현재 처리 중)
        self._busy: dict[int, bool] = {}
        self._processes: dict[int, asyncio.subprocess.Process] = {}
        # thread_id -> (prompt, thread) 대기열 (최신 1개만 유지)
        self._pending: dict[int, tuple[str, discord.Thread | discord.TextChannel]] = {}

    def is_busy(self, thread_id: int) -> bool:
        return self._busy.get(thread_id, False)

    async def ask(
        self, prompt: str, thread: discord.Thread | discord.TextChannel, thread_id: int
    ) -> None:
        """프롬프트를 해당 스레드의 Claude 세션으로 전달 (처리 중이면 큐에 대기)"""
        if self.is_busy(thread_id):
            overwrite = thread_id in self._pending
            self._pending[thread_id] = (prompt, thread)
            if overwrite:
                await thread.send("⏳ 이전 대기 요청은 덮어씌워집니다. 완료 후 입력됩니다.")
            else:
                await thread.send("⏳ 이전 요청 처리 중입니다. 완료 후 입력됩니다.")
            return

        self._busy[thread_id] = True
        try:
            await self._run_claude(prompt, thread, thread_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.exception("Claude 실행 오류: %s", e)
            await thread.send(f"❌ 오류 발생: `{e}`")
        finally:
            self._busy[thread_id] = False
            self._processes.pop(thread_id, None)
            # 대기 중인 요청이 있으면 자동 실행
            pending = self._pending.pop(thread_id, None)
            if pending:
                pending_prompt, pending_thread = pending
                asyncio.create_task(self.ask(pending_prompt, pending_thread, thread_id))

    async def cancel(self, thread_id: int) -> bool:
        """해당 스레드의 진행 중인 요청 취소 (대기 중인 요청도 제거)"""
        self._pending.pop(thread_id, None)
        proc = self._processes.get(thread_id)
        if not proc:
            return False
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        return True

    def reset_session(self, thread_id: int):
        """해당 스레드의 세션 초기화"""
        self._pending.pop(thread_id, None)
        self.sessions.remove_session(thread_id)

    async def _run_claude(
        self, prompt: str, thread: discord.Thread | discord.TextChannel, thread_id: int
    ):
        """claude -p 실행 및 stdout 실시간 스트리밍 (Discord 입력 중 상태 활용)"""
        session_id, is_new = self.sessions.get_session(thread_id)
        initialized = self.sessions.is_initialized(thread_id)

        # 새 세션 시작 시 미인덱싱 세션 파일 지연 인덱싱
        if is_new:
            log.info("새 세션 시작 - 인덱싱 시도 (retriever.enabled=%s)", self.retriever.enabled)
            try:
                await self.retriever.index_pending_sessions()
            except Exception as e:
                log.error("지연 인덱싱 실패: %s", e, exc_info=True)

        # 명령어 구성
        cmd = ["claude", "-p"]

        if not initialized:
            cmd.extend(["--session-id", session_id])
        else:
            cmd.extend(["--resume", session_id])

        # 세션별 모델 지정
        model = self.sessions.get_model(thread_id)
        if model:
            cmd.extend(["--model", model])

        if CLAUDE_EXTRA_ARGS:
            cmd.extend(CLAUDE_EXTRA_ARGS.split())

        cmd.append("--")
        cmd.append(prompt)

        log.info("[%s] → %s", session_id[:8], " ".join(cmd[:6]))

        # CLAUDECODE 환경변수 제거
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._processes[thread_id] = proc

        output_buffer = ""

        # Discord "입력 중..." 표시 유지하며 응답 대기
        try:
            async with thread.typing():
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            proc.stdout.read(4096),
                            timeout=PROCESS_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        await thread.send("⏰ 응답 타임아웃 (10분 초과)")
                        proc.terminate()
                        return

                    if not chunk:
                        break  # EOF

                    text = chunk.decode("utf-8", errors="replace")
                    output_buffer += text
        finally:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()

        # 세션 초기화 기록
        if not initialized:
            self.sessions.mark_initialized(thread_id)

        # stderr 확인
        stderr_data = await proc.stderr.read()
        if stderr_data:
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
            if stderr_text:
                log.warning("[%s] stderr: %s", session_id[:8], stderr_text[:500])

        # 최종 출력 전송 (수정 없이 일반 전송)
        if output_buffer.strip():
            chunks = chunk_text(output_buffer.strip(), limit=DISCORD_MAX_LEN)
            for chunk in chunks:
                await thread.send(chunk)
                await asyncio.sleep(0.3)

            # 세션 대화 로깅
            thread_name = getattr(thread, "name", "")
            try:
                log.info("세션 로깅 시도: thread_id=%s, thread_name=%s", thread_id, thread_name)
                await self.retriever.log_conversation(
                    thread_id, prompt, output_buffer.strip(), thread_name
                )
            except Exception as e:
                log.error("세션 로깅 실패: %s", e, exc_info=True)
        else:
            await thread.send("⚠️ Claude로부터 응답이 없습니다.")

    async def _update_discord(self, text, status_msg, sent_messages, channel):
        """더 이상 사용되지 않음 (스트리밍 대신 typing... 사용)"""
        pass

    async def _send_final(self, text, status_msg, sent_messages, channel):
        """더 이상 사용되지 않음 (수정 전송 대신 일반 전송 사용)"""
        pass


# ──────────────────────────────────────────────
# Discord 봇
# ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
retriever = HybridRetriever(RETRIEVER_CONFIG)
gateway = ClaudeGateway(retriever)


def is_authorized(message: discord.Message) -> bool:
    if message.author.id != ADMIN_USER_ID:
        return False
    return True


def get_thread_id(message: discord.Message) -> int | None:
    """메시지가 스레드 안에 있으면 스레드 ID, 아니면 None"""
    if isinstance(message.channel, discord.Thread):
        return message.channel.id
    return None


@client.event
async def on_ready():
    log.info("봇 로그인 완료: %s (ID: %s)", client.user.name, client.user.id)
    if CHANNEL_ID:
        channel = client.get_channel(CHANNEL_ID)
        if channel:
            try:
                await channel.send(
                    "🟢 **Claude Code 게이트웨이 온라인**\n"
                    "메시지를 보내면 자동으로 스레드가 생성되고 Claude Code와 대화합니다.\n"
                    "스레드마다 독립된 세션이 유지됩니다.\n"
                    "`!cancel` 취소 | `!reset` 세션 리셋 | `!restart` 재시작 | `!status` 상태 | `!model` 모델 변경"
                )
            except discord.Forbidden:
                log.warning("채널 %s에 전송 권한 없음", CHANNEL_ID)


# ──────────────────────────────────────────────
# 모델 선택 UI
# ──────────────────────────────────────────────
MODEL_CHOICES = [
    ("sonnet", "Sonnet"),
    ("opus", "Opus"),
    ("haiku", "Haiku"),
]


class ModelSelect(discord.ui.Select):
    def __init__(self, thread_id: int, current_model: str | None):
        options = []
        for model_id, label in MODEL_CHOICES:
            is_default = model_id == current_model
            options.append(discord.SelectOption(
                label=label, value=model_id, description=model_id,
                default=is_default,
            ))
        # 기본값(모델 미지정) 옵션
        options.append(discord.SelectOption(
            label="기본값", value="__default__",
            description="Claude Code 기본 모델",
            default=current_model is None,
        ))
        super().__init__(placeholder="모델을 선택하세요", options=options)
        self.thread_id = thread_id

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        if chosen == "__default__":
            gateway.sessions.set_model(self.thread_id, None)
            await interaction.response.edit_message(
                content="✅ 이 스레드는 **기본 모델**을 사용합니다.", view=None,
            )
        else:
            gateway.sessions.set_model(self.thread_id, chosen)
            label = next((l for m, l in MODEL_CHOICES if m == chosen), chosen)
            await interaction.response.edit_message(
                content=f"✅ 이 스레드의 모델이 **{label}** (`{chosen}`)로 설정되었습니다.", view=None,
            )


class ModelSelectView(discord.ui.View):
    def __init__(self, thread_id: int, current_model: str | None):
        super().__init__(timeout=60)
        self.add_item(ModelSelect(thread_id, current_model))


@client.event
async def on_message(message: discord.Message):
    if message.author.id == client.user.id:
        return

    if not is_authorized(message):
        if message.content.startswith("!"):
            try:
                await message.channel.send("🚫 권한이 없습니다.")
            except discord.HTTPException:
                pass
        return

    content = message.content.strip()
    if not content:
        return

    thread_id = get_thread_id(message)

    # ────────────────────────────────────────
    # 관리 명령어
    # ────────────────────────────────────────

    if content == "!restart":
        await message.channel.send("🔄 게이트웨이를 재시작합니다...")
        await client.close()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    if content == "!cancel":
        if thread_id:
            cancelled = await gateway.cancel(thread_id)
            msg = "🛑 요청을 취소했습니다." if cancelled else "ℹ️ 진행 중인 요청이 없습니다."
            await message.channel.send(msg)
        else:
            await message.channel.send("ℹ️ 스레드 안에서 사용하세요.")
        return

    if content == "!reset":
        if thread_id:
            if gateway.is_busy(thread_id):
                await gateway.cancel(thread_id)
                await asyncio.sleep(1)

            # 리셋 전 미인덱싱 세션 파일 인덱싱
            log.info("세션 리셋 - 인덱싱 시도 (retriever.enabled=%s)", retriever.enabled)
            try:
                await retriever.index_pending_sessions()
            except Exception as e:
                log.error("리셋 시 인덱싱 실패: %s", e, exc_info=True)

            gateway.reset_session(thread_id)
            await message.channel.send("🔄 이 스레드의 세션이 초기화되었습니다.")
        else:
            await message.channel.send("ℹ️ 스레드 안에서 사용하세요.")
        return

    if content == "!status":
        if thread_id:
            busy = gateway.is_busy(thread_id)
            init = gateway.sessions.is_initialized(thread_id)
            sid, _ = gateway.sessions.get_session(thread_id)
            model = gateway.sessions.get_model(thread_id)
            model_label = next((l for m, l in MODEL_CHOICES if m == model), model) if model else "기본값"
            status_text = (
                f"📊 세션: `{sid[:8]}...`\n"
                f"상태: **{'처리 중' if busy else '대기 중'}**\n"
                f"이력: **{'있음' if init else '새 세션'}**\n"
                f"모델: **{model_label}**"
            )
            rs = await retriever.get_status()
            mode_map = {"none": "비활성화", "external": "외부 RAG", "local": "내장 임베딩"}
            mode_text = mode_map.get(rs.get("mode"), rs.get("mode", "알 수 없음"))
            status_text += f"\n\n🔍 **세션 기억** ({mode_text})"
            if rs.get("mode") != "none":
                status_text += (
                    f"\n디렉토리: `{rs.get('sessions_dir', 'N/A')}`\n"
                    f"세션 파일: {rs['total_sessions']}개 "
                    f"(인덱싱: {rs['indexed_sessions']}, 대기: {rs['pending_sessions']})"
                )
            if rs.get("mode") == "local":
                status_text += (
                    f"\n청크: {rs.get('total_chunks', 0)}개 "
                    f"(스레드: {rs.get('total_threads', 0)}개)"
                )
            await message.channel.send(status_text)
        else:
            default_model = get_default_model()
            model_label = next((l for m, l in MODEL_CHOICES if m == default_model), default_model)
            await message.channel.send(
                f"📊 활성 세션: **{len(gateway.sessions._map)}**개\n"
                f"처리 중: **{sum(gateway._busy.values())}**건\n"
                f"기본 모델: **{model_label}** (`{default_model}`)"
            )
        return

    if content == "!model":
        if thread_id:
            current_model = gateway.sessions.get_model(thread_id)
            view = ModelSelectView(thread_id, current_model)
            if current_model:
                label = next((l for m, l in MODEL_CHOICES if m == current_model), current_model)
                msg_text = f"🤖 현재 모델: **{label}** (`{current_model}`)\n변경할 모델을 선택하세요:"
            else:
                msg_text = "🤖 현재 모델: **기본값**\n변경할 모델을 선택하세요:"
            await message.channel.send(msg_text, view=view)
        else:
            await message.channel.send("ℹ️ 스레드 안에서 사용하세요.")
        return

    # ────────────────────────────────────────
    # 스레드 안에서 메시지 → 해당 세션으로 전달
    # ────────────────────────────────────────
    if thread_id:
        await message.add_reaction("📨")
        await gateway.ask(content, message.channel, thread_id)
        return

    # ────────────────────────────────────────
    # 채널 본문 메시지 → 자동 스레드 생성 후 전달
    # ────────────────────────────────────────
    if CHANNEL_ID and message.channel.id != CHANNEL_ID:
        return

    # 메시지 내용 앞 30자를 스레드 제목으로 사용
    thread_name = content[:30] + ("..." if len(content) > 30 else "")
    
    # 이미 생성된 스레드 확인 (error code 160004 대응)
    if message.thread:
        await message.add_reaction("📨")
        await gateway.ask(content, message.thread, message.thread.id)
        return

    try:
        thread = await message.create_thread(name=thread_name)
    except discord.HTTPException as e:
        if e.code == 160004: 
            # 이미 스레드가 생성된 경우, API를 다시 찔러 해당 메시지 객체의 thread를 다시 가져와 보거나 
            # fetch_channel 등을 통해 찾을 수도 있지만, 보통 message.thread가 자동으로 채워지지 않았을 때 발생함.
            log.info("이미 생성된 스레드가 존재하여 다시 조회합니다.")
            msg = await message.channel.fetch_message(message.id)
            if msg.thread:
                thread = msg.thread
            else:
                log.warning("스레드 생성 에러는 났으나 fetch된 메시지에도 스레드가 없습니다.")
                await message.channel.send("❌ 이미 스레드가 존재하지만 찾을 수 없습니다.")
                return
        else:
            log.warning("스레드 생성 실패: %s", e)
            await message.channel.send("❌ 스레드 생성에 실패했습니다.")
            return

    await message.add_reaction("📨")
    await gateway.ask(content, thread, thread.id)


# ──────────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────────
def main():
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN이 .env 파일에 설정되지 않았습니다.")
        return
    if not ADMIN_USER_ID:
        print("❌ ADMIN_USER_ID가 .env 파일에 설정되지 않았습니다.")
        return

    async def runner():
        async with client:
            await client.start(DISCORD_TOKEN)

    try:
        asyncio.run(runner())
    finally:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(retriever.close())
        loop.close()


if __name__ == "__main__":
    main()
