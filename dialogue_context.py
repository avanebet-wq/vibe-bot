"""Structured recent dialogue context for Liza."""
from __future__ import annotations

from collections import defaultdict, deque
import time
from threading import Lock
from typing import Optional

_MAX = 16
_LOCK = Lock()
_CONTEXT = defaultdict(lambda: deque(maxlen=_MAX))
_MAX_CHATS = 5000
_LAST_USED = {}

def record(chat_id, user_name: str, text: str, message_id=None,
           reply_to_user: Optional[str] = None, reply_to_message_id=None):
    if chat_id is None or not text:
        return
    item = {
        "speaker": (user_name or "Пользователь")[:80],
        "text": str(text).strip()[:2500],
        "message_id": message_id,
        "reply_to_user": (reply_to_user or "")[:80],
        "reply_to_message_id": reply_to_message_id,
    }
    with _LOCK:
        key = str(chat_id)
        _CONTEXT[key].append(item)
        _LAST_USED[key] = time.time()
        if len(_LAST_USED) > _MAX_CHATS:
            oldest = min(_LAST_USED, key=_LAST_USED.get)
            _LAST_USED.pop(oldest, None); _CONTEXT.pop(oldest, None)

def mark_liza(chat_id, text: str, message_id=None):
    record(chat_id, "Лиза", text, message_id=message_id)

def get(chat_id, limit=10):
    if chat_id is None:
        return []
    with _LOCK:
        rows = list(_CONTEXT.get(str(chat_id), ()))
    return rows[-max(1, min(int(limit), _MAX)):]

def format_for_ai(chat_id, limit=10):
    rows = get(chat_id, limit)
    result = []
    for row in rows:
        line = f'{row["speaker"]}: {row["text"]}'
        if row.get("reply_to_user"):
            line += f' [ответ пользователю: {row["reply_to_user"]}]'
        result.append({"role": "user", "content": line})
    return result
