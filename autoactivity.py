"""Smart autoactivity decision logic for Liza."""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque

_HISTORY = defaultdict(lambda: deque(maxlen=18))
_LAST_LIZA = {}
_COOLDOWN = {}
_MAX_CHATS = 5000
_STATE_TTL = 3600.0
_LAST_CLEAN = 0.0

QUESTION_RE = re.compile(r"[?？]|^(как|что|кто|где|когда|зачем|почему|можно|а ты|ты)\b", re.I)
DIRECT_RE = re.compile(r"\bлиза\b", re.I)
ACTIVE_RE = re.compile(
    r"\b(да|нет|ага|угу|лол|ахах|кек|точно|согласен|согласна|жесть|капец|блин|"
    r"смотри|слушай|короче|вообще|реально)\b", re.I
)

def _cleanup(now=None):
    global _LAST_CLEAN
    now = time.time() if now is None else now
    if now - _LAST_CLEAN < 300 and len(_HISTORY) <= _MAX_CHATS:
        return
    cutoff = now - _STATE_TTL
    for key, ts in list(_LAST_LIZA.items()):
        if ts < cutoff:
            _LAST_LIZA.pop(key, None); _HISTORY.pop(key, None); _COOLDOWN.pop(key, None)
    _LAST_CLEAN = now

def remember_message(chat_id, text, is_liza=False):
    global _LAST_CLEAN
    if not text:
        return
    now=time.time()
    _cleanup(now)
    _HISTORY[chat_id].append({
        "text": str(text)[:1000],
        "is_liza": bool(is_liza),
        "ts": time.time(),
    })
    if is_liza:
        _LAST_LIZA[chat_id] = now
    if len(_HISTORY) > _MAX_CHATS:
        oldest = sorted(_HISTORY.keys(), key=lambda k: _LAST_LIZA.get(k, 0))[:max(1, len(_HISTORY)-_MAX_CHATS)]
        for key in oldest:
            _HISTORY.pop(key, None); _LAST_LIZA.pop(key, None); _COOLDOWN.pop(key, None)

def mark_liza_response(chat_id):
    now=time.time(); _cleanup(now); _LAST_LIZA[chat_id] = now

def should_auto_reply(chat_id, text, base_chance=0.05):
    """Return whether Liza should join an unsolicited group message."""
    text = (text or "").strip()
    if not text:
        return False

    # Never compete with a direct address; the regular handler handles it.
    if DIRECT_RE.search(text):
        return False

    now = time.time(); _cleanup(now)
    history = list(_HISTORY.get(chat_id, ()))

    # Recent Liza answer means a short cooldown to avoid spamming.
    last_liza = _LAST_LIZA.get(chat_id, 0)
    if now - last_liza < 25:
        return False

    score = float(base_chance)

    # Questions are much more likely to benefit from an answer.
    if QUESTION_RE.search(text):
        score += 0.16

    # Very short conversational reactions are less useful to interrupt.
    words = text.split()
    if len(words) <= 2:
        score -= 0.04

    # If several users have been talking recently, lower interruption probability.
    recent = [x for x in history if now - x["ts"] < 75]
    if len(recent) >= 5:
        score -= 0.06

    # If nobody has spoken recently, a small bump is useful for reviving chat.
    if not recent:
        score += 0.04

    # If the current text looks like conversational banter, reduce intrusion.
    if ACTIVE_RE.search(text) and not QUESTION_RE.search(text):
        score -= 0.03

    # Cap to a reasonable range.
    score = max(0.0, min(0.35, score))

    # Deterministic-ish threshold from the message text avoids an extra RNG source
    # while still varying across messages.
    import hashlib
    digest = hashlib.sha256(f"{chat_id}:{text}:{int(now // 8)}".encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") / 2**32
    return bucket < score
