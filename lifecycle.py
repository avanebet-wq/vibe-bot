# -*- coding: utf-8 -*-
"""Application lifecycle: health, plugins and graceful shutdown."""
import logging, signal
from plugins import discover
from reliability import stop, safe_loop
from goals import due, mark_notified
from contest import tick as contest_tick
from word_game import tick as word_game_tick
LOG=logging.getLogger(__name__)

def _notify_due(bot):
    try:
        from database import db_get
        groups=db_get("known_groups",{}) or {}
        for gid in list(groups)[:500]:
            for goal in due(gid, limit=5):
                try:
                    owner=goal.get("owner_id")
                    target=owner if owner else gid
                    bot.send_message(target, "🎯 Напоминание: <b>"+str(goal.get("title","цель")).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")+"</b>")
                    mark_notified(gid, goal.get("id"))
                except Exception:
                    LOG.exception("goal notification failed for %s", goal.get("id"))
    except Exception:
        LOG.exception("goal scan failed")

def install(bot):
    try: discover(bot)
    except Exception: LOG.exception("plugin discovery failed")
    safe_loop("goals", lambda: _notify_due(bot), 60)
    safe_loop("contests", lambda: contest_tick(bot), 1)
    safe_loop("word-games", lambda: word_game_tick(bot), 1)
    def shutdown(*_):
        stop()
    try:
        signal.signal(signal.SIGTERM, shutdown); signal.signal(signal.SIGINT, shutdown)
    except ValueError:
        pass
