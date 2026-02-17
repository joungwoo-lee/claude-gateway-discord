"""
Hybrid Retriever for Discord ↔ Claude Gateway
===============================================
게이트웨이 역할: 하이브리드 검색 + 세션 로깅 + 지연 인덱싱.

- sessions/{thread_id}.md: 세션별 대화 로그 (Q&A append)
- HybridRetriever API (localhost:9380): 하이브리드 검색/인덱싱
- 외부 RAG 미설정 시 → 내장 임베딩 시스템 (SQLite + sentence-transformers)
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import aiohttp

from local_embeddings import LocalEmbeddings, EmbeddingConfig

log = logging.getLogger("retriever")


@dataclass
class RetrieverConfig:
    session_memory: str = "none"  # none / local / external
    base_url: str = "http://localhost:9380"
    api_key: str = "secret-key"
    dataset_id: str = ""
    sessions_dir: Path = field(default_factory=lambda: Path.home() / ".claude" / "gateway-sessions")
    top_n: int = 8
    similarity_threshold: float = 0.2
    vector_similarity_weight: float = 0.3

    @classmethod
    def from_env(cls) -> "RetrieverConfig":
        # 고정된 경로 사용 (환경변수 무시)
        sessions_dir = Path.home() / ".claude" / "gateway-sessions"
        return cls(
            session_memory=os.getenv("CLAUDE_GATEWAY_SESSION_MEMORY", "none").lower().strip(),
            base_url=os.getenv("RETRIEVER_BASE_URL", "http://localhost:9380"),
            api_key=os.getenv("RETRIEVER_API_KEY", "secret-key"),
            dataset_id=os.getenv("RAG_DATASET_IDS", ""),
            sessions_dir=sessions_dir,
            top_n=int(os.getenv("RETRIEVER_TOP_N", "8")),
            similarity_threshold=float(
                os.getenv("RETRIEVER_SIMILARITY_THRESHOLD", "0.2")
            ),
            vector_similarity_weight=float(
                os.getenv("RETRIEVER_VECTOR_WEIGHT", "0.3")
            ),
        )


class HybridRetriever:
    """하이브리드 검색 + 세션 로깅 + 지연 인덱싱"""

    def __init__(self, config: RetrieverConfig):
        self.config = config
        self.memory_mode = config.session_memory  # none / local / external
        self.enabled = self.memory_mode == "external" and bool(config.dataset_id)
        self._session: aiohttp.ClientSession | None = None
        self._local_embeddings: LocalEmbeddings | None = None

        if self.memory_mode == "none":
            log.info("세션 기억 비활성화 (CLAUDE_GATEWAY_SESSION_MEMORY=none)")
        elif self.memory_mode == "external":
            if self.enabled:
                log.info("외부 RAG 활성화 (dataset=%s)", config.dataset_id)
            else:
                log.warning("CLAUDE_GATEWAY_SESSION_MEMORY=external 이지만 RAG_DATASET_IDS가 비어있어 기억 비활성화")
                self.memory_mode = "none"
        elif self.memory_mode == "local":
            log.info("내장 임베딩 시스템 사용 (CLAUDE_GATEWAY_SESSION_MEMORY=local)")
            emb_config = EmbeddingConfig(db_path=config.sessions_dir / "embeddings.db")
            self._local_embeddings = LocalEmbeddings(emb_config)
        else:
            log.warning("알 수 없는 CLAUDE_GATEWAY_SESSION_MEMORY 값: %s → none으로 동작", config.session_memory)
            self.memory_mode = "none"

    # ── HTTP 세션 관리 ──

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        if self._local_embeddings:
            await self._local_embeddings.close()

    # ── 세션 대화 로깅 ──

    async def log_conversation(
        self, thread_id: int, user_msg: str, assistant_msg: str, thread_name: str = ""
    ):
        """세션 대화를 sessions/{thread_id}.md에 append"""
        if self.memory_mode == "none":
            return
        try:
            # 절대 경로 확인
            sessions_dir_abs = self.config.sessions_dir.resolve()
            sessions_dir_abs.mkdir(parents=True, exist_ok=True)

            file_path = sessions_dir_abs / f"{thread_id}.md"
            log.info("세션 로그 저장 시도: %s", file_path)

            now = datetime.now().strftime("%Y-%m-%d %H:%M")

            # 첫 기록이면 헤더 추가
            if not file_path.exists():
                header = f"# Session: {thread_name or thread_id}\n\n"
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(header)
                log.info("새 세션 파일 생성: %s", file_path)

            entry = (
                f"## {now}\n\n"
                f"**User:** {user_msg}\n\n"
                f"**Assistant:** {assistant_msg}\n\n"
                f"---\n\n"
            )

            with open(file_path, "a", encoding="utf-8") as f:
                f.write(entry)

            log.info("세션 로그 저장 완료: %s (%d자, 크기: %d bytes)",
                    file_path.name, len(entry), file_path.stat().st_size)
        except Exception as e:
            log.error("세션 로그 저장 실패: %s", e, exc_info=True)

    # ── 지연 인덱싱 (새 세션 시작 시) ──

    async def index_pending_sessions(self):
        """변경된 세션 파일들을 인덱싱 (외부 RAG 또는 내장 임베딩)"""
        if self.memory_mode == "none":
            return
        try:
            # 절대 경로로 변환
            sessions_dir_abs = self.config.sessions_dir.resolve()
            sessions_dir_abs.mkdir(parents=True, exist_ok=True)

            log.info("세션 디렉토리 확인: %s", sessions_dir_abs)

            indexed = self._load_indexed()
            session_files = list(sessions_dir_abs.glob("*.md"))

            log.info("발견된 세션 파일: %d개", len(session_files))
            if session_files:
                log.info("세션 파일 목록: %s", [f.name for f in session_files[:5]])

            pending = [
                f for f in session_files
                if f.name not in indexed or indexed[f.name] != f.stat().st_size
            ]

            if not pending:
                log.info("인덱싱 대기 세션 없음 (전체: %d, 인덱싱됨: %d)",
                        len(session_files), len(indexed))
                return

            log.info("지연 인덱싱 시작: %d개 파일 (전체: %d개)", len(pending), len(session_files))

            for file_path in pending:
                try:
                    log.info("인덱싱 시도: %s (크기: %d bytes)",
                            file_path.name, file_path.stat().st_size)

                    if self.enabled:
                        # 외부 RAG 시스템 사용
                        await self._ingest(file_path)
                    else:
                        # 내장 임베딩 시스템 사용
                        await self._local_embeddings.index_file(file_path)

                    indexed[file_path.name] = file_path.stat().st_size
                    log.info("인덱싱 완료: %s", file_path.name)
                except Exception as e:
                    log.error("인덱싱 실패 (%s): %s", file_path.name, e, exc_info=True)

            self._save_indexed(indexed)
        except Exception as e:
            log.error("index_pending_sessions 전체 실패: %s", e, exc_info=True)

    def _load_indexed(self) -> dict[str, int]:
        """파일명 → 인덱싱 시점 파일 크기 매핑 로드"""
        indexed_file = self.config.sessions_dir / ".indexed.json"
        if indexed_file.exists():
            try:
                data = json.loads(indexed_file.read_text())
                # 하위호환: 이전 형식이 list면 크기 0으로 변환
                if isinstance(data, list):
                    return {name: 0 for name in data}
                return data
            except Exception:
                pass
        return {}

    def _save_indexed(self, indexed: dict[str, int]):
        indexed_file = self.config.sessions_dir / ".indexed.json"
        indexed_file.write_text(json.dumps(indexed, indent=2))

    # ── 상태 조회 ──

    async def get_status(self) -> dict:
        if self.memory_mode == "none":
            return {
                "mode": "none",
                "sessions_dir": str(self.config.sessions_dir.resolve()),
                "total_sessions": 0,
                "indexed_sessions": 0,
                "pending_sessions": 0,
            }

        sessions_dir_abs = self.config.sessions_dir.resolve()
        session_files = (
            list(sessions_dir_abs.glob("*.md"))
            if sessions_dir_abs.exists()
            else []
        )
        indexed = self._load_indexed()
        pending_count = sum(
            1 for f in session_files
            if f.name not in indexed or indexed[f.name] != f.stat().st_size
        )

        base_status = {
            "sessions_dir": str(sessions_dir_abs),
            "total_sessions": len(session_files),
            "indexed_sessions": len(indexed),
            "pending_sessions": pending_count,
        }

        if self.enabled:
            base_status.update({
                "mode": "external",
                "retriever_url": self.config.base_url,
                "dataset_id": self.config.dataset_id,
            })
        else:
            # 내장 임베딩 상태 추가
            local_status = await self._local_embeddings.get_status()
            base_status.update({
                "mode": "local",
                "db_path": local_status["db_path"],
                "model": local_status["model"],
                "total_chunks": local_status["total_chunks"],
                "total_threads": local_status["total_threads"],
            })

        return base_status

    # ── 인덱싱 (파일 업로드 + 파싱) ──

    async def _ingest(self, file_path: Path):
        """HybridRetriever API로 파일 업로드 + 파싱"""
        if not self.config.dataset_id:
            return

        session = await self._get_session()
        ds = self.config.dataset_id
        base = self.config.base_url

        # 1. 파일 업로드 (multipart)
        form = aiohttp.FormData()
        form.add_field(
            "file",
            open(file_path, "rb"),
            filename=file_path.name,
            content_type="text/markdown",
        )

        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        async with aiohttp.ClientSession(
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as upload_session:
            async with upload_session.post(
                f"{base}/api/v1/datasets/{ds}/documents",
                data=form,
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    log.warning("업로드 HTTP %d: %s", resp.status, body[:200])
                    return
                upload_data = await resp.json()

        # document_id 추출
        doc_data = upload_data.get("data", [])
        if isinstance(doc_data, list) and doc_data:
            doc_id = doc_data[0].get("id", "")
        elif isinstance(doc_data, dict):
            doc_id = doc_data.get("id", "")
        else:
            log.warning("업로드 응답에서 doc_id 추출 실패: %s", upload_data)
            return

        if not doc_id:
            log.warning("업로드: doc_id가 비어있음")
            return

        # 2. 파싱/청킹 트리거
        async with session.post(
            f"{base}/api/v1/datasets/{ds}/chunks",
            json={"document_ids": [doc_id]},
        ) as resp:
            if resp.status not in (200, 201):
                body = await resp.text()
                log.warning("파싱 HTTP %d: %s", resp.status, body[:200])
                return

        log.info("인덱싱 완료: %s → doc=%s", file_path.name, doc_id[:8])

    # ── 검색 ──

    async def search(self, query: str, top_k: int = None) -> list[dict]:
        """
        과거 세션 대화 검색 (외부 RAG 또는 내장 임베딩).
        Returns: [{"content": str, "similarity": float, "file_name": str, ...}, ...]
        """
        if self.memory_mode == "none":
            return []

        if top_k is None:
            top_k = self.config.top_n

        if self.enabled:
            # 외부 RAG 시스템 사용
            return await self._search_external(query, top_k)
        else:
            # 내장 임베딩 시스템 사용
            return await self._local_embeddings.search(query, top_k)

    async def _search_external(self, query: str, top_k: int) -> list[dict]:
        """외부 HybridRetriever API로 검색"""
        session = await self._get_session()
        base = self.config.base_url
        ds = self.config.dataset_id

        payload = {
            "question": query,
            "dataset_ids": [ds],
            "top_n": top_k,
            # similarity_threshold와 vector_similarity_weight는 API가 지원하지 않음
            # "similarity_threshold": self.config.similarity_threshold,
            # "vector_similarity_weight": self.config.vector_similarity_weight,
        }

        log.debug("Search payload: %s", payload)

        try:
            async with session.post(f"{base}/api/v1/retrieval", json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("검색 HTTP %d: %s", resp.status, body[:200])
                    return []

                data = await resp.json()
                # 응답 형식은 RAGFlow API에 맞게 조정 필요
                # 여기서는 간단히 파싱
                log.debug("Full response data: %s", data)
                results = []
                data_field = data.get("data", {})
                chunks = data_field.get("chunks", [])
                log.info("검색 응답: code=%s, data_field_keys=%s, chunks=%d",
                        data.get("code"), list(data_field.keys()), len(chunks))
                for item in chunks:
                    results.append({
                        "content": item.get("content", ""),
                        "similarity": item.get("similarity", 0.0),
                        "file_name": item.get("document_name", ""),
                        "thread_id": item.get("document_id", "").replace("oldsessions_", ""),
                    })
                return results

        except Exception as e:
            log.warning("검색 실패: %s", e)
            return []
