from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import numpy as np

from ..domain.requests import Memory


def chunk_id(user_id: str, request_id: str) -> str:
    value = hashlib.sha1(f"{user_id}\0{request_id}".encode("utf-8")).hexdigest()[:16]
    return value


def memory_id(chunk: str, kind: str, position: int) -> str:
    return f"{chunk}-{'r' if kind == 'raw' else 'f'}{position}"


class SQLiteMemoryStore:
    """One small SQLite database, with a transaction per completed Add request."""

    def __init__(self, path: str, dimensions: int, identity: dict[str, object]):
        self.path = path
        self.dimensions = dimensions
        self.identity = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._request_locks: dict[tuple[str, str], threading.Lock] = {}

    def open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memory (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT,
                    position INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    UNIQUE(user_id, id)
                );
                CREATE INDEX IF NOT EXISTS memory_user_seq ON memory(user_id, seq);
                CREATE TABLE IF NOT EXISTS request (
                    user_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    PRIMARY KEY(user_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            row = conn.execute("SELECT value FROM metadata WHERE key='embedding_identity'").fetchone()
            populated = conn.execute("SELECT 1 FROM memory LIMIT 1").fetchone() is not None
            if row and row[0] != self.identity:
                conn.close()
                raise RuntimeError("Embedding configuration differs from this database; use a new database file")
            if not row and populated:
                conn.close()
                raise RuntimeError("Database vectors have no recorded identity; use a new database file")
            if not row:
                conn.execute("INSERT INTO metadata(key,value) VALUES('embedding_identity',?)", (self.identity,))
                conn.commit()
            self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def request_lock(self, user_id: str, request_id: str) -> threading.Lock:
        with self._lock:
            return self._request_locks.setdefault((user_id, request_id), threading.Lock())

    def has_request(self, user_id: str, request_id: str) -> bool:
        with self._lock:
            return self._connection().execute(
                "SELECT 1 FROM request WHERE user_id=? AND request_id=?", (user_id, request_id)
            ).fetchone() is not None

    def add(self, user_id: str, request_id: str, memories: list[Memory]) -> bool:
        """Store every vector and the idempotency marker atomically."""
        if not memories:
            return True
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                if conn.execute("SELECT 1 FROM request WHERE user_id=? AND request_id=?",
                                (user_id, request_id)).fetchone():
                    conn.rollback()
                    return False
                conn.execute("INSERT INTO request(user_id,request_id) VALUES(?,?)", (user_id, request_id))
                for item in memories:
                    vector = np.asarray(item.vector, dtype=np.float32)
                    if vector.shape != (self.dimensions,) or not np.isfinite(vector).all():
                        raise ValueError("Invalid embedding vector")
                    if not np.linalg.norm(vector):
                        raise ValueError("Embedding vectors must be nonzero")
                    conn.execute(
                        "INSERT INTO memory(id,user_id,session_id,request_id,kind,chunk_id,content,created_at,position,vector) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (item.id, item.user_id, item.session_id, item.request_id, item.kind, item.chunk_id,
                         item.content, item.created_at, item.position, vector.tobytes()),
                    )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def for_user(self, user_id: str) -> list[Memory]:
        with self._lock:
            rows = self._connection().execute(
                "SELECT id,user_id,session_id,request_id,kind,chunk_id,content,created_at,position,vector "
                "FROM memory WHERE user_id=? ORDER BY seq", (user_id,)
            ).fetchall()
        memories = []
        for row in rows:
            vector = np.frombuffer(row[9], dtype=np.float32).copy()
            if vector.shape != (self.dimensions,) or not np.isfinite(vector).all() or not np.linalg.norm(vector):
                raise RuntimeError("Stored vector is invalid")
            memories.append(Memory(*row[:9], vector))
        return memories

    def stats(self) -> dict[str, int]:
        with self._lock:
            row = self._connection().execute("SELECT COUNT(DISTINCT user_id),COUNT(*) FROM memory").fetchone()
        return {"users": int(row[0]), "memories": int(row[1])}

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteMemoryStore.open() must be called first")
        return self._conn
