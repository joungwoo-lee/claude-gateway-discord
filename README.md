# Discord ↔ Claude Code Gateway Bot

**Use Claude Code CLI like OpenClaw through Discord** — OpenClaw token usage becoming too expensive? This project lets you use Claude Code as an alternative to OpenClaw. Control Claude Code directly from Discord, manage different models per session, and leverage RAG-powered access to past conversation history.

A gateway bot that maintains independent Claude Code sessions per Discord thread, forwarding user messages to Claude Code and streaming responses back to Discord. Perfect for developers who want interactive Claude Code workflows within Discord without leaving your chat.

## Quick Start

```bash
./setup.sh
```

After setup, run `claudegateway` from anywhere.
Like `claude`, run `claudegateway` inside the project directory you want to work on.

## Key Features

- **Thread-based independent sessions** — Sending a message in a channel automatically creates a thread, with a separate Claude Code session assigned to each thread.
- **Session persistence** — Deterministic session IDs based on UUID5 allow conversations to resume even after bot restarts (`--resume`).
- **Real-time streaming** — Claude Code responses are streamed to Discord messages at 1.5-second intervals.
- **Admin commands** — Control sessions with `!cancel`, `!reset`, and `!status`.
- **Single admin** — Only the user specified by `ADMIN_USER_ID` can use the bot.

## How It Works

```
User sends a message in a channel
        │
        ▼
   Thread auto-created (first 30 chars as title)
        │
        ▼
   📨 Reaction added to confirm receipt
        │
        ▼
   claude -p --session-id <uuid> -- "<message>"  (first message)
   claude -p --resume <uuid> -- "<message>"       (subsequent messages)
        │
        ▼
   💭 "Waiting for Claude response..." indicator
        │
        ▼
   Read stdout in real-time → update Discord message every 1.5s
        │
        ▼
   Response complete → send final text (split at 1900 chars)
```

## Installation

### Prerequisites

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude` command must be in PATH)
- Discord bot token (see "Discord Bot Setup" below)

### Discord Bot Setup

Create a bot on the Discord Developer Portal and invite it to your server.

#### 1. Create an Application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and log in.
2. Click **"New Application"** in the top right, enter a name, and create it.

#### 2. Get the Bot Token

1. Select the **"Bot"** tab from the left menu.
2. Click **"Reset Token"** to generate a token.
3. Copy the token and paste it into `DISCORD_TOKEN` in your `.env` file.

> **Warning**: The token is only shown once — copy it immediately. If lost, you must regenerate it with Reset Token.

#### 3. Enable Privileged Gateway Intents

In the same **"Bot"** tab, enable all of the following:

- **Presence Intent**
- **Server Members Intent**
- **Message Content Intent** — Required for the bot to read message contents.

#### 4. Invite the Bot to Your Server

1. Select the **"OAuth2"** tab from the left menu.
2. In the **"OAuth2 URL Generator"** section, check `bot` under Scopes.
3. Under Bot Permissions, select the following:
   - `Send Messages`
   - `Send Messages in Threads`
   - `Create Public Threads`
   - `Read Message History`
   - `Add Reactions`
   - `Use Slash Commands` (optional)
4. Open the generated URL in your browser and select the server to invite the bot to.

#### 5. Get User ID / Channel ID

1. In Discord client, enable **Settings > Advanced > Developer Mode**.
2. **User ID**: Right-click your profile > **"Copy User ID"** → paste into `ADMIN_USER_ID` in `.env`
3. **Channel ID**: Right-click the channel > **"Copy Channel ID"** → paste into `CHANNEL_ID` in `.env` (set to `0` for all channels)

### Install

```bash
git clone <repository-url>
cd claude-gateway-discord
./setup.sh
```

### Environment Variables

Copy `.env.example` to `.env` and fill in the values.

```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `ADMIN_USER_ID` | Yes | Discord user ID authorized to control the bot |
| `CHANNEL_ID` | - | Channel ID where the bot operates (0 for all channels) |
| `CLAUDE_EXTRA_ARGS` | - | Extra Claude Code arguments (e.g., `--dangerously-skip-permissions`, `--model sonnet`) |
| `RAG_DATASET_IDS` | - | External RAG system dataset ID (uses built-in embeddings if not set) |
| `RETRIEVER_BASE_URL` | - | External RAG system URL (default: `http://localhost:9380`) |
| `RETRIEVER_API_KEY` | - | External RAG system API key (default: `secret-key`) |

> To find your Discord user ID: Settings > Advanced > Developer Mode > right-click your profile > "Copy User ID".

## Running

```bash
claudegateway
```

Once the bot is online, a status message is sent to the configured channel.

## Usage

### Starting a Conversation

Send a message in the designated channel — a thread is automatically created and a conversation with Claude Code begins.

### Admin Commands (use inside threads)

| Command | Description |
|---------|-------------|
| `!cancel` | Cancel the in-progress request |
| `!reset` | Reset the current thread's session |
| `!status` | View session info (session ID, processing state, retrieval system mode) |
| `!model [sonnet\|opus\|haiku]` | Change the model for the current thread |

### Session Memory & Search

**Auto-indexing**
- All conversations are automatically saved to `~/.claude/gateway-sessions/{thread_id}.md`.
- Past sessions are automatically indexed when a new session starts.
- Sessions are always saved to the same path **regardless of the working directory**.

**Searching Past Sessions**

To search past session conversations, use the `/search-sessions` skill or the following command:

```bash
python .claude/skills/search-gateway-sessions/scripts/search_gateway_sessions.py "search query"
```

**Retrieval System Modes**

The system automatically selects one of the following based on environment variables:

1. **External RAG System** (when `RAG_DATASET_IDS` is set)
   - Uses HybridRetriever API (localhost:9380)
   - Dataset IDs are read from the `RAG_DATASET_IDS` environment variable
   - Advanced hybrid search (BM25 + Vector)

2. **Built-in Embedding System** (when `RAG_DATASET_IDS` is not set)
   - Uses SQLite + sentence-transformers
   - Runs locally with no external dependencies
   - Multilingual embedding model (`paraphrase-multilingual-MiniLM-L12-v2`)
   - DB path: `~/.claude/gateway-sessions/embeddings.db`

**Common Behavior**

- Past session conversations are stored as per-thread `.md` files in `~/.claude/gateway-sessions/`
- Automatically indexed when a new session starts (lazy indexing)
- When a user mentions previous conversations, past context, or memory, this search is utilized

**Checking Status**

Use the `!status` command in Discord to check the current retrieval system mode and indexing status.

**Architecture**

```
main.py
├── SessionManager     Session mapping (thread_id ↔ UUID5, sessions.json persistence)
├── ClaudeGateway      Claude Code subprocess execution & streaming
└── Discord Bot        Event handlers (on_ready, on_message)

hybrid_retriever.py
├── RetrieverConfig    Config management (env vars → dataclass)
└── HybridRetriever    Retrieval system integration (external RAG or built-in embeddings)
    ├── Session logging     Save conversations as .md files
    ├── Lazy indexing       Index past sessions on new session start
    └── Search              Similarity search over past conversations

local_embeddings.py
├── EmbeddingConfig    Built-in embedding config (model, DB path, chunking settings)
└── LocalEmbeddings    SQLite + sentence-transformers based local search
    ├── Text chunking       Split documents by chunk_size
    ├── Embedding           Convert to vectors with multilingual model
    ├── DB storage          Store chunks + vectors in SQLite
    └── Similarity search   Top-k search by cosine similarity
```

### Session Management

1. Discord thread IDs are converted to deterministic session IDs via UUID5 namespace
2. Mappings are saved in `sessions.json` for persistence across bot restarts
3. First message uses `--session-id` to create a new session; subsequent messages use `--resume`

### Key Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `DISCORD_MAX_LEN` | 1900 | Max characters per Discord message |
| `STREAM_INTERVAL` | 1.5s | Streaming update interval |
| `PROCESS_TIMEOUT` | 300s | Claude process timeout |

## License

MIT

---

# Discord ↔ Claude Code 게이트웨이 봇 (한국어)

Discord 스레드별로 독립된 Claude Code 세션을 유지하며, 사용자의 메시지를 Claude Code로 전달하고 응답을 실시간 스트리밍하는 게이트웨이 봇입니다.

## 실행법

```bash
./setup.sh
```
setup 후 아무데서나 `claudegateway` 입력하면 실행됩니다.
claude와 마찬가지로 작업할 프로젝트 안에서 `claudegateway` 실행하세요.

## 주요 기능

- **스레드 기반 독립 세션** — 채널에 메시지를 보내면 자동으로 스레드가 생성되고, 스레드마다 별도의 Claude Code 세션이 할당됩니다.
- **세션 영속성** — UUID5 기반의 결정론적 세션 ID로, 봇을 재시작해도 이전 대화를 이어갑니다 (`--resume`).
- **실시간 스트리밍** — Claude Code의 응답을 1.5초 간격으로 Discord 메시지에 실시간 반영합니다.
- **관리 명령어** — `!cancel`, `!reset`, `!status`로 세션을 제어할 수 있습니다.
- **단일 관리자** — `ADMIN_USER_ID`로 지정된 사용자만 봇을 사용할 수 있습니다.

## 동작 흐름

```
사용자가 채널에 메시지 전송
        │
        ▼
   자동 스레드 생성 (메시지 앞 30자가 제목)
        │
        ▼
   📨 리액션으로 수신 확인
        │
        ▼
   claude -p --session-id <uuid> -- "<메시지>"  (첫 메시지)
   claude -p --resume <uuid> -- "<메시지>"       (이후 메시지)
        │
        ▼
   💭 "Claude 응답 대기 중..." 표시
        │
        ▼
   stdout 실시간 읽기 → 1.5초마다 Discord 메시지 업데이트
        │
        ▼
   응답 완료 → 최종 텍스트 전송 (1900자 단위 분할)
```

## 설치

### 사전 요구사항

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude` 명령어가 PATH에 있어야 함)
- Discord 봇 토큰 (아래 "디스코드 봇 설정" 참고)

### 디스코드 봇 설정

Discord Developer Portal에서 봇을 생성하고 서버에 초대하는 과정입니다.

#### 1. 애플리케이션 생성

1. [Discord Developer Portal](https://discord.com/developers/applications)에 접속하여 로그인합니다.
2. 우측 상단 **"New Application"** 클릭 후 이름을 입력하고 생성합니다.

#### 2. 봇 토큰 발급

1. 좌측 메뉴에서 **"Bot"** 탭을 선택합니다.
2. **"Reset Token"** 버튼을 클릭하여 토큰을 발급받습니다.
3. 발급된 토큰을 복사하여 `.env` 파일의 `DISCORD_TOKEN`에 입력합니다.

> **주의**: 토큰은 한 번만 표시되므로 즉시 복사해 두세요. 분실 시 Reset Token으로 재발급해야 합니다.

#### 3. Privileged Gateway Intents 활성화

같은 **"Bot"** 탭에서 아래 항목들을 모두 **ON**으로 설정합니다:

- **Presence Intent**
- **Server Members Intent**
- **Message Content Intent** — 이 봇이 메시지 내용을 읽으려면 반드시 필요합니다.

#### 4. 봇 서버 초대

1. 좌측 메뉴에서 **"OAuth2"** 탭을 선택합니다.
2. **"OAuth2 URL Generator"** 섹션에서 Scopes에 `bot`을 체크합니다.
3. Bot Permissions에서 아래 권한을 선택합니다:
   - `Send Messages` — 메시지 전송
   - `Send Messages in Threads` — 스레드 내 메시지 전송
   - `Create Public Threads` — 공개 스레드 생성
   - `Read Message History` — 메시지 기록 읽기
   - `Add Reactions` — 리액션 추가
   - `Use Slash Commands` — (선택) 슬래시 커맨드 사용
4. 하단에 생성된 URL을 브라우저에서 열고, 봇을 초대할 서버를 선택합니다.

#### 5. 사용자 ID / 채널 ID 확인

1. Discord 클라이언트에서 **설정 > 고급 > 개발자 모드**를 활성화합니다.
2. **사용자 ID**: 자신의 프로필을 우클릭 > **"사용자 ID 복사"** → `.env`의 `ADMIN_USER_ID`에 입력
3. **채널 ID**: 봇을 사용할 채널을 우클릭 > **"채널 ID 복사"** → `.env`의 `CHANNEL_ID`에 입력 (모든 채널에서 사용하려면 `0`)

### 설치 방법

```bash
git clone <repository-url>
cd claude-gateway-discord
./setup.sh
```

### 환경 변수 설정

`.env.example`을 복사하여 `.env` 파일을 만들고 값을 입력합니다.

```bash
cp .env.example .env
```

| 변수 | 필수 | 설명 |
|------|------|------|
| `DISCORD_TOKEN` | O | Discord 봇 토큰 |
| `ADMIN_USER_ID` | O | 봇을 제어할 Discord 사용자 ID |
| `CHANNEL_ID` | - | 봇이 동작할 채널 ID (0이면 모든 채널) |
| `CLAUDE_EXTRA_ARGS` | - | Claude Code 추가 인자 (예: `--dangerously-skip-permissions`, `--model sonnet`) |
| `RAG_DATASET_IDS` | - | 외부 RAG 시스템 Dataset ID (미설정 시 내장 임베딩 사용) |
| `RETRIEVER_BASE_URL` | - | 외부 RAG 시스템 URL (기본: `http://localhost:9380`) |
| `RETRIEVER_API_KEY` | - | 외부 RAG 시스템 API 키 (기본: `secret-key`) |

> Discord 사용자 ID는 Discord 설정 > 고급 > 개발자 모드 활성화 후, 프로필 우클릭 > "사용자 ID 복사"로 확인할 수 있습니다.

## 실행

```bash
claudegateway
```

봇이 온라인되면 설정된 채널에 상태 메시지가 전송됩니다.

## 사용법

### 대화 시작

지정된 채널에 메시지를 보내면 자동으로 스레드가 생성되고 Claude Code와 대화가 시작됩니다.

### 관리 명령어 (스레드 안에서 사용)

| 명령어 | 설명 |
|--------|------|
| `!cancel` | 진행 중인 요청 취소 |
| `!reset` | 현재 스레드의 세션 초기화 |
| `!status` | 세션 정보 확인 (세션 ID, 처리 상태, 검색 시스템 모드) |
| `!model [sonnet\|opus\|haiku]` | 현재 스레드의 모델 변경 |

### 세션 메모리 및 검색

**자동 인덱싱**
- 모든 대화는 `~/.claude/gateway-sessions/{thread_id}.md`에 자동 저장됩니다.
- 새 세션 시작 시 과거 세션이 자동으로 인덱싱됩니다.
- **실행 위치와 무관하게** 항상 동일한 경로에 세션이 저장됩니다.

**과거 세션 기억 검색**

과거 세션의 대화 내용이 필요할 때, `/search-sessions` 스킬 또는 다음 명령어로 검색할 수 있다:

```bash
python .claude/skills/search-gateway-sessions/scripts/search_gateway_sessions.py "검색어"
```

**검색 시스템 모드**

시스템은 환경 변수에 따라 자동으로 다음 중 하나를 선택한다:

1. **외부 RAG 시스템** (env에 `RAG_DATASET_IDS` 설정 시)
   - HybridRetriever API (localhost:9380) 사용
   - dataset-ids는 환경변수 `RAG_DATASET_IDS`에서 자동으로 읽힌다
   - 고급 하이브리드 검색 (BM25 + Vector)

2. **내장 임베딩 시스템** (env에 `RAG_DATASET_IDS` 미설정 시)
   - SQLite + sentence-transformers 사용
   - 외부 의존성 없이 로컬에서 동작
   - 다국어 임베딩 모델 (`paraphrase-multilingual-MiniLM-L12-v2`)
   - DB 경로: `~/.claude/gateway-sessions/embeddings.db`

**공통 동작**

- 과거 세션 대화는 `~/.claude/gateway-sessions/` 폴더에 스레드별 `.md` 파일로 저장됨
- 새 세션 시작 시 자동으로 인덱싱됨 (지연 인덱싱)
- 사용자가 이전 대화, 과거 맥락, 기억을 언급하면 이 검색을 활용하라

**상태 확인**

Discord에서 `!status` 명령어로 현재 검색 시스템 모드와 인덱싱 상태를 확인할 수 있다.


**아키텍처**

```
main.py
├── SessionManager     세션 매핑 관리 (thread_id ↔ UUID5, sessions.json 영속화)
├── ClaudeGateway      Claude Code 서브프로세스 실행 및 스트리밍
└── Discord Bot        이벤트 핸들러 (on_ready, on_message)

hybrid_retriever.py
├── RetrieverConfig    설정 관리 (환경변수 → dataclass)
└── HybridRetriever    검색 시스템 통합 (외부 RAG or 내장 임베딩)
    ├── 세션 로깅       대화를 .md 파일로 저장
    ├── 지연 인덱싱     새 세션 시작 시 과거 세션 인덱싱
    └── 검색            과거 대화 유사도 검색

local_embeddings.py
├── EmbeddingConfig    내장 임베딩 설정 (모델, DB 경로, 청킹 설정)
└── LocalEmbeddings    SQLite + sentence-transformers 기반 로컬 검색
    ├── 텍스트 청킹      문서를 chunk_size로 분할
    ├── 임베딩 생성      다국어 모델로 벡터 변환
    ├── DB 저장         SQLite에 청크 + 벡터 저장
    └── 유사도 검색      코사인 유사도로 top-k 검색
```

### 세션 관리 방식

1. Discord 스레드 ID를 UUID5 네임스페이스로 변환하여 결정론적 세션 ID 생성
2. `sessions.json`에 매핑을 저장하여 봇 재시작 후에도 유지
3. 첫 메시지는 `--session-id`로 새 세션 생성, 이후는 `--resume`으로 이어가기

### 주요 상수

| 상수 | 값 | 설명 |
|------|-----|------|
| `DISCORD_MAX_LEN` | 1900 | Discord 메시지당 최대 글자 수 |
| `STREAM_INTERVAL` | 1.5초 | 스트리밍 업데이트 주기 |
| `PROCESS_TIMEOUT` | 300초 | Claude 프로세스 타임아웃 |

## 라이선스

MIT
