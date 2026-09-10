# -*- coding: utf-8 -*-
"""SQLite backup and retention helpers for Railway/production."""
import os, sqlite3, time, logging
from pathlib import Path
from database import DB_PATH

LOG = logging.getLogger(__name__)
BACKUP_DIR = Path(os.environ.get("LIZA_BACKUP_DIR", "data/backups"))
KEEP = max(1, int(os.environ.get("LIZA_BACKUP_KEEP", "7")))

def create_backup():
    src = Path(DB_PATH)
    if not src.exists(): return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"bot-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
    source = sqlite3.connect(str(src))
    dest = sqlite3.connect(str(target))
    try:
        source.backup(dest)
        dest.commit()
    finally:
        dest.close(); source.close()
    files = sorted(BACKUP_DIR.glob("bot-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP:]:
        try: old.unlink()
        except OSError: LOG.warning("cannot delete backup %s", old)
    return str(target)
