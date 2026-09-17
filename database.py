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
    import psycopg2.pool
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


# ---------------------------------------------------------------------------
# Connection pooling.
#
# The old implementation kept exactly one psycopg2 connection for the whole
# process and serialized every single database call (from every thread, for
# every unrelated chat) behind one threading.RLock(). That made the DB the
# real concurrency ceiling of the bot: a slow query for chat A blocked stats,
# moderation and AI-context lookups for chat B, C, D... even though Postgres
# itself was perfectly capable of running those in parallel.
#
# Now `db_lock` is a per-thread, reentrant *checkout* of a real connection
# from a pool. Each thread that needs the DB gets its own live connection for
# the duration of its `with db_lock:` block, so independent operations on
# different threads actually run concurrently against Postgres. Nested
# `with db_lock:` blocks on the same thread reuse the same checked-out
# connection (reentrant, just like the old RLock), so no call site anywhere
# else in the project needs to change.
# ---------------------------------------------------------------------------

_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "4"))
_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "20"))
_local = threading.local()


def _create_pool():
    _require_pg()
    delay = 1.0
    last = None
    for attempt in range(6):
        try:
            return psycopg2.pool.ThreadedConnectionPool(
                _POOL_MIN, _POOL_MAX, DATABASE_URL,
                connect_timeout=10, application_name="liza",
            )
        except Exception as exc:
            last = exc
            LOG.error("PostgreSQL pool unavailable at startup (attempt %s/6): %s", attempt + 1, exc)
            if attempt < 5:
                time.sleep(delay)
                delay = min(delay * 2.0, 15.0)
    raise RuntimeError(f"PostgreSQL unavailable: {last}")


_require_pg()
_pool = _create_pool()


def _checkout_raw():
    delay = 0.5
    last = None
    for attempt in range(4):
        try:
            raw = _pool.getconn()
            raw.autocommit = False
            return raw
        except Exception as exc:
            last = exc
            LOG.error("DB pool checkout failed (attempt %s/4): %s", attempt + 1, exc)
            time.sleep(delay)
            delay = min(delay * 2.0, 4.0)
    raise RuntimeError(f"PostgreSQL pool exhausted/unavailable: {last}")


def _release_raw(raw):
    if raw is None:
        return
    try:
        # A caller that forgot to commit/rollback (or crashed mid-transaction)
        # must never poison the pool for the next thread that checks this
        # connection out. rollback() on a clean connection is a harmless no-op.
        raw.rollback()
    except Exception:
        try:
            raw.close()
        except Exception:
            pass
        return
    try:
        _pool.putconn(raw)
    except Exception:
        try:
            raw.close()
        except Exception:
            pass


class _DBLock:
    """Reentrant, per-thread checkout of a pooled PostgreSQL connection."""

    def __enter__(self):
        depth = getattr(_local, "depth", 0)
        if depth == 0:
            _local.conn = _checkout_raw()
        _local.depth = depth + 1
        return self

    def __exit__(self, exc_type, exc, tb):
        _local.depth = getattr(_local, "depth", 1) - 1
        if _local.depth <= 0:
            raw = getattr(_local, "conn", None)
            _local.conn = None
            _local.depth = 0
            _release_raw(raw)
        return False


db_lock = _DBLock()


class _PGConnection:
    """Proxy that always operates on the calling thread's checked-out
    connection (see _DBLock above). Every call must happen inside a
    `with db_lock:` block, exactly as before."""

    def _raw(self):
        raw = getattr(_local, "conn", None)
        if raw is None:
            raise RuntimeError(
                "database access outside 'with db_lock:' -- no pooled "
                "connection is checked out for this thread"
            )
        return raw

    def _reconnect(self):
        old = getattr(_local, "conn", None)
        try:
            if old is not None:
                old.close()
        except Exception:
            pass
        _require_pg()
        new_raw = _checkout_raw()
        _local.conn = new_raw
        return new_raw

    def execute(self, sql, params=None):
        text = _translate_sql(sql)
        readonly = str(text).lstrip().upper().startswith(("SELECT", "SHOW", "WITH", "EXPLAIN"))
        raw = self._raw()
        try:
            cur = raw.cursor()
            cur.execute(text, params)
            return cur
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            # A write can have an ambiguous outcome: PostgreSQL may have
            # accepted it before the socket failed. Never transparently retry
            # mutating statements, otherwise a transient disconnect can
            # duplicate XP/events/stats. Read-only queries may safely retry.
            raw = self._reconnect()
            if not readonly:
                raise
            cur = raw.cursor()
            cur.execute(text, params)
            return cur

    def commit(self):
        raw = self._raw()
        try:
            raw.commit()
            return
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            # SQLSTATE 08007 means the transaction resolution is unknown.
            # In that state PostgreSQL does not give the client a safe way to
            # infer whether COMMIT reached the server. Never auto-retry a
            # mutating transaction: that could duplicate events or XP.
            pgcode = getattr(exc, "pgcode", None)
            LOG.error("PostgreSQL COMMIT outcome is ambiguous (SQLSTATE=%s)", pgcode)
            try:
                self._reconnect()
            except Exception:
                LOG.exception("PostgreSQL reconnect after ambiguous COMMIT failed")
            raise

    def rollback(self):
        raw = self._raw()
        try:
            raw.rollback()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            try:
                self._reconnect()
            except Exception:
                pass

    def close(self):
        # Closing the shared proxy makes no sense with a pool; real shutdown
        # goes through db_close(), which closes the whole pool.
        pass


conn = _PGConnection()

_cache = {}
_scoped_cache = {}
_CACHE_TTL = 2.0
_NO_CACHE_KEYS = {
    "stats", "moderation", "group_settings", "known_groups",
    "goals",
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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS miniapp_replay (replay_key TEXT PRIMARY KEY, seen_at DOUBLE PRECISION NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_miniapp_replay_seen_at ON miniapp_replay(seen_at)")
    # Per-scope JSON storage: one row per (namespace, scope) -- e.g. one row
    # per chat_id for mood/social state, one row per "chat_id:user_id" for
    # per-user facts -- instead of one giant JSON blob per namespace shared
    # by every chat. This means updating chat A's mood never locks or
    # rewrites chat B's mood, and reading one user's facts never has to load
    # every user's facts in the whole bot.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS scoped_store ("
        "namespace TEXT NOT NULL, scope TEXT NOT NULL, value TEXT NOT NULL, "
        "PRIMARY KEY(namespace, scope))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scoped_store_namespace ON scoped_store(namespace)")
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



def db_get_scoped(namespace, scope, default=None):
    """Read one (namespace, scope) row -- e.g. one chat's mood, one user's
    facts -- instead of the whole namespace's blob. Cached the same way as
    db_get, but the cache/row granularity is per-scope, so one hot chat can't
    evict or block another's."""
    cache_key = f"{namespace}\x00{scope}"
    now = time.monotonic()
    with db_lock:
        cached = _scoped_cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL:
            return copy.deepcopy(cached[1])
        try:
            cur = conn.execute(
                "SELECT value FROM scoped_store WHERE namespace=%s AND scope=%s",
                (namespace, str(scope)),
            )
            row = cur.fetchone()
            value = json.loads(row[0]) if row else copy.deepcopy(default)
            _scoped_cache[cache_key] = (now, copy.deepcopy(value))
            return copy.deepcopy(value)
        except Exception as e:
            conn.rollback()
            LOG.error("Scoped DB Read Error [%s/%s]: %s", namespace, scope, e)
            return copy.deepcopy(default)


def db_set_scoped(namespace, scope, value):
    cache_key = f"{namespace}\x00{scope}"
    with db_lock:
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                "INSERT INTO scoped_store(namespace,scope,value) VALUES(%s,%s,%s) "
                "ON CONFLICT(namespace,scope) DO UPDATE SET value=EXCLUDED.value",
                (namespace, str(scope), encoded),
            )
            conn.commit()
            _scoped_cache[cache_key] = (time.monotonic(), copy.deepcopy(value))
            return True
        except Exception as e:
            conn.rollback()
            LOG.error("Scoped DB Write Error [%s/%s]: %s", namespace, scope, e)
            return False


def db_update_json_scoped(namespace, scope, mutator, default=None):
    """Same contract as db_update_json, but scoped to one (namespace, scope)
    row. The FOR UPDATE row lock now only ever contends with another writer
    of the *same* scope (e.g. the same chat), never with unrelated chats."""
    cache_key = f"{namespace}\x00{scope}"
    with db_lock:
        try:
            cur = conn.execute(
                "SELECT value FROM scoped_store WHERE namespace=%s AND scope=%s FOR UPDATE",
                (namespace, str(scope)),
            )
            row = cur.fetchone()
            current = json.loads(row[0]) if row else default
            if current is None:
                current = default
            result = mutator(current)
            if result is None:
                result = current
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                "INSERT INTO scoped_store(namespace,scope,value) VALUES(%s,%s,%s) "
                "ON CONFLICT(namespace,scope) DO UPDATE SET value=EXCLUDED.value",
                (namespace, str(scope), encoded),
            )
            conn.commit()
            _scoped_cache.pop(cache_key, None)
            return copy.deepcopy(result)
        except Exception as exc:
            conn.rollback()
            LOG.error("Scoped DB Atomic JSON Update Error [%s/%s]: %s", namespace, scope, exc, exc_info=True)
            raise


def db_delete_scoped_prefix(namespace, scope_prefix):
    """Delete every scoped_store row for `namespace` whose scope starts with
    `scope_prefix` -- e.g. clearing every "chat_id:user_id" row for one chat
    when memory is wiped for that chat."""
    escaped = str(scope_prefix).replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    with db_lock:
        try:
            conn.execute(
                "DELETE FROM scoped_store WHERE namespace=%s AND scope LIKE %s",
                (namespace, escaped + "%"),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        prefix = f"{namespace}\x00{scope_prefix}"
        for k in [k for k in _scoped_cache if k.startswith(prefix)]:
            _scoped_cache.pop(k, None)


def db_invalidate_scoped(namespace, scope=None):
    with db_lock:
        if scope is None:
            prefix = f"{namespace}\x00"
            for k in [k for k in _scoped_cache if k.startswith(prefix)]:
                _scoped_cache.pop(k, None)
        else:
            _scoped_cache.pop(f"{namespace}\x00{scope}", None)


def _migrate_blob_to_scoped(old_key, namespace):
    """One-time move of a legacy single-blob key (all chats/users in one
    JSON value under `store`) into per-scope rows under `scoped_store`.
    Guarded by migration_meta so it only ever runs once per namespace."""
    marker_key = f"scoped_migrated:{namespace}"
    with db_lock:
        row = conn.execute("SELECT value FROM migration_meta WHERE key=%s", (marker_key,)).fetchone()
        if row and str(row[0]) == "1":
            return

    blob = db_get(old_key, {})
    if isinstance(blob, dict) and blob:
        LOG.info("Migrating legacy blob '%s' into scoped_store (%d scopes)", old_key, len(blob))
        for scope, value in blob.items():
            db_set_scoped(namespace, scope, value)

    with db_lock:
        try:
            conn.execute(
                "INSERT INTO migration_meta(key,value) VALUES(%s,%s) "
                "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                (marker_key, "1"),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


for _old_key, _namespace in (
    ("mood_state", "mood_state"),
    ("social_context", "social_context"),
    ("user_memory", "user_memory"),
):
    try:
        _migrate_blob_to_scoped(_old_key, _namespace)
    except Exception:
        LOG.exception("Scoped migration failed for %s -> %s (will retry next start)", _old_key, _namespace)


def db_claim_replay(replay_key, seen_at, ttl_seconds):
    """Atomically claim a Mini App replay key across all bot instances.

    Returns True when the key was not seen within the TTL, False for a replay.
    The operation is idempotent and uses a primary-key conflict instead of
    retrying an ambiguous write.
    """
    key = str(replay_key)[:256]
    now = float(seen_at)
    cutoff = now - float(ttl_seconds)
    with db_lock:
        try:
            conn.execute("DELETE FROM miniapp_replay WHERE seen_at < %s", (cutoff,))
            cur = conn.execute(
                "INSERT INTO miniapp_replay(replay_key,seen_at) VALUES(%s,%s) ON CONFLICT(replay_key) DO NOTHING RETURNING replay_key",
                (key, now),
            )
            claimed = cur.fetchone() is not None
            conn.commit()
            return claimed
        except Exception:
            conn.rollback()
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
    try:
        _pool.closeall()
    except Exception:
        pass
