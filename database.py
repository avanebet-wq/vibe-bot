import sqlite3
import json
import threading
import os
import logging

DB_DIR = "data" # Папка на диске Railway
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "bot.db")

db_lock = threading.RLock()
conn = sqlite3.connect(DB_PATH, check_same_thread=False)

with db_lock:
    conn.execute("CREATE TABLE IF NOT EXISTS store (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()

def db_get(key, default=None):
    with db_lock:
        try:
            cur = conn.execute("SELECT value FROM store WHERE key=?", (key,))
            row = cur.fetchone()
            return json.loads(row[0]) if row else default
        except Exception as e:
            logging.error(f"DB Read Error [{key}]: {e}")
            return default

def db_set(key, value):
    with db_lock:
        try:
            conn.execute("REPLACE INTO store (key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))
            conn.commit()
        except Exception as e:
            logging.error(f"DB Write Error [{key}]: {e}")
