# -*- coding: utf-8 -*-
"""Persistent per-user facts with bounded storage and privacy-friendly controls."""
import re
import time
from database import db_get, db_set, db_update_json

MAX_FACTS = 40
MIN_FACT_IMPORTANCE = 0

MAX_FACT_LEN = 240
_STOP = {"я", "мне", "меня", "мой", "моя", "это", "просто", "что", "как", "лиза"}


def _store(): return db_get("user_memory", {})

def _save(s): db_set("user_memory", s)

def _key(chat_id, user_id): return f"{chat_id}:{user_id}"

def get_facts(chat_id, user_id, limit=12):
    row = _store().get(_key(chat_id, user_id), {})
    return list(row.get("facts", []))[-max(1, int(limit)):]

def add_fact(chat_id, user_id, fact, source="conversation"):
    fact = re.sub(r"\s+", " ", str(fact or "")).strip()[:MAX_FACT_LEN]
    if len(fact) < 3: return False
    added = False
    def mutate(s):
        nonlocal added
        key = _key(chat_id, user_id); row = s.setdefault(key, {"facts": []})
        facts = row.setdefault("facts", [])
        normalized = fact.casefold()
        if any(x.get("text", "").casefold() == normalized for x in facts): return s
        facts.append({"text": fact, "source": str(source)[:40], "at": time.time(), "importance": 1})
        row["facts"] = facts[-MAX_FACTS:]
        added = True
        return s
    db_update_json("user_memory", mutate, {})
    return added

def clear(chat_id, user_id):
    def mutate(s):
        s.pop(_key(chat_id, user_id), None)
        return s
    db_update_json("user_memory", mutate, {})

def forget_fact(chat_id, user_id, index):
    removed = False
    def mutate(s):
        nonlocal removed
        key = _key(chat_id, user_id); row = s.get(key)
        if not row: return s
        facts = row.get("facts", [])
        try: facts.pop(int(index)-1)
        except (ValueError, IndexError): return s
        row["facts"] = facts; removed = True
        return s
    db_update_json("user_memory", mutate, {})
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
