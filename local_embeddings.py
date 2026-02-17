"""
Local Embedding System for Session Memory
==========================================
외부 RAG 시스템이 없을 때 사용하는 내장 임베딩 시스템.
- sentence-transformers로 경량 임베딩 모델 사용
- SQLite로 벡터 저장 및 유사도 검색
- 세션 대화 자동 인덱싱
"""

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime

import numpy as np

log = logging.getLogger("local_embeddings")


@dataclass
class EmbeddingConfig:
    """내장 임베딩 시스템 설정"""
    db_path: Path = Path("./sessions/embeddings.db")
    model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 8
    similarity_threshold: float = 0.2


class LocalEmbeddings:
    """SQLite + sentence-transformers 기반 로컬 임베딩 시스템"""

    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self._model = None
        self._db_conn = None
        self._init_lock = asyncio.Lock()
        log.info("LocalEmbeddings 초기화 (모델=%s)", config.model_name)

    async def _ensure_initialized(self):
        """모델과 DB를 lazy 초기화 (최초 사용 시)"""
        async with self._init_lock:
            if self._model is None:
                await self._load_model()
            if self._db_conn is None:
                await self._init_db()

    async def _load_model(self):
        """임베딩 모델 로드 (블로킹 작업을 별도 스레드에서 실행)"""
        log.info("임베딩 모델 로딩 중... (최초 1회, 시간 소요 가능)")
        loop = asyncio.get_event_loop()

        def _load():
            from sentence_transformers import SentenceTransformer
            return SentenceTransformer(self.config.model_name)

        self._model = await loop.run_in_executor(None, _load)
        log.info("임베딩 모델 로드 완료")

    async def _init_db(self):
        """SQLite DB 초기화"""
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_event_loop()

        def _init():
            conn = sqlite3.connect(str(self.config.db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    chunk_idx INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    timestamp TEXT NOT NULL,
                    UNIQUE(thread_id, chunk_idx)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_thread_id ON embeddings(thread_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_file_name ON embeddings(file_name)
            """)
            conn.commit()
            return conn

        self._db_conn = await loop.run_in_executor(None, _init)
        log.info("SQLite DB 초기화 완료: %s", self.config.db_path)

    async def close(self):
        """리소스 정리"""
        if self._db_conn:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._db_conn.close)
            self._db_conn = None
        self._model = None

    # ── 텍스트 청킹 ──

    def _chunk_text(self, text: str) -> List[str]:
        """텍스트를 chunk_size 기준으로 분할 (오버랩 포함)"""
        chunks = []
        chunk_size = self.config.chunk_size
        overlap = self.config.chunk_overlap

        if len(text) <= chunk_size:
            return [text]

        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk = text[start:end]
            chunks.append(chunk)
            start += chunk_size - overlap

        return chunks

    # ── 임베딩 생성 ──

    async def _embed_text(self, text: str) -> np.ndarray:
        """텍스트를 벡터로 변환"""
        await self._ensure_initialized()
        loop = asyncio.get_event_loop()
        embedding = await loop.run_in_executor(
            None, lambda: self._model.encode(text, normalize_embeddings=True)
        )
        return embedding

    async def _embed_batch(self, texts: List[str]) -> List[np.ndarray]:
        """여러 텍스트를 배치로 임베딩"""
        await self._ensure_initialized()
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: self._model.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                normalize_embeddings=True,
            ),
        )
        return [emb for emb in embeddings]

    # ── 인덱싱 (파일 → 청크 → 임베딩 → DB 저장) ──

    async def index_file(self, file_path: Path):
        """세션 파일을 읽어서 청크로 나누고 임베딩하여 DB에 저장"""
        try:
            await self._ensure_initialized()

            # 절대 경로로 변환
            file_path_abs = file_path.resolve()
            log.info("파일 인덱싱 시작: %s (존재: %s)", file_path_abs, file_path_abs.exists())

            if not file_path_abs.exists():
                log.error("파일이 존재하지 않음: %s", file_path_abs)
                return

            thread_id = file_path_abs.stem  # 파일명이 thread_id
            log.info("스레드 ID: %s", thread_id)

            content = file_path_abs.read_text(encoding="utf-8")
            log.info("파일 읽기 완료: %d자", len(content))

            if not content.strip():
                log.info("빈 파일 건너뜀: %s", file_path_abs.name)
                return

            # 텍스트 청킹
            chunks = self._chunk_text(content)
            log.info("파일 청킹 완료: %s → %d개 청크", file_path_abs.name, len(chunks))

            # 배치 임베딩
            embeddings = await self._embed_batch(chunks)
            log.info("임베딩 생성 완료: %d개", len(embeddings))

            # DB에 저장
            loop = asyncio.get_event_loop()

            def _save():
                # 기존 데이터 삭제 (갱신)
                self._db_conn.execute("DELETE FROM embeddings WHERE thread_id = ?", (thread_id,))

                # 새 데이터 삽입
                timestamp = datetime.now().isoformat()
                for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                    emb_bytes = emb.astype(np.float32).tobytes()
                    self._db_conn.execute(
                        """
                        INSERT OR REPLACE INTO embeddings
                        (thread_id, file_name, chunk_idx, content, embedding, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (thread_id, file_path_abs.name, idx, chunk, emb_bytes, timestamp),
                    )
                self._db_conn.commit()

            await loop.run_in_executor(None, _save)
            log.info("DB 저장 완료: %s (%d개 청크)", file_path_abs.name, len(chunks))
        except Exception as e:
            log.error("index_file 실패 (%s): %s", file_path, e, exc_info=True)
            raise

    # ── 검색 (유사도 기반) ──

    async def search(self, query: str, top_k: Optional[int] = None) -> List[dict]:
        """쿼리 텍스트와 유사한 청크 검색"""
        await self._ensure_initialized()

        if top_k is None:
            top_k = self.config.top_k

        # 쿼리 임베딩
        query_emb = await self._embed_text(query)

        # DB에서 모든 임베딩 가져오기
        loop = asyncio.get_event_loop()

        def _fetch_all():
            cursor = self._db_conn.execute(
                """
                SELECT id, thread_id, file_name, chunk_idx, content, embedding
                FROM embeddings
                """
            )
            return cursor.fetchall()

        rows = await loop.run_in_executor(None, _fetch_all)

        if not rows:
            log.info("검색 결과 없음 (DB 비어있음)")
            return []

        # 코사인 유사도 계산
        similarities = []
        for row in rows:
            row_id, thread_id, file_name, chunk_idx, content, emb_bytes = row
            emb = np.frombuffer(emb_bytes, dtype=np.float32)

            # 코사인 유사도 (이미 정규화되어 있으므로 내적만 계산)
            similarity = float(np.dot(query_emb, emb))

            if similarity >= self.config.similarity_threshold:
                similarities.append({
                    "id": row_id,
                    "thread_id": thread_id,
                    "file_name": file_name,
                    "chunk_idx": chunk_idx,
                    "content": content,
                    "similarity": similarity,
                })

        # 유사도 높은 순으로 정렬하여 top_k 반환
        similarities.sort(key=lambda x: x["similarity"], reverse=True)
        results = similarities[:top_k]

        log.info("검색 완료: 쿼리='%s' → %d개 결과", query[:50], len(results))
        return results

    # ── 상태 조회 ──

    async def get_status(self) -> dict:
        """DB 통계 반환"""
        if self._db_conn is None:
            return {
                "total_chunks": 0,
                "total_threads": 0,
                "db_path": str(self.config.db_path),
                "model": self.config.model_name,
                "initialized": False,
            }

        loop = asyncio.get_event_loop()

        def _stats():
            cursor = self._db_conn.execute("SELECT COUNT(*) FROM embeddings")
            total_chunks = cursor.fetchone()[0]

            cursor = self._db_conn.execute("SELECT COUNT(DISTINCT thread_id) FROM embeddings")
            total_threads = cursor.fetchone()[0]

            return total_chunks, total_threads

        total_chunks, total_threads = await loop.run_in_executor(None, _stats)

        return {
            "total_chunks": total_chunks,
            "total_threads": total_threads,
            "db_path": str(self.config.db_path),
            "model": self.config.model_name,
            "initialized": True,
        }
