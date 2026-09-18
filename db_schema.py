# -*- coding: utf-8 -*-
"""Schema/migration marker for normalized PostgreSQL tables."""
from database import conn, db_lock

SCHEMA_VERSION = 27


def ensure_schema():
    with db_lock:
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS migration_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            # Premium emoji library v26: packs are global and ordered by first creation.
            # Old flat premium_emojis data is intentionally discarded once, during
            # the v25 -> v26 migration; subsequent startups keep the new library.
            _schema_row = conn.execute("SELECT value FROM migration_meta WHERE key='schema_version'").fetchone()
            _old_schema = int(_schema_row[0]) if _schema_row and str(_schema_row[0]).isdigit() else 0
            if _old_schema < 26:
                conn.execute("DROP TABLE IF EXISTS premium_emojis")
                conn.execute("DROP TABLE IF EXISTS premium_emoji_packs")
            conn.execute("""CREATE TABLE IF NOT EXISTS premium_emoji_packs (
                pack_id BIGSERIAL PRIMARY KEY,
                set_name TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                preview_emoji_id TEXT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS premium_emojis (
                emoji_id TEXT PRIMARY KEY,
                pack_id BIGINT NOT NULL,
                emoji TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                preview_url TEXT,
                added_at REAL NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_premium_emoji_pack ON premium_emojis(pack_id, added_at, emoji_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_premium_emoji_emoji ON premium_emojis(emoji)")

            # v27: persistent disk cache for emoji preview images (avoids re-fetching from Telegram)
            conn.execute("""CREATE TABLE IF NOT EXISTS emoji_preview_cache (
                emoji_id TEXT PRIMARY KEY,
                content_type TEXT NOT NULL,
                payload BLOB NOT NULL,
                cached_at REAL NOT NULL
            )""")

            # v27: group-local emoji pack subscriptions (users can add packs visible only in their group)
            conn.execute("""CREATE TABLE IF NOT EXISTS group_emoji_packs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                set_name TEXT NOT NULL,
                pack_id BIGINT,
                added_by TEXT,
                added_at REAL NOT NULL,
                UNIQUE(chat_id, set_name)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_group_emoji_packs_chat ON group_emoji_packs(chat_id, added_at)")

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
            conn.execute(
                "INSERT INTO migration_meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return SCHEMA_VERSION

# updated 2026-09-18
