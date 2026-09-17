# -*- coding: utf-8 -*-
"""In-process abuse/rate protection with bounded memory."""
import time
from collections import defaultdict, deque

_EVENTS=defaultdict(lambda: deque(maxlen=40))
_LAST_CLEAN=0.0
_TTL=900.0
_LAST_SEEN={}

def allow(key, limit=12, window=20):
    global _LAST_CLEAN
    now=time.time(); skey=str(key); q=_EVENTS[skey]
    while q and now-q[0] > window: q.popleft()
    if len(q)>=limit:
        _LAST_SEEN[skey]=now
        return False
    q.append(now); _LAST_SEEN[skey]=now
    if now-_LAST_CLEAN>300 and len(_EVENTS)>5000:
        cutoff=now-_TTL
        for k,seen in list(_LAST_SEEN.items()):
            if seen < cutoff:
                _LAST_SEEN.pop(k,None); _EVENTS.pop(k,None)
        _LAST_CLEAN=now
    return True

# updated 2026-09-18
