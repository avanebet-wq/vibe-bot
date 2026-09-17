# -*- coding: utf-8 -*-
"""Persistent per-user facts with bounded storage and privacy-friendly controls.

Storage is scoped per "chat_id:user_id" row (see database.db_*_scoped).
Previously every read here (`get_facts`, called on *every* AI request via
`format_facts`) loaded a single JSON blob containing every user's facts in
every chat the bot is in, uncached ("user_memory" was in _NO_CACHE_KEYS).
That is now a single small cached row lookup for one user.
"""
import re
import time
from database import db_get_scoped, db_set_scoped, db_update_json_scoped

MAX_FACTS = 40
MIN_FACT_IMPORTANCE = 0

MAX_FACT_LEN = 240
_STOP = {"я", "мне", "меня", "мой", "моя", "это", "просто", "что", "как", "лиза"}

_EMPTY = {"facts": []}


def _key(chat_id, user_id): return f"{chat_id}:{user_id}"

def get_facts(chat_id, user_id, limit=12):
    row = db_get_scoped("user_memory", _key(chat_id, user_id), _EMPTY)
    return list(row.get("facts", []))[-max(1, int(limit)):]

def add_fact(chat_id, user_id, fact, source="conversation"):
    fact = re.sub(r"\s+", " ", str(fact or "")).strip()[:MAX_FACT_LEN]
    if len(fact) < 3: return False
    added = False
    def mutate(row):
        nonlocal added
        row = dict(row) if row else {"facts": []}
        facts = row.setdefault("facts", [])
        normalized = fact.casefold()
        if any(x.get("text", "").casefold() == normalized for x in facts): return row
        facts.append({"text": fact, "source": str(source)[:40], "at": time.time(), "importance": 1})
        row["facts"] = facts[-MAX_FACTS:]
        added = True
        return row
    db_update_json_scoped("user_memory", _key(chat_id, user_id), mutate, {"facts": []})
    return added

def clear(chat_id, user_id):
    db_set_scoped("user_memory", _key(chat_id, user_id), {"facts": []})

def forget_fact(chat_id, user_id, index):
    removed = False
    def mutate(row):
        nonlocal removed
        row = dict(row) if row else {"facts": []}
        facts = row.get("facts", [])
        try: facts.pop(int(index) - 1)
        except (ValueError, IndexError): return row
        row["facts"] = facts; removed = True
        return row
    db_update_json_scoped("user_memory", _key(chat_id, user_id), mutate, {"facts": []})
    return removed

def format_facts(chat_id, user_id):
    facts = get_facts(chat_id, user_id)
    if not facts: return ""
    return "Факты о собеседнике, используй только если это уместно и не утверждай их как абсолютную истину: " + "; ".join(x["text"] for x in facts)

def infer_safe_fact(text):
    """Extract only explicit, low-risk preference/hobby statements."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    patterns = [r"(?:я|мне)\s+(?:нравится|нравятся|люблю)\s+(.+)", r"(?:я|мне)\s+(?:не нравится|не люблю)\s+(.+)", r"мой любимый\s+(.+)"]
    for p in patterns:
        m = re.search(p, t, re.I)
        if m:
            value = m.group(1).strip(" .!?\n")
            if 2 <= len(value) <= 120:
                return "Предпочтение: " + value
    return None
