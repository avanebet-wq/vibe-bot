# -*- coding: utf-8 -*-
"""In-process abuse/rate protection with bounded memory."""
import time
from collections import defaultdict, deque

_EVENTS=defaultdict(lambda: deque(maxlen=40))
_LAST_CLEAN=0.0

def allow(key, limit=12, window=20):
    global _LAST_CLEAN
    now=time.time(); q=_EVENTS[str(key)]
    while q and now-q[0] > window: q.popleft()
    if len(q)>=limit: return False
    q.append(now)
    if now-_LAST_CLEAN>300 and len(_EVENTS)>5000:
        for k in list(_EVENTS)[:2000]:
            if not _EVENTS[k]: _EVENTS.pop(k,None)
        _LAST_CLEAN=now
    return True
