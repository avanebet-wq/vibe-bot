"""Lightweight event system for Liza.

Events are kept in bounded in-process state. They are intended to trigger
optional AI reactions later without changing moderation behavior.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

_MAX_EVENTS = 30
_EVENTS = defaultdict(lambda: deque(maxlen=_MAX_EVENTS))

def emit(chat_id, event_type: str, **data):
    if chat_id is None:
        return
    _EVENTS[str(chat_id)].append({
        "type": str(event_type),
        "time": time.time(),
        "data": data,
    })

def recent(chat_id, limit=10):
    rows = list(_EVENTS.get(str(chat_id), ()))
    return rows[-max(1, min(int(limit), _MAX_EVENTS)):]

def format_event(event):
    typ = event.get("type", "event")
    data = event.get("data", {})
    if typ == "member_join":
        return f'Пользователь {data.get("name", "кто-то")} вошёл в чат.'
    if typ == "member_leave":
        return f'Пользователь {data.get("name", "кто-то")} вышел из чата.'
    if typ == "mention":
        return f'Лизу упомянули: {data.get("text", "")}'
    if typ == "reply":
        return f'На сообщение Лизы ответили: {data.get("text", "")}'
    if typ == "moderation":
        return f'Модерация: {data.get("action", "действие")} для {data.get("name", "пользователя")}.'
    if typ == "silence":
        return "В чате долго нет сообщений."
    return f'Событие: {typ}.'

def recent_descriptions(chat_id, limit=5):
    return [format_event(x) for x in recent(chat_id, limit)]
