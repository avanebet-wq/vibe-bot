# Liza persistent conversation memory — v2

- Recent conversation messages are persisted in SQLite.
- Memory is isolated by Telegram chat ID.
- Maximum memory is bounded (20 messages by default).
- Messages are trimmed automatically after insertion.
- Memory survives Railway process restarts.
- `conversation_memory.py` exposes `get`, `add`, `clear`, and `count`.
- The AI layer accepts an optional `chat_id` argument for context.

This version intentionally keeps the feature separate from the existing database
logic to minimize regressions. A later refactor can migrate this table into the
main application database.
