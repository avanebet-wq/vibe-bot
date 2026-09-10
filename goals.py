# -*- coding: utf-8 -*-
"""Persistent lightweight goals/tasks for Liza."""
import time
import uuid
from database import db_get, db_set

MAX_GOALS = 100

def _store(): return db_get("goals", {})
def _save(s): db_set("goals", s)

def _bucket(chat_id):
    s = _store(); b = s.setdefault(str(chat_id), []); return s, b

def add(chat_id, title, owner_id=None, due_at=None, note=""):
    title = " ".join(str(title or "").split())[:240]
    if not title: return None
    s, b = _bucket(chat_id)
    if len(b) >= MAX_GOALS: b[:] = b[-MAX_GOALS+1:]
    item = {"id": uuid.uuid4().hex[:10], "title": title, "owner_id": owner_id,
            "due_at": float(due_at) if due_at else None, "note": str(note or "")[:240],
            "status": "open", "notified_at": None, "created_at": time.time(), "updated_at": time.time()}
    b.append(item); _save(s); return item

def list_open(chat_id):
    s = _store(); return [x for x in s.get(str(chat_id), []) if x.get("status") == "open"]

def complete(chat_id, goal_id):
    s, b = _bucket(chat_id)
    for x in b:
        if x.get("id") == str(goal_id): x["status"] = "done"; x["updated_at"] = time.time(); _save(s); return True
    return False

def remove(chat_id, goal_id):
    s, b = _bucket(chat_id); old = len(b); b[:] = [x for x in b if x.get("id") != str(goal_id)]
    if len(b) != old: _save(s); return True
    return False

def due(chat_id, now=None, limit=10):
    now = now or time.time()
    return [x for x in list_open(chat_id) if x.get("due_at") and x["due_at"] <= now and not x.get("notified_at")][:limit]

def mark_notified(chat_id, goal_id):
    s,b=_bucket(chat_id)
    for x in b:
        if x.get("id")==str(goal_id): x["notified_at"]=time.time(); x["updated_at"]=time.time(); _save(s); return True
    return False
