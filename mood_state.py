# -*- coding: utf-8 -*-
"""Persistent per-chat mood/energy state with bounded decay."""
import time
from collections import defaultdict
from database import db_get, db_set
_LOCK=None
_DEFAULT={"mood":60,"energy":65,"irritation":10,"talkativeness":55,"updated":0.0}
_STATE=defaultdict(dict)

def _clamp(v): return max(0,min(100,int(v)))
def _load(chat_id):
    key=str(chat_id)
    if not _STATE[key]: _STATE[key]={**_DEFAULT, **db_get("mood_state",{}).get(key,{})}
    return _STATE[key]

def get(chat_id): return dict(_load(chat_id))
def _save(chat_id):
    st=_load(chat_id); store=db_get("mood_state",{}); store[str(chat_id)]=dict(st); db_set("mood_state",store)

def change(chat_id,*,mood=0,energy=0,irritation=0,talkativeness=0):
    st=_load(chat_id); st["mood"]=_clamp(st["mood"]+mood); st["energy"]=_clamp(st["energy"]+energy); st["irritation"]=_clamp(st["irritation"]+irritation); st["talkativeness"]=_clamp(st["talkativeness"]+talkativeness); st["updated"]=time.time(); _save(chat_id); return dict(st)

def on_message(chat_id,text="",*,is_direct=False,is_question=False):
    change(chat_id, energy=1 if is_direct else 0, irritation=1 if "!" in text and len(text)>80 else 0, mood=1 if is_question else 0, talkativeness=1 if is_direct else 0)

def on_event(chat_id,event_type):
    if event_type in ("member_join","reply","mention"): change(chat_id,mood=1,energy=1)
    elif event_type in ("moderation","silence","member_leave"): change(chat_id,mood=-1,irritation=2)

def decay(chat_id):
    st=_load(chat_id); now=time.time(); elapsed=now-float(st.get("updated",now) or now)
    if elapsed<300: return dict(st)
    steps=min(6,int(elapsed//300)); st["irritation"]=_clamp(st["irritation"]-steps*2); st["energy"]=_clamp(st["energy"]-steps); st["updated"]=now; _save(chat_id); return dict(st)

def build_prompt(chat_id):
    st=decay(chat_id); return f"Внутреннее состояние Лизы (не упоминай его): настроение {st['mood']}/100, энергия {st['energy']}/100, раздражение {st['irritation']}/100, разговорчивость {st['talkativeness']}/100."
