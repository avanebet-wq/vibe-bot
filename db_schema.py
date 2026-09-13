# -*- coding: utf-8 -*-
"""Schema/migration marker for legacy JSON plus normalized tables introduced in v20."""
from database import conn, db_lock
SCHEMA_VERSION=24

def ensure_schema():
    with db_lock:
        conn.execute("CREATE TABLE IF NOT EXISTS migration_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS user_facts (chat_id TEXT NOT NULL, user_id TEXT NOT NULL, fact TEXT NOT NULL, source TEXT, importance INTEGER DEFAULT 1, created_at REAL NOT NULL, PRIMARY KEY(chat_id,user_id,fact))")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_facts_chat_user ON user_facts(chat_id,user_id)")
        conn.execute("CREATE TABLE IF NOT EXISTS app_events (chat_id TEXT, event_type TEXT, actor_id TEXT, target_id TEXT, created_at REAL NOT NULL, payload TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_app_events_chat_time ON app_events(chat_id,created_at)")
        conn.execute("CREATE TABLE IF NOT EXISTS minigame_events (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, user_id TEXT NOT NULL, username TEXT, display_name TEXT, kind TEXT NOT NULL, created_at REAL NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_minigame_events_chat_kind_time ON minigame_events(chat_id,kind,created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_minigame_events_chat_user_kind_time ON minigame_events(chat_id,user_id,kind,created_at)")
        conn.execute("""CREATE TABLE IF NOT EXISTS drink_game_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL, user_id TEXT NOT NULL, username TEXT, display_name TEXT,
            revo_name TEXT NOT NULL, fruit_emoji TEXT NOT NULL, multiplier REAL NOT NULL,
            volume_liters REAL NOT NULL, created_at REAL NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_drink_game_chat_user_time ON drink_game_events(chat_id,user_id,created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_drink_game_chat_time ON drink_game_events(chat_id,created_at)")
        conn.execute("CREATE TABLE IF NOT EXISTS chat_user_presence (chat_id TEXT NOT NULL, user_id TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL, PRIMARY KEY(chat_id,user_id))")
        conn.execute("CREATE TABLE IF NOT EXISTS profile_xp (chat_id TEXT NOT NULL, user_id TEXT NOT NULL, xp INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(chat_id,user_id))")
        conn.execute("INSERT OR REPLACE INTO migration_meta(key,value) VALUES('schema_version',?)",(str(SCHEMA_VERSION),))
        conn.commit()
    return SCHEMA_VERSION
