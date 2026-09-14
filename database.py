# -*- coding: utf-8 -*-
"""Persistent PostgreSQL storage for Liza.

The bot uses Railway PostgreSQL when DATABASE_URL is configured.  The small
compatibility wrapper keeps the existing SQLite-style ``conn.execute(..., ?)``
API used throughout the project, so application modules do not need to know
which database driver is underneath.

If an old local SQLite bot.db or liza_memory.sqlite3 exists on the Railway
volume, it is migrated once into PostgreSQL automatically.  SQLite files are
never included in project archives.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import copy
from pathlib import Path

try:
    import psycopg2
except Exception as exc:  # pragma: no cover - dependency is installed in production
    psycopg2 = None
    _PG_IMPORT_ERROR = exc
else:
    _PG_IMPORT_ERROR = None

LOG = logging.getLogger(__name__)
DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "bot.db")  # legacy path, kept for compatibility
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()


def _require_pg():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    if psycopg2 is None:
        raise RuntimeError(f"psycopg2 is unavailable: {_PG_IMPORT_ERROR}")


def _translate_sql(sql: str) -> str:
    """Translate the project's SQLite-flavoured SQL to PostgreSQL."""
    text = str(sql)
    # Existing modules use SQLite '?' placeholders.
    text = text.replace("?", "%s")
    # SQLite-only AUTOINCREMENT syntax.
    text = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", text, flags=re.I)
    text = re.sub(r"INT\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", text, flags=re.I)
    return text


class _PGConnection:
    def __init__(self, raw):
        self.raw = raw

    def _reconnect(self):
        global _raw_conn
        old = self.raw
        try:
            old.close()
        except Exception:
            pass
        _require_pg()
        new_raw = _connect_pg()
        self.raw = new_raw
        _raw_conn = new_raw

    def execute(self, sql, params=None):
        text = _translate_sql(sql)
        readonly = str(text).lstrip().upper().startswith(("SELECT", "SHOW", "WITH", "EXPLAIN"))
        try:
            cur = self.raw.cursor()
            cur.execute(text, params)
            return cur
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            # A write can have an ambiguous outcome: PostgreSQL may have
            # accepted it before the socket failed. Never transparently retry
            # mutating statements, otherwise a transient disconnect can
            # duplicate XP/events/stats. Read-only queries may safely retry.
            self._reconnect()
            if not readonly:
                raise
            cur = self.raw.cursor()
            cur.execute(text, params)
            return cur


    def commit(self):
        try:
            self.raw.commit()
            return
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            # COMMIT outcome is ambiguous after a transport failure. Do not
            # reconnect-and-commit again; recover the connection and surface
            # the error to the caller so it can decide what is safe to retry.
            try:
                self._reconnect()
            except Exception:
                pass
            raise


    def rollback(self):
        try:
            self.raw.rollback()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            try:
                self._reconnect()
            except Exception:
                pass

    def close(self):
        self.raw.close()


def _connect_pg():
    _require_pg()
    delay=1.0
    last=None
    for attempt in range(6):
        try:
            raw=psycopg2.connect(DATABASE_URL, connect_timeout=10, application_name="liza")
            raw.autocommit=False
            return raw
        except Exception as exc:
            last=exc
            LOG.error("PostgreSQL unavailable at startup (attempt %s/6): %s", attempt+1, exc)
            if attempt<5:
                time.sleep(delay)
                delay=min(delay*2.0,15.0)
    raise RuntimeError(f"PostgreSQL unavailable: {last}")

_require_pg()
_raw_conn = _connect_pg()
conn = _PGConnection(_raw_conn)

db_lock = threading.RLock()
_cache = {}
_CACHE_TTL = 2.0
_NO_CACHE_KEYS = {
    "stats", "moderation", "contest_sessions", "contest_configs",
    "contest_settings_pending", "group_settings", "known_groups",
    "user_memory", "mood_state", "social_context", "goals",
}


def _pg_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _sqlite_schema_to_pg(create_sql: str) -> str:
    sql = create_sql
    sql = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", sql, flags=re.I)
    sql = re.sub(r"INT\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", sql, flags=re.I)
    sql = re.sub(r"\bBLOB\b", "BYTEA", sql, flags=re.I)
    # SQLite's INSERT OR REPLACE is handled in application queries; schema
    # definitions themselves do not use it.
    return sql


def _table_names(sqlite_conn):
    rows = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r[0] for r in rows]


def _migrate_sqlite_file(path: str, label: str) -> tuple[int, bool]:
    """Migrate an old SQLite database into PG without failing on existing tables.

    Returns (migrated_rows, completed). ``completed`` is false when any table
    could not be created/read/imported, so the global migration marker is not
    written prematurely and the migration can safely retry on the next start.
    """
    if not Path(path).exists():
        return 0, True
    try:
        src = sqlite3.connect(path)
        src.row_factory = sqlite3.Row
    except Exception:
        LOG.exception("Cannot open legacy SQLite database %s", path)
        return 0, False

    migrated = 0
    completed = True
    try:
        with db_lock:
            for table in _table_names(src):
                row = src.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not row or not row[0]:
                    continue
                create_sql = _sqlite_schema_to_pg(row[0])
                # PostgreSQL may already contain a table from a previous run.
                # CREATE IF NOT EXISTS makes the migration idempotent.
                create_sql = re.sub(r"^\s*CREATE\s+TABLE\s+", "CREATE TABLE IF NOT EXISTS ", create_sql, count=1, flags=re.I)
                try:
                    conn.execute(create_sql)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    completed = False
                    LOG.exception("Cannot create migrated table %s from %s", table, label)
                    continue

                escaped_table = table.replace(chr(34), chr(34) * 2)
                columns = [r[1] for r in src.execute(f'PRAGMA table_info("{escaped_table}")').fetchall()]
                if not columns:
                    completed = False
                    LOG.error("Cannot read columns for migrated table %s from %s", table, label)
                    continue

                rows = src.execute(f'SELECT * FROM "{table.replace(chr(34), chr(34)*2)}"').fetchall()
                if not rows:
                    continue
                col_sql = ", ".join(_pg_identifier(c) for c in columns)
                placeholders = ", ".join(["%s"] * len(columns))
                insert_sql = (
                    f"INSERT INTO {_pg_identifier(table)} ({col_sql}) VALUES ({placeholders}) "
                    "ON CONFLICT DO NOTHING"
                )
                for item in rows:
                    values = []
                    for value in item:
                        # JSON/text values are already strings in SQLite.
                        values.append(value)
                    try:
                        conn.execute(insert_sql, values)
                        migrated += 1
                    except Exception:
                        conn.rollback()
                        completed = False
                        LOG.exception("Cannot migrate row in %s.%s", label, table)
                conn.commit()

                # Keep sequences ahead of imported IDs where applicable.
                # Do not roll back the imported rows for tables without an id
                # column (many legacy tables do not have one).
                if "id" in columns:
                    try:
                        seq = conn.execute(
                            "SELECT pg_get_serial_sequence(%s, %s)",
                            (table, "id"),
                        ).fetchone()[0]
                        if seq:
                            max_id = conn.execute(
                                f"SELECT MAX({_pg_identifier('id')}) FROM {_pg_identifier(table)}"
                            ).fetchone()[0]
                            if max_id is not None:
                                conn.execute("SELECT setval(%s, %s, true)", (seq, int(max_id)))
                                conn.commit()
                    except Exception:
                        conn.rollback()
                        completed = False
                        LOG.exception("Cannot update sequence for migrated table %s from %s", table, label)
        LOG.info("Migrated %s rows from legacy %s", migrated, label)
        return migrated, completed
    finally:
        src.close()


with db_lock:
    conn.execute("CREATE TABLE IF NOT EXISTS store (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()

# Migrate old data if it exists on a Railway volume. A marker prevents doing
# the expensive scan on every restart, while still allowing a normal first
# deployment with no legacy files.
with db_lock:
    conn.execute("CREATE TABLE IF NOT EXISTS migration_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    marker = conn.execute("SELECT value FROM migration_meta WHERE key=%s", ("legacy_sqlite_migrated",)).fetchone()
    legacy_done = bool(marker and str(marker[0]) == "1")

if not legacy_done:
    _, bot_db_done = _migrate_sqlite_file(DB_PATH, "bot.db")
    _, memory_db_done = _migrate_sqlite_file(
        os.path.join(DB_DIR, "liza_memory.sqlite3"),
        "liza_memory.sqlite3",
    )
    # Mark the migration only after every legacy source completed. This makes
    # the process safe to retry after a transient/database/schema error.
    if bot_db_done and memory_db_done:
        with db_lock:
            conn.execute(
                "INSERT INTO migration_meta(key,value) VALUES(%s,%s) "
                "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                ("legacy_sqlite_migrated", "1"),
            )
            conn.commit()


def db_get(key, default=None):
    now = time.monotonic()
    with db_lock:
        use_cache = key not in _NO_CACHE_KEYS
        cached = _cache.get(key) if use_cache else None
        if cached and now - cached[0] < _CACHE_TTL:
            return copy.deepcopy(cached[1])
        try:
            cur = conn.execute("SELECT value FROM store WHERE key=%s", (key,))
            row = cur.fetchone()
            value = json.loads(row[0]) if row else copy.deepcopy(default)
            if use_cache:
                _cache[key] = (now, copy.deepcopy(value))
            return copy.deepcopy(value)
        except Exception as e:
            conn.rollback()
            logging.error(f"DB Read Error [{key}]: {e}")
            return copy.deepcopy(default)


def db_set(key, value):
    with db_lock:
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                "INSERT INTO store(key,value) VALUES(%s,%s) "
                "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                (key, encoded),
            )
            conn.commit()
            if key in _NO_CACHE_KEYS:
                _cache.pop(key, None)
            else:
                _cache[key] = (time.monotonic(), copy.deepcopy(value))
            return True
        except Exception as e:
            conn.rollback()
            logging.error(f"DB Write Error [{key}]: {e}")
            return False


def db_update_json(key, mutator, default=None):
    """Atomically read-modify-write one JSON value under the DB lock.

    The mutator receives a mutable Python object and may return a replacement
    value. The whole operation is serialized with other database operations,
    preventing lost updates from concurrent Telegram handlers.
    """
    with db_lock:
        try:
            cur = conn.execute("SELECT value FROM store WHERE key=%s FOR UPDATE", (key,))
            row = cur.fetchone()
            current = json.loads(row[0]) if row else default
            if current is None:
                current = default
            result = mutator(current)
            if result is None:
                result = current
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                "INSERT INTO store(key,value) VALUES(%s,%s) "
                "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                (key, encoded),
            )
            conn.commit()
            _cache.pop(key, None)
            return copy.deepcopy(result)
        except Exception as exc:
            conn.rollback()
            LOG.error("DB Atomic JSON Update Error [%s]: %s", key, exc, exc_info=True)
            raise


def db_invalidate(key=None):
    with db_lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)


def db_checkpoint():
    # PostgreSQL does not need SQLite WAL checkpoints. This is a lightweight
    # health check, but it also recovers a connection left in an aborted
    # transaction by any unexpected direct DB caller.
    try:
        with db_lock:
            conn.execute("SELECT 1")
            conn.rollback()
        return True
    except Exception as e:
        with db_lock:
            try:
                conn.rollback()
            except Exception:
                pass
        LOG.error("DB health check error: %s", e)
        return False


def db_close():
    with db_lock:
        try:
            conn.close()
        except Exception:
            pass
