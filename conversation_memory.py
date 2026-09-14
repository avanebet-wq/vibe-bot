"""Persistent short-term conversation memory stored in Railway PostgreSQL."""
from __future__ import annotations

from threading import Lock
from typing import Dict, List, Optional

from database import conn, db_lock


class PersistentConversationMemory:
    def __init__(self, db_path: str | None = None, max_messages: int = 20):
        # db_path is ignored; storage is PostgreSQL. Kept only for compatibility.
        self.max_messages = max(2, int(max_messages))
        self._lock = Lock()
        self._cleanup_counter = {}
        self._init_db()

    def _init_db(self):
        with self._lock, db_lock:
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_memory (
                        id BIGSERIAL PRIMARY KEY,
                        chat_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_conversation_memory_chat_id
                    ON conversation_memory(chat_id, id)
                    """
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def add(self, chat_id, role: str, content: str):
        if chat_id is None or not content:
            return
        role = str(role)
        if role not in ("user", "assistant", "system"):
            return
        content = str(content).strip()[:4000]
        if not content:
            return

        with self._lock, db_lock:
            try:
                conn.execute(
                    "INSERT INTO conversation_memory(chat_id, role, content) VALUES (%s, %s, %s)",
                    (str(chat_id), role, content),
                )
                key = str(chat_id)
                self._cleanup_counter[key] = self._cleanup_counter.get(key, 0) + 1
                if self._cleanup_counter[key] >= 20:
                    conn.execute(
                        """
                        DELETE FROM conversation_memory
                        WHERE chat_id = %s
                          AND id NOT IN (
                            SELECT id FROM conversation_memory
                            WHERE chat_id = %s
                            ORDER BY id DESC
                            LIMIT %s
                          )
                        """,
                        (key, key, self.max_messages),
                    )
                    self._cleanup_counter[key] = 0
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get(self, chat_id, limit: Optional[int] = None) -> List[Dict[str, str]]:
        if chat_id is None:
            return []
        limit = max(1, min(int(limit or self.max_messages), self.max_messages))
        with self._lock, db_lock:
            try:
                rows = conn.execute(
                    """
                    SELECT role, content
                    FROM conversation_memory
                    WHERE chat_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (str(chat_id), limit),
                ).fetchall()
            except Exception:
                conn.rollback()
                raise
        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]

    def clear(self, chat_id):
        if chat_id is None:
            return
        with self._lock, db_lock:
            try:
                conn.execute("DELETE FROM conversation_memory WHERE chat_id = %s", (str(chat_id),))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def count(self, chat_id) -> int:
        if chat_id is None:
            return 0
        with self._lock, db_lock:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM conversation_memory WHERE chat_id = %s",
                    (str(chat_id),),
                ).fetchone()
            except Exception:
                conn.rollback()
                raise
        return int(row[0] or 0)


conversation_memory = PersistentConversationMemory()
