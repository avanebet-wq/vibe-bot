"""Recent Telegram group context for Liza.

A lightweight adapter around the persistent conversation memory. It records
speaker labels in the content so the model can distinguish participants.
"""
from __future__ import annotations

from conversation_memory import conversation_memory


def record_group_message(chat_id, display_name: str, text: str):
    if not text or not str(text).strip():
        return
    name = (display_name or "Пользователь").strip()[:80]
    content = str(text).strip()[:3500]
    conversation_memory.add(chat_id, "user", f"{name}: {content}")


def get_group_context(chat_id, limit: int = 12):
    return conversation_memory.get(chat_id, limit=limit)


def clear_group_context(chat_id):
    conversation_memory.clear(chat_id)

# updated 2026-09-18
