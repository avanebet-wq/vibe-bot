# -*- coding: utf-8 -*-
"""Per-user karma and lightweight communication-style memory."""
from __future__ import annotations

import re
import time
from database import db_get, db_update_json

_MAX_KARMA = 100
_MIN_KARMA = -100
_MAX_SAMPLES = 12

_POSITIVE = (
    "спасибо", "поддерж", "держись", "молодец", "умно", "умный совет",
    "хороший совет", "советую", "подскажу", "подскажи", "помогу", "помоги",
    "респект", "круто", "класс", "молодчина", "солидарен", "солидарна",
    "согласен", "согласна", "правильно", "верно", "не переживай", "всё будет",
)
_NEGATIVE = (
    "идиот", "дебил", "тупой", "тупая", "кретин", "мудак", "мразь", "чмо",
    "лох", "урод", "заткнись", "пошёл нах", "пошел нах", "нахуй", "нахер",
    "достал", "достала", "ненавижу тебя", "угрож", "оскорб",
)


def _key(chat_id):
    return str(chat_id)


def _ensure_user(chat, uid, first_name=None):
    users = chat.setdefault("liza_users", {})
    user = users.setdefault(str(uid), {
        "requests": 0,
        "replies": 0,
        "karma": 0,
        "karma_events": [],
        "samples": [],
        "style": {},
    })
    user.setdefault("requests", 0)
    user.setdefault("replies", 0)
    user.setdefault("karma", 0)
    user.setdefault("karma_events", [])
    user.setdefault("samples", [])
    user.setdefault("style", {})
    if first_name:
        user["first_name"] = str(first_name)[:80]
    return user


def _style_from_samples(samples):
    texts = [str(x.get("text", "")) for x in samples if isinstance(x, dict) and x.get("text")]
    if not texts:
        return {}
    joined = " ".join(texts)
    words = joined.split()
    avg_len = sum(len(x) for x in texts) / len(texts)
    emoji_count = len(re.findall(r"[^\w\s,.!?;:'\"()\-–—]+", joined, flags=re.UNICODE))
    exclaims = joined.count("!")
    questions = joined.count("?")
    lower_words = sum(1 for x in words if x == x.lower())
    caps_words = sum(1 for x in words if len(x) >= 3 and x.isupper())
    slang_markers = sum(joined.lower().count(x) for x in ("лол", "ахах", "хаха", "жиза", "имба", "кек", "бро", "брат", "чел"))
    return {
        "avg_message_chars": round(avg_len),
        "emoji_level": "высокий" if emoji_count >= max(4, len(texts)) else "обычный",
        "exclamation_level": "высокий" if exclaims >= len(texts) else "обычный",
        "question_level": "высокий" if questions >= len(texts) else "обычный",
        "lowercase_style": lower_words >= max(3, int(len(words) * 0.7)),
        "caps_style": caps_words >= 2,
        "slang_level": "высокий" if slang_markers >= 2 else "обычный",
    }


def observe_message(chat_id, user_id, text, first_name=None):
    """Persist a small per-user dialogue sample and communication style."""
    if chat_id is None or user_id is None or not str(text).strip():
        return
    clean = str(text).strip()[:1200]

    def mutate(store):
        chat = store.setdefault(_key(chat_id), {})
        user = _ensure_user(chat, user_id, first_name)
        samples = user["samples"]
        samples.append({"text": clean, "at": time.time()})
        if len(samples) > _MAX_SAMPLES:
            del samples[:-_MAX_SAMPLES]
        user["style"] = _style_from_samples(samples)
        return store

    db_update_json("stats", mutate, {})


def get_user_context(chat_id, user_id):
    if chat_id is None or user_id is None:
        return None
    store = db_get("stats", {}) or {}
    chat = store.get(_key(chat_id), {}) or {}
    user = (chat.get("liza_users", {}) or {}).get(str(user_id), {}) or {}
    samples = user.get("samples", []) or []
    style = user.get("style", {}) or {}
    lines = []
    if style:
        lines.append("Манера текущего собеседника: " + ", ".join(f"{k}={v}" for k, v in style.items()))
    if samples:
        lines.append("Примеры его сообщений (ориентир для естественной манеры, не цитируй дословно):")
        for item in samples[-8:]:
            lines.append("- " + str(item.get("text", ""))[:500])
    return "\n".join(lines)[:5000] if lines else None


def get_karma(chat_id, user_id):
    store = db_get("stats", {}) or {}
    user = (((store.get(_key(chat_id), {}) or {}).get("liza_users", {}) or {}).get(str(user_id), {}) or {})
    try:
        return int(user.get("karma", 0))
    except Exception:
        return 0


def change_karma(chat_id, target_user_id, delta, reason="", actor_user_id=None):
    """Atomically change karma and return (old, new)."""
    delta = int(delta)
    if delta == 0:
        return get_karma(chat_id, target_user_id), get_karma(chat_id, target_user_id)
    delta = max(-10, min(10, delta))

    result = {"old": 0, "new": 0}

    def mutate(store):
        chat = store.setdefault(_key(chat_id), {})
        user = _ensure_user(chat, target_user_id)
        old = int(user.get("karma", 0) or 0)
        new = max(_MIN_KARMA, min(_MAX_KARMA, old + delta))
        user["karma"] = new
        if new != old:
            events = user["karma_events"]
            events.append({
                "delta": new - old,
                "reason": str(reason or "")[:200],
                "actor_id": str(actor_user_id) if actor_user_id is not None else None,
                "at": time.time(),
            })
            if len(events) > 30:
                del events[:-30]
        result["old"], result["new"] = old, new
        return store

    db_update_json("stats", mutate, {})
    return result["old"], result["new"]


def auto_delta(text):
    """Return a conservative automatic karma delta and a human-readable reason."""
    low = str(text or "").lower()
    if not low.strip():
        return 0, ""
    if any(marker in low for marker in _NEGATIVE):
        return -1, "за отвратительное поведение"
    if any(marker in low for marker in _POSITIVE):
        return 1, "за поддержку или полезный совет"
    return 0, ""
