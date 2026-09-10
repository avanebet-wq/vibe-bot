"""Smart autoactivity decision logic for Liza."""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque

_HISTORY = defaultdict(lambda: deque(maxlen=18))
_LAST_LIZA = {}
_COOLDOWN = {}

QUESTION_RE = re.compile(r"[?？]|^(как|что|кто|где|когда|зачем|почему|можно|а ты|ты)\b", re.I)
DIRECT_RE = re.compile(r"\bлиза\b", re.I)
ACTIVE_RE = re.compile(
    r"\b(да|нет|ага|угу|лол|ахах|кек|точно|согласен|согласна|жесть|капец|блин|"
    r"смотри|слушай|короче|вообще|реально)\b", re.I
)

def remember_message(chat_id, text, is_liza=False):
    if not text:
        return
    _HISTORY[chat_id].append({
        "text": str(text)[:1000],
        "is_liza": bool(is_liza),
        "ts": time.time(),
    })
    if is_liza:
        _LAST_LIZA[chat_id] = time.time()

def mark_liza_response(chat_id):
    _LAST_LIZA[chat_id] = time.time()

def should_auto_reply(chat_id, text, base_chance=0.05):
    """Return whether Liza should join an unsolicited group message."""
    text = (text or "").strip()
    if not text:
        return False

    # Never compete with a direct address; the regular handler handles it.
    if DIRECT_RE.search(text):
        return False

    history = list(_HISTORY.get(chat_id, ()))
    now = time.time()

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
