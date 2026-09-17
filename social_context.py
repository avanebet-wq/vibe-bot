# -*- coding: utf-8 -*-
"""Conversation-only interaction graph, bounded and non-sensitive.

Persistence is scoped per chat_id (see database.db_*_scoped), so persisting
one chat's graph never locks or rewrites any other chat's data, unlike the
old single shared "social_context" blob.
"""
import time
import threading
from collections import defaultdict, deque, Counter
from database import db_get_scoped, db_update_json_scoped
_LOCK=threading.RLock()
_STATE=defaultdict(lambda:{"edges":Counter(),"recent":deque(maxlen=60)})
_LAST_USED={}
_STATE_TTL=3600.0

def _persist(chat_id):
    with _LOCK:
        s=_STATE[str(chat_id)]
        snapshot={"edges":{f"{a}:{b}":n for (a,b),n in s["edges"].items()}}
    def mutate(_ignored): return snapshot
    db_update_json_scoped("social_context", str(chat_id), mutate, {})

def _load(chat_id):
    key=str(chat_id)
    with _LOCK:
        if _STATE[key]["edges"]: return
        row=db_get_scoped("social_context", key, {})
        for edge,n in row.get("edges",{}).items():
            try: a,b=edge.split(":",1); _STATE[key]["edges"][(a,b)]=int(n)
            except ValueError: pass

def _cleanup(now=None):
    now=time.time() if now is None else now
    cutoff=now-_STATE_TTL
    with _LOCK:
        for key,ts in list(_LAST_USED.items()):
            if ts < cutoff:
                _LAST_USED.pop(key,None); _STATE.pop(key,None)

def observe(chat_id,user_id,reply_to_user_id=None):
    now=time.time(); key=str(chat_id); _cleanup(now); _load(key)
    with _LOCK:
        s=_STATE[key]; s["recent"].append((str(user_id),now)); _LAST_USED[key]=now
        should_persist=False
        if reply_to_user_id and str(reply_to_user_id)!=str(user_id):
            s["edges"][(str(user_id),str(reply_to_user_id))]+=1
            should_persist=(sum(s["edges"].values())%10==0)
    if should_persist: _persist(key)

def summary(chat_id,user_id=None):
    _cleanup(); _load(chat_id)
    with _LOCK:
        s=_STATE[str(chat_id)]
        recent_count=len({u for u,_ in s["recent"]})
        interactions=sum(s["edges"].values())
        outgoing=[(b,n) for (a,b),n in s["edges"].items() if user_id is not None and a==str(user_id)]
    if user_id is None:
        return {"recent_speakers":recent_count,"interactions":interactions}
    return sorted(outgoing,key=lambda x:-x[1])[:5]
