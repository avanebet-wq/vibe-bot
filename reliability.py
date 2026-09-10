# -*- coding: utf-8 -*-
"""Production health state, graceful stop and guarded background loops."""
import logging, threading, time
_HEALTH={"started_at":time.time(),"last_poll_ok":0.0,"errors":0,"last_error":None,"background":{}}
_STOP=threading.Event()
_LOCK=threading.RLock()

def mark_ok():
    with _LOCK: _HEALTH["last_poll_ok"]=time.time()

def mark_error(exc=None):
    with _LOCK:
        _HEALTH["errors"]+=1; _HEALTH["last_error"]=str(exc)[:500] if exc else "unknown"

def mark_background(name, ok=True, error=None):
    with _LOCK: _HEALTH["background"][name]={"ok":bool(ok),"at":time.time(),"error":str(error)[:300] if error else None}

def health():
    with _LOCK:
        x=dict(_HEALTH); x["background"]=dict(_HEALTH["background"]); return x

def stop(): _STOP.set()
def stopped(): return _STOP.is_set()

def safe_loop(name, fn, interval=30):
    def run():
        while not _STOP.is_set():
            try:
                fn(); mark_background(name, True)
            except Exception as exc:
                mark_error(exc); mark_background(name, False, exc); logging.exception("[%s] background loop failed", name)
            _STOP.wait(interval)
    t=threading.Thread(target=run, daemon=True, name=name); t.start(); return t
