"""Persistent short-term conversation memory stored in Railway PostgreSQL."""
from __future__ import annotations

from threading import Lock
from typing import Dict, List, Optional

from database import conn, db_lock


class PersistentConversationMemory:
    def __init__(self, db_path: str = "liza_memory.sqlite3", max_messages: int = 20):
        # db_path is kept for API compatibility; storage is now PostgreSQL.
        self.max_messages = max(2, int(max_messages))
        self._lock = Lock()
        self._init_db()

    def _init_db(self):
        with self._lock, db_lock:
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
            conn.execute(
                "INSERT INTO conversation_memory(chat_id, role, content) VALUES (%s, %s, %s)",
                (str(chat_id), role, content),
            )
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
                (str(chat_id), str(chat_id), self.max_messages),
            )
            conn.commit()

    def get(self, chat_id, limit: Optional[int] = None) -> List[Dict[str, str]]:
        if chat_id is None:
            return []
        limit = max(1, min(int(limit or self.max_messages), self.max_messages))
        with self._lock, db_lock:
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
        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]

    def clear(self, chat_id):
        if chat_id is None:
            return
        with self._lock, db_lock:
            conn.execute("DELETE FROM conversation_memory WHERE chat_id = %s", (str(chat_id),))
            conn.commit()

    def count(self, chat_id) -> int:
        if chat_id is None:
            return 0
        with self._lock, db_lock:
            row = conn.execute(
                "SELECT COUNT(*) FROM conversation_memory WHERE chat_id = %s",
                (str(chat_id),),
            ).fetchone()
        return int(row[0] or 0)


conversation_memory = PersistentConversationMemory()
