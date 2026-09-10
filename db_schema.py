# -*- coding: utf-8 -*-
"""Schema/migration marker for legacy JSON plus normalized tables introduced in v20."""
from database import conn, db_lock
SCHEMA_VERSION=20

def ensure_schema():
    with db_lock:
        conn.execute("CREATE TABLE IF NOT EXISTS migration_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS user_facts (chat_id TEXT NOT NULL, user_id TEXT NOT NULL, fact TEXT NOT NULL, source TEXT, importance INTEGER DEFAULT 1, created_at REAL NOT NULL, PRIMARY KEY(chat_id,user_id,fact))")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_facts_chat_user ON user_facts(chat_id,user_id)")
        conn.execute("CREATE TABLE IF NOT EXISTS app_events (chat_id TEXT, event_type TEXT, actor_id TEXT, target_id TEXT, created_at REAL NOT NULL, payload TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_app_events_chat_time ON app_events(chat_id,created_at)")
        conn.execute("INSERT OR REPLACE INTO migration_meta(key,value) VALUES('schema_version',?)",(str(SCHEMA_VERSION),))
        conn.commit()
    return SCHEMA_VERSION
