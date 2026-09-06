import sqlite3
import json
import threading
import os
import logging
import time

DB_DIR = "data"
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "bot.db")

db_lock = threading.RLock()
conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA busy_timeout=30000")
conn.execute("PRAGMA temp_store=MEMORY")

_cache = {}
_CACHE_TTL = 8.0

with db_lock:
    conn.execute("CREATE TABLE IF NOT EXISTS store (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()


def db_get(key, default=None):
    now = time.monotonic()
    with db_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]
        try:
            cur = conn.execute("SELECT value FROM store WHERE key=?", (key,))
            row = cur.fetchone()
            value = json.loads(row[0]) if row else default
            _cache[key] = (now, value)
            return value
        except Exception as e:
            logging.error(f"DB Read Error [{key}]: {e}")
            return default


def db_set(key, value):
    with db_lock:
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            conn.execute("REPLACE INTO store (key, value) VALUES (?, ?)", (key, encoded))
            conn.commit()
            _cache[key] = (time.monotonic(), value)
            return True
        except Exception as e:
            logging.error(f"DB Write Error [{key}]: {e}")
            return False


def db_invalidate(key=None):
    with db_lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)
