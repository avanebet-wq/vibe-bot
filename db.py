import sqlite3
import json
import threading
import os

db_lock = threading.RLock()
DB_DIR = "data"
DB_FILE = os.path.join(DB_DIR, "bot.db")

os.makedirs(DB_DIR, exist_ok=True)
conn = sqlite3.connect(DB_FILE, check_same_thread=False)

with db_lock:
    conn.execute("CREATE TABLE IF NOT EXISTS store (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()

def db_get(key, default=None):
    with db_lock:
        cur = conn.execute("SELECT value FROM store WHERE key=?", (key,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else default

def db_set(key, value):
    with db_lock:
        conn.execute("REPLACE INTO store (key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))
        conn.commit()

# Функции быстрой работы с таблицами
def get_cfg(): return db_get("cfg", {})
def set_cfg(data): db_set("cfg", data)

def get_autopost(): 
    default = {"posts": [{"id": "1", "name": "Пост №1", "enabled": False, "interval": 3600, "daily_time": None, "start_date": None, "auto_delete_prev": False, "last_msg_id": None, "text": "Та ну, всё самое интересное тут: @vibe_247top_bot ...", "photo": None, "buttons": [], "last_post": 0, "chat_id": -1004374303475}]}
    return db_get("autopost", default)
def set_autopost(data): db_set("autopost", data)

def get_mutes(): return db_get("mutes", {})
def set_mutes(data): db_set("mutes", data)

def get_lucky_leaders(): return db_get("lucky_leaders", {})
def set_lucky_leaders(data): db_set("lucky_leaders", data)

def get_safe_leaders(): return db_get("safe_leaders", {})
def set_safe_leaders(data): db_set("safe_leaders", data)

def get_chats_cache(): return db_get("chats_cache", {"-1004374303475": "Основная VIBE", "-1003514059820": "Вторая группа"})
def set_chats_cache(data): db_set("chats_cache", data)
