# -*- coding: utf-8 -*-
"""Persistent per-chat mood/energy state with bounded decay."""
import time
import threading
from collections import defaultdict
from database import db_get, db_set, db_update_json
_LOCK=threading.RLock()
_DEFAULT={"mood":60,"energy":65,"irritation":10,"talkativeness":55,"updated":0.0}
_STATE=defaultdict(dict)
_STATE_LAST_USED={}
_STATE_TTL=86400.0

def _clamp(v): return max(0,min(100,int(v)))
def _load(chat_id):
    key=str(chat_id)
    _STATE_LAST_USED[key]=time.time()
    if len(_STATE_LAST_USED)>5000:
        cutoff=time.time()-_STATE_TTL
        for k,ts in list(_STATE_LAST_USED.items()):
            if ts<cutoff:
                _STATE_LAST_USED.pop(k,None); _STATE.pop(k,None)
    if not _STATE[key]: _STATE[key]={**_DEFAULT, **db_get("mood_state",{}).get(key,{})}
    return _STATE[key]

def get(chat_id): return dict(_load(chat_id))
def _save(chat_id):
    st=_load(chat_id)
    def mutate(store):
        store[str(chat_id)] = dict(st)
        return store
    db_update_json("mood_state", mutate, {})

def change(chat_id,*,mood=0,energy=0,irritation=0,talkativeness=0):
    key=str(chat_id); now=time.time(); result={}
    with _LOCK:
        st=_load(chat_id)
        with_updates={
            "mood":_clamp(st.get("mood",60)+mood),
            "energy":_clamp(st.get("energy",65)+energy),
            "irritation":_clamp(st.get("irritation",10)+irritation),
            "talkativeness":_clamp(st.get("talkativeness",55)+talkativeness),
            "updated":now,
        }
        def mutate(store):
            current={**_DEFAULT, **store.get(key,{})}
            current.update(with_updates)
            store[key]=dict(current); result.update(current); return store
        db_update_json("mood_state", mutate, {})
        _STATE[key]=dict(result)
    return dict(result)

def on_message(chat_id,text="",*,is_direct=False,is_question=False):
    change(chat_id, energy=1 if is_direct else 0, irritation=1 if "!" in text and len(text)>80 else 0, mood=1 if is_question else 0, talkativeness=1 if is_direct else 0)

def on_event(chat_id,event_type):
    if event_type in ("member_join","reply","mention"): change(chat_id,mood=1,energy=1)
    elif event_type in ("moderation","silence","member_leave"): change(chat_id,mood=-1,irritation=2)

def decay(chat_id):
    key=str(chat_id); now=time.time(); result={}
    with _LOCK:
        def mutate(store):
            current={**_DEFAULT, **store.get(key,{})}
            elapsed=now-float(current.get("updated",now) or now)
            if elapsed<300:
                result.update(current); return store
            steps=min(6,int(elapsed//300))
            current["irritation"]=_clamp(current["irritation"]-steps*2)
            current["energy"]=_clamp(current["energy"]-steps)
            current["updated"]=now
            store[key]=dict(current); result.update(current); return store
        db_update_json("mood_state", mutate, {})
        _STATE[key]=dict(result)
    return dict(result)

def build_prompt(chat_id):
    st=decay(chat_id); return f"Внутреннее состояние Лизы (не упоминай его): настроение {st['mood']}/100, энергия {st['energy']}/100, раздражение {st['irritation']}/100, разговорчивость {st['talkativeness']}/100."
