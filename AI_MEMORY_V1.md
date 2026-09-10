# Liza AI memory — v1

Implemented:
- bounded short-term conversation memory;
- per-chat memory isolation;
- automatic inclusion of recent messages in the AI context;
- automatic storage of successful user/assistant exchanges;
- `clear(chat_id)` and `trim(chat_id, max_messages)` helpers.

The memory is intentionally in RAM in v1. A later version can move it to SQLite
for persistence across restarts.
