"""Persistent short-term conversation memory for Liza.

Stores only a bounded number of recent messages per chat in SQLite.
The module is intentionally independent from the AI provider so it can be
used by handlers and the AI layer without coupling either to the database
implementation.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional


class PersistentConversationMemory:
    def __init__(self, db_path: str = "liza_memory.sqlite3", max_messages: int = 20):
        self.db_path = str(Path(db_path))
        self.max_messages = max(2, int(max_messages))
        self._lock = Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    def add(self, chat_id, role: str, content: str):
        if chat_id is None or not content:
            return
        role = str(role)
        if role not in ("user", "assistant", "system"):
            return
        content = str(content).strip()[:4000]
        if not content:
            return

        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO conversation_memory(chat_id, role, content) VALUES (?, ?, ?)",
                (str(chat_id), role, content),
            )
            conn.execute(
                """
                DELETE FROM conversation_memory
                WHERE chat_id = ?
                  AND id NOT IN (
                    SELECT id FROM conversation_memory
                    WHERE chat_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                  )
                """,
                (str(chat_id), str(chat_id), self.max_messages),
            )

    def get(self, chat_id, limit: Optional[int] = None) -> List[Dict[str, str]]:
        if chat_id is None:
            return []
        limit = max(1, min(int(limit or self.max_messages), self.max_messages))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content
                FROM conversation_memory
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(chat_id), limit),
            ).fetchall()
        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]

    def clear(self, chat_id):
        if chat_id is None:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM conversation_memory WHERE chat_id = ?",
                (str(chat_id),),
            )

    def count(self, chat_id) -> int:
        if chat_id is None:
            return 0
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM conversation_memory WHERE chat_id = ?",
                (str(chat_id),),
            ).fetchone()
        return int(row[0] or 0)


conversation_memory = PersistentConversationMemory()
