#!/usr/bin/env python3
"""
세션 검색 독립 스크립트 (스킬용)
- hybrid_retriever / local_embeddings 의존성 없음
- 환경변수만 사용 (main.py가 .env를 로드하므로 이미 설정됨)
- external: HTTP POST (urllib, stdlib만 사용)
- local: SQLite + numpy + sentence-transformers 직접 사용
"""

import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ── 설정 (환경변수에서 읽음) ──

CLAUDE_GATEWAY_SESSION_MEMORY = os.getenv("CLAUDE_GATEWAY_SESSION_MEMORY", "none").lower().strip()
BASE_URL = os.getenv("RETRIEVER_BASE_URL", "http://localhost:9380")
API_KEY = os.getenv("RETRIEVER_API_KEY", "secret-key")
DATASET_ID = os.getenv("RAG_DATASET_IDS", "mymemory")
TOP_N = int(os.getenv("RETRIEVER_TOP_N", "8"))
SIMILARITY_THRESHOLD = float(os.getenv("RETRIEVER_SIMILARITY_THRESHOLD", "0.2"))
SESSIONS_DIR = Path.home() / ".claude" / "gateway-sessions"


# ── external 검색: stdlib urllib ──


def search_external(query: str, top_k: int) -> list[dict]:
    payload = json.dumps({
        "question": query,
        "dataset_ids": [DATASET_ID],
        "top_n": top_k,
    }).encode()

    req = urllib.request.Request(
        f"{BASE_URL}/api/v1/retrieval",
        data=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"검색 실패: {e}", file=sys.stderr)
        return []

    results = []
    for item in data.get("data", {}).get("chunks", []):
        results.append({
            "content": item.get("content", ""),
            "similarity": item.get("similarity", 0.0),
            "file_name": item.get("document_name", ""),
            "thread_id": item.get("document_id", "").replace("oldsessions_", ""),
        })
    return results


# ── local 검색: SQLite + numpy + sentence-transformers ──


def search_local(query: str, top_k: int) -> list[dict]:
    import numpy as np

    db_path = SESSIONS_DIR / "embeddings.db"
    if not db_path.exists():
        print("임베딩 DB가 없습니다. 세션이 인덱싱되지 않았습니다.", file=sys.stderr)
        return []

    # 모델 로드
    from sentence_transformers import SentenceTransformer
    model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    model = SentenceTransformer(model_name)

    # 쿼리 임베딩
    query_emb = model.encode(query, normalize_embeddings=True)

    # DB 검색
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT thread_id, file_name, chunk_idx, content, embedding FROM embeddings"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    results = []
    for thread_id, file_name, chunk_idx, content, emb_bytes in rows:
        emb = np.frombuffer(emb_bytes, dtype=np.float32)
        similarity = float(np.dot(query_emb, emb))
        if similarity >= SIMILARITY_THRESHOLD:
            results.append({
                "content": content,
                "similarity": similarity,
                "file_name": file_name,
                "thread_id": thread_id,
            })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


# ── 출력 ──


def print_results(query: str, results: list[dict]):
    print(f"\n🔍 검색어: {query}")
    print(f"📊 모드: {CLAUDE_GATEWAY_SESSION_MEMORY}")
    print("=" * 60)

    if not results:
        print("\n검색 결과가 없습니다.")
        return

    print(f"\n{len(results)}개 결과:\n")
    for i, r in enumerate(results, 1):
        print(f"[{i}] 유사도: {r['similarity']:.3f} | 파일: {r['file_name']}")
        if r.get("thread_id"):
            print(f"    스레드: {r['thread_id']}")
        print(f"    내용: {r['content'][:200]}...")
        print()


# ── main ──


def main():
    if CLAUDE_GATEWAY_SESSION_MEMORY == "none":
        print("CLAUDE_GATEWAY_SESSION_MEMORY=none — 세션 기억이 비활성화되어 있습니다.")
        sys.exit(0)

    if len(sys.argv) < 2:
        print("사용법: python search_sessions.py <검색어> [--top-k N]")
        sys.exit(1)

    query = sys.argv[1]
    top_k = TOP_N

    if "--top-k" in sys.argv:
        idx = sys.argv.index("--top-k")
        if idx + 1 < len(sys.argv):
            try:
                top_k = int(sys.argv[idx + 1])
            except ValueError:
                print("--top-k 값은 정수여야 합니다.", file=sys.stderr)
                sys.exit(1)

    if CLAUDE_GATEWAY_SESSION_MEMORY == "external":
        if not DATASET_ID:
            print("RAG_DATASET_IDS가 설정되지 않았습니다.", file=sys.stderr)
            sys.exit(1)
        results = search_external(query, top_k)
    elif CLAUDE_GATEWAY_SESSION_MEMORY == "local":
        results = search_local(query, top_k)
    else:
        print(f"알 수 없는 CLAUDE_GATEWAY_SESSION_MEMORY 값: {CLAUDE_GATEWAY_SESSION_MEMORY}")
        sys.exit(1)

    print_results(query, results)


if __name__ == "__main__":
    main()
