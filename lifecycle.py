# -*- coding: utf-8 -*-
"""Application lifecycle: health, backups, plugins and graceful shutdown."""
import atexit, logging, signal
from backup import create_backup
from plugins import discover
from reliability import stop, safe_loop, health
from goals import due, mark_notified
from contest import tick as contest_tick
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
    try: create_backup()
    except Exception: LOG.exception("initial backup failed")
    safe_loop("backup", lambda: create_backup(), 6*60*60)
    safe_loop("goals", lambda: _notify_due(bot), 60)
    safe_loop("contests", lambda: contest_tick(bot), 1)
    def shutdown(*_):
        try: create_backup()
        except Exception: LOG.exception("shutdown backup failed")
        stop()
    try:
        signal.signal(signal.SIGTERM, shutdown); signal.signal(signal.SIGINT, shutdown)
    except ValueError:
        pass
    atexit.register(lambda: create_backup())
