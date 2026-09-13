# -*- coding: utf-8 -*-
"""Conversation-only interaction graph, bounded and non-sensitive."""
import time
from collections import defaultdict, deque, Counter
from database import db_get, db_set, db_update_json
_STATE=defaultdict(lambda:{"edges":Counter(),"recent":deque(maxlen=60)})

def _persist(chat_id):
    s=_STATE[str(chat_id)]
    snapshot={"edges":{f"{a}:{b}":n for (a,b),n in s["edges"].items()}}
    def mutate(store):
        store[str(chat_id)] = snapshot
        return store
    db_update_json("social_context", mutate, {})

def _load(chat_id):
    key=str(chat_id)
    if _STATE[key]["edges"]: return
    row=db_get("social_context",{}).get(key,{})
    for edge,n in row.get("edges",{}).items():
        try: a,b=edge.split(":",1); _STATE[key]["edges"][(a,b)]=int(n)
        except ValueError: pass

def observe(chat_id,user_id,reply_to_user_id=None):
    key=str(chat_id); _load(key); s=_STATE[key]; s["recent"].append((str(user_id),time.time()))
    if reply_to_user_id and str(reply_to_user_id)!=str(user_id):
        s["edges"][(str(user_id),str(reply_to_user_id))]+=1
        if sum(s["edges"].values())%10==0: _persist(key)

def summary(chat_id,user_id=None):
    _load(chat_id); s=_STATE[str(chat_id)]
    if user_id is None: return {"recent_speakers":len({u for u,_ in s["recent"]}),"interactions":sum(s["edges"].values())}
    outgoing=[(b,n) for (a,b),n in s["edges"].items() if a==str(user_id)]
    return sorted(outgoing,key=lambda x:-x[1])[:5]
