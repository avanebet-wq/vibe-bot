# -*- coding: utf-8 -*-
"""Мини-игры «Пыхнуть» и «Заварить» + накопительная статистика."""
import html
import logging
import time
from collections import defaultdict

from database import conn, db_lock
from runtime import bot

LOG = logging.getLogger("minigames")
COOLDOWN = 60 * 60
KINDS = ("smoke", "coffee")


def ensure_schema():
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS minigame_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT,
                display_name TEXT,
                kind TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_minigame_events_chat_kind_time "
            "ON minigame_events(chat_id, kind, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_minigame_events_chat_user_kind_time "
            "ON minigame_events(chat_id, user_id, kind, created_at)"
        )
        conn.commit()


def _user_info(message):
    user = getattr(message, "from_user", None)
    uid = getattr(user, "id", None)
    username = (getattr(user, "username", None) or "").strip()
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    display = " ".join(x for x in (first, last) if x).strip() or username or str(uid)
    return str(uid), username, display


def _last_use(chat_id, user_id, kind):
    with db_lock:
        row = conn.execute(
            "SELECT created_at FROM minigame_events "
            "WHERE chat_id=? AND user_id=? AND kind=? "
            "ORDER BY created_at DESC LIMIT 1",
            (str(chat_id), str(user_id), kind),
        ).fetchone()
    return float(row[0]) if row else None


def _record(chat_id, user_id, username, display_name, kind, now):
    with db_lock:
        conn.execute(
            "INSERT INTO minigame_events(chat_id,user_id,username,display_name,kind,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (str(chat_id), str(user_id), username, display_name, kind, now),
        )
        conn.commit()


def _count_all(chat_id, user_id, kind):
    with db_lock:
        row = conn.execute(
            "SELECT COUNT(*) FROM minigame_events WHERE chat_id=? AND user_id=? AND kind=?",
            (str(chat_id), str(user_id), kind),
        ).fetchone()
    return int(row[0] or 0)


def _format_wait(seconds):
    seconds = max(0, int(seconds + 0.999))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if secs or not parts:
        parts.append(f"{secs} сек")
    return " ".join(parts)


def _try_use(message, kind):
    ensure_schema()
    chat_id = message.chat.id
    user_id, username, display_name = _user_info(message)
    if user_id == "None":
        return None

    now = time.time()
    last = _last_use(chat_id, user_id, kind)
    if last is not None:
        remaining = COOLDOWN - (now - last)
        if remaining > 0:
            return remaining, None

    _record(chat_id, user_id, username, display_name, kind, now)
    total = _count_all(chat_id, user_id, kind)
    return 0, total


def cmd_smoke(message):
    try:
        result = _try_use(message, "smoke")
        if result is None:
            return
        remaining, total = result
        if remaining > 0:
            bot.reply_to(message, f"🕐 Следующая попытка через: {_format_wait(remaining)}")
            return
        bot.reply_to(
            message,
            f"Вы пыхнули сигаретку 🚬 | Вы уже скурили: <b>{total}</b> сигарет\n"
            f"🕐 Следующая попытка: через <b>{_format_wait(COOLDOWN)}</b>",
            parse_mode="HTML",
        )
    except Exception:
        LOG.exception("smoke command failed")


def cmd_coffee(message):
    try:
        result = _try_use(message, "coffee")
        if result is None:
            return
        remaining, total = result
        if remaining > 0:
            bot.reply_to(message, f"🕐 Следующая попытка через: {_format_wait(remaining)}")
            return
        bot.reply_to(
            message,
            f"Вы заварили и выпили ароматный бодрящий кофе ☕ | Вы выпили чашек: <b>{total}</b>\n"
            f"🕐 Следующая попытка через: <b>{_format_wait(COOLDOWN)}</b>",
            parse_mode="HTML",
        )
    except Exception:
        LOG.exception("coffee command failed")


def _period_days(args):
    raw = (args or "").strip().lower()
    if not raw or raw == "вся":
        return None
    first = raw.split()[0]
    try:
        value = int(first)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _period_label(args):
    raw = (args or "").strip().lower()
    if raw == "вся":
        return "за всё время"
    days = _period_days(args)
    if days is None:
        return None
    return f"за {days} дн." if days != 1 else "за 1 день"


def _rows(chat_id, kind, days=None):
    params = [str(chat_id), kind]
    where = "WHERE chat_id=? AND kind=?"
    if days is not None:
        where += " AND created_at>=?"
        params.append(time.time() - days * 86400)
    with db_lock:
        return conn.execute(
            f"""SELECT user_id,
                       COALESCE(NULLIF(username,''), ''),
                       COALESCE(NULLIF(display_name,''), ''),
                       COUNT(*) AS total,
                       MAX(created_at) AS last_at
                FROM minigame_events {where}
                GROUP BY user_id
                ORDER BY total DESC, last_at ASC""",
            params,
        ).fetchall()


def _person_label(username, display_name, user_id):
    if username:
        return "@" + username.lstrip("@")
    if display_name:
        return display_name
    return f"ID {user_id}"


def _table(title, rows, emoji):
    lines = [f"{emoji} <b>{html.escape(title)}</b>"]
    if not rows:
        lines.append("— пока нет данных")
        return lines
    lines.append("<pre>№  Пользователь                 Кол-во</pre>")
    for idx, (uid, username, display_name, total, _last) in enumerate(rows[:50], 1):
        label = html.escape(_person_label(username, display_name, uid))
        if len(label) > 25:
            label = label[:22] + "..."
        lines.append(f"<code>{idx:>2}. {label:<25} {int(total):>6}</code>")
    if len(rows) > 50:
        lines.append(f"… и ещё {len(rows) - 50} пользователей")
    return lines


def cmd_stats(message, args=""):
    try:
        label = _period_label(args)
        if label is None:
            bot.reply_to(message, "⚠️ Формат: <code>стата 7</code>, <code>стата 3 дня</code> или <code>стата вся</code>", parse_mode="HTML")
            return
        days = _period_days(args)
        smoke = _rows(message.chat.id, "smoke", days)
        coffee = _rows(message.chat.id, "coffee", days)
        lines = [f"📊 <b>Стата мини-игр — {label}</b>", ""]
        lines.extend(_table("Сигареты", smoke, "🚬"))
        lines.append("")
        lines.extend(_table("Кофе", coffee, "☕"))
        text = "\n".join(lines)
        if len(text) <= 4000:
            bot.reply_to(message, text, parse_mode="HTML")
        else:
            # Telegram ограничивает текст сообщения; таблицы режем безопасно.
            bot.reply_to(message, text[:4000], parse_mode="HTML")
    except Exception:
        LOG.exception("minigame stats failed")
