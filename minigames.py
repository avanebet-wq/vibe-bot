# -*- coding: utf-8 -*-
"""Мини-игры «Пыхнуть» и «Заварить» + накопительная статистика."""
import html
import logging
import time
import random
from collections import defaultdict

from database import conn, db_lock
from runtime import bot

LOG = logging.getLogger("minigames")
COOLDOWN = 60 * 60
XP_REWARDS = {
    "smoke": [(5, 0.55), (10, 0.25), (20, 0.12), (35, 0.06), (50, 0.02)],
    "coffee": [(8, 0.55), (15, 0.25), (25, 0.12), (40, 0.06), (60, 0.02)],
    "drink": [(10, 0.55), (20, 0.25), (35, 0.12), (55, 0.06), (80, 0.02)],
}
KINDS = ("smoke", "coffee", "drink")

# Результаты игры «Выпить». Мелкие множители имеют основной вес,
# а максимальные значения специально сделаны крайне редкими.
DRINK_RESULTS = [
    (0.5, 22.0),
    (0.7, 28.0),
    (0.9, 20.0),
    (1.0, 12.0),
    (1.2, 7.0),
    (1.5, 5.0),
    (2.0, 3.0),
    (2.5, 1.5),
    (3.0, 0.8),
    (4.0, 0.5),
    (5.0, 0.2),
]

# Только названия + ассоциирующийся фрукт/цвет. Описания вкусов намеренно не выводятся.
REVO_NAMES = (
    ("Revo Cherry", "🍒"),
    ("Revo Original", "🍊"),
    ("Revo Love Is", "🍓"),
    ("Revo Black", "🫐"),
    ("Revo Манго", "🥭"),
    ("Revo Grapefruit", "🍊"),
    ("Revo Pitahaya", "🍓"),
    ("Revo Shizandra", "🍋"),
    ("Revo Blue Ice", "🫐"),
)


def ensure_schema():
    with db_lock:
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS minigame_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT,
                    display_name TEXT,
                    kind TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    source_message_id BIGINT
                )"""
            )
            conn.execute("ALTER TABLE minigame_events ADD COLUMN IF NOT EXISTS source_message_id BIGINT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_minigame_events_chat_kind_time "
                "ON minigame_events(chat_id, kind, created_at)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_minigame_source_message "
                "ON minigame_events(chat_id, source_message_id, kind) WHERE source_message_id IS NOT NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_minigame_events_chat_user_kind_time "
                "ON minigame_events(chat_id, user_id, kind, created_at)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS drink_game_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT,
                    display_name TEXT,
                    revo_name TEXT NOT NULL,
                    fruit_emoji TEXT NOT NULL,
                    multiplier REAL NOT NULL,
                    volume_liters REAL NOT NULL,
                    created_at REAL NOT NULL,
                    source_message_id BIGINT
                )"""
            )
            conn.execute("ALTER TABLE drink_game_events ADD COLUMN IF NOT EXISTS source_message_id BIGINT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_drink_game_chat_user_time "
                "ON drink_game_events(chat_id, user_id, created_at)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_drink_source_message "
                "ON drink_game_events(chat_id, source_message_id) WHERE source_message_id IS NOT NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_drink_game_chat_time "
                "ON drink_game_events(chat_id, created_at)"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


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
        try:
            row = conn.execute(
            "SELECT created_at FROM minigame_events "
            "WHERE chat_id=? AND user_id=? AND kind=? "
            "ORDER BY created_at DESC LIMIT 1",
                (str(chat_id), str(user_id), kind),
            ).fetchone()
        except Exception:
            conn.rollback()
            raise
    return float(row[0]) if row else None


def _award_xp(chat_id, user_id, kind):
    choices = XP_REWARDS[kind]
    values = [x[0] for x in choices]
    weights = [x[1] for x in choices]
    base = random.choices(values, weights=weights, k=1)[0]
    multiplier = random.choices([0.8, 1.0, 1.5, 2.0, 3.0], weights=[20, 50, 20, 8, 2], k=1)[0]
    xp = int(round(base * multiplier))
    with db_lock:
        conn.execute("""CREATE TABLE IF NOT EXISTS profile_xp (
            chat_id TEXT NOT NULL, user_id TEXT NOT NULL, xp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(chat_id,user_id)
        )""")
        try:
            conn.execute(
                "INSERT INTO profile_xp(chat_id,user_id,xp) VALUES(?,?,?) "
                "ON CONFLICT(chat_id,user_id) DO UPDATE SET xp=profile_xp.xp+excluded.xp",
                (str(chat_id), str(user_id), xp),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return xp, multiplier


def get_xp(chat_id, user_id):
    with db_lock:
        try:
            row = conn.execute("SELECT xp FROM profile_xp WHERE chat_id=? AND user_id=?", (str(chat_id), str(user_id))).fetchone()
        except Exception:
            conn.rollback()
            raise
    return int(row[0]) if row else 0


def _record(chat_id, user_id, username, display_name, kind, now):
    with db_lock:
        try:
            conn.execute(
                "INSERT INTO minigame_events(chat_id,user_id,username,display_name,kind,created_at,source_message_id) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id, source_message_id, kind) DO NOTHING",
                (str(chat_id), str(user_id), username, display_name, kind, now, getattr(message, "message_id", None)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _count_all(chat_id, user_id, kind):
    with db_lock:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM minigame_events WHERE chat_id=? AND user_id=? AND kind=?",
                (str(chat_id), str(user_id), kind),
            ).fetchone()
        except Exception:
            conn.rollback()
            raise
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


def _xp_roll(kind):
    choices = XP_REWARDS[kind]
    values = [x[0] for x in choices]
    weights = [x[1] for x in choices]
    base = random.choices(values, weights=weights, k=1)[0]
    multiplier = random.choices([0.8, 1.0, 1.5, 2.0, 3.0], weights=[20, 50, 20, 8, 2], k=1)[0]
    return int(round(base * multiplier)), multiplier


def _award_xp_in_transaction(chat_id, user_id, kind):
    xp, multiplier = _xp_roll(kind)
    conn.execute("""CREATE TABLE IF NOT EXISTS profile_xp (
        chat_id TEXT NOT NULL, user_id TEXT NOT NULL, xp INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(chat_id,user_id)
    )""")
    conn.execute(
        "INSERT INTO profile_xp(chat_id,user_id,xp) VALUES(?,?,?) "
        "ON CONFLICT(chat_id,user_id) DO UPDATE SET xp=profile_xp.xp+excluded.xp",
        (str(chat_id), str(user_id), xp),
    )
    return xp, multiplier


def _try_use(message, kind):
    chat_id = message.chat.id
    user_id, username, display_name = _user_info(message)
    if user_id == "None":
        return None

    now = time.time()
    with db_lock:
        try:
            row = conn.execute(
                "SELECT created_at FROM minigame_events "
                "WHERE chat_id=? AND user_id=? AND kind=? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(chat_id), str(user_id), kind),
            ).fetchone()
            last = float(row[0]) if row else None
            if last is not None:
                remaining = COOLDOWN - (now - last)
                if remaining > 0:
                    return remaining, None, None, None

            cur = conn.execute(
                "INSERT INTO minigame_events(chat_id,user_id,username,display_name,kind,created_at,source_message_id) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id, source_message_id, kind) DO NOTHING RETURNING id",
                (str(chat_id), str(user_id), username, display_name, kind, now, getattr(message, "message_id", None)),
            )
            if cur.fetchone() is None:
                # Telegram redelivery of the same update: the first transaction
                # already recorded it, so do not award XP a second time.
                conn.rollback()
                return COOLDOWN, None, None, None
            total = int(conn.execute(
                "SELECT COUNT(*) FROM minigame_events WHERE chat_id=? AND user_id=? AND kind=?",
                (str(chat_id), str(user_id), kind),
            ).fetchone()[0])
            xp, xp_mult = _award_xp_in_transaction(chat_id, user_id, kind)
            conn.commit()
            return 0, total, xp, xp_mult
        except Exception:
            conn.rollback()
            raise


def _random_drink_result():
    values = [x[0] for x in DRINK_RESULTS]
    weights = [x[1] for x in DRINK_RESULTS]
    multiplier = random.choices(values, weights=weights, k=1)[0]
    revo_name, fruit_emoji = random.choice(REVO_NAMES)
    # Формула подобрана под заявленный пример: 1.5x = 2.5 л.
    volume_liters = round(1.0 + multiplier, 1)
    return revo_name, fruit_emoji, multiplier, volume_liters


def _record_drink(chat_id, user_id, username, display_name, revo_name, fruit_emoji, multiplier, volume_liters, now):
    with db_lock:
        try:
            conn.execute(
                "INSERT INTO drink_game_events "
                "(chat_id,user_id,username,display_name,revo_name,fruit_emoji,multiplier,volume_liters,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (str(chat_id), str(user_id), username, display_name, revo_name, fruit_emoji,
                 float(multiplier), float(volume_liters), now),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def cmd_drink(message):
    """Игра «Выпить»: КД 1 час, случайный Revo, множитель и XP."""
    try:
        chat_id = message.chat.id
        user_id, username, display_name = _user_info(message)
        if user_id == "None":
            return
        now = time.time()
        with db_lock:
            try:
                row = conn.execute(
                    "SELECT created_at FROM minigame_events "
                    "WHERE chat_id=? AND user_id=? AND kind=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (str(chat_id), str(user_id), "drink"),
                ).fetchone()
                last = float(row[0]) if row else None
                if last is not None:
                    remaining = COOLDOWN - (now - last)
                    if remaining > 0:
                        bot.reply_to(message, f"🕐 Следующая попытка через: <b>{_format_wait(remaining)}</b>", parse_mode="HTML")
                        return

                revo_name, fruit_emoji, multiplier, volume_liters = _random_drink_result()
                cur = conn.execute(
                    "INSERT INTO drink_game_events "
                    "(chat_id,user_id,username,display_name,revo_name,fruit_emoji,multiplier,volume_liters,created_at,source_message_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chat_id, source_message_id) DO NOTHING RETURNING id",
                    (str(chat_id), str(user_id), username, display_name, revo_name, fruit_emoji,
                     float(multiplier), float(volume_liters), now, getattr(message, "message_id", None)),
                )
                if cur.fetchone() is None:
                    conn.rollback()
                    bot.reply_to(message, "🕐 Этот запрос уже был обработан.")
                    return
                conn.execute(
                    "INSERT INTO minigame_events(chat_id,user_id,username,display_name,kind,created_at,source_message_id) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(chat_id, source_message_id, kind) DO NOTHING",
                    (str(chat_id), str(user_id), username, display_name, "drink", now, getattr(message, "message_id", None)),
                )
                total = int(conn.execute(
                    "SELECT COUNT(*) FROM minigame_events WHERE chat_id=? AND user_id=? AND kind=?",
                    (str(chat_id), str(user_id), "drink"),
                ).fetchone()[0])
                xp, xp_mult = _award_xp_in_transaction(chat_id, user_id, "drink")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        bot.reply_to(message,
            f"🥤 Вы выпили <b>{html.escape(revo_name)}</b> {fruit_emoji}\n"
            f"🎲 Вам выпал множитель <b>{multiplier:g}х</b> = <b>{volume_liters:g} л Рево</b>\n"
            f"⭐ Опыт: <b>+{xp} XP</b> (×{xp_mult:g})\n"
            f"🕐 Следующая попытка: через <b>{_format_wait(COOLDOWN)}</b>", parse_mode="HTML")
    except Exception:
        LOG.exception("drink command failed")

def cmd_smoke(message):
    try:
        result = _try_use(message, "smoke")
        if result is None:
            return
        remaining, total, xp, xp_mult = result
        if remaining > 0:
            bot.reply_to(message, f"🕐 Следующая попытка через: {_format_wait(remaining)}")
            return
        bot.reply_to(
            message,
            f"Вы пыхнули сигаретку 🚬 | Вы уже скурили: <b>{total}</b> сигарет\n"
            f"⭐ Опыт: <b>+{xp} XP</b> (×{xp_mult:g})\n"
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
        remaining, total, xp, xp_mult = result
        if remaining > 0:
            bot.reply_to(message, f"🕐 Следующая попытка через: {_format_wait(remaining)}")
            return
        bot.reply_to(
            message,
            f"Вы заварили и выпили ароматный бодрящий кофе ☕ | Вы выпили чашек: <b>{total}</b>\n"
            f"⭐ Опыт: <b>+{xp} XP</b> (×{xp_mult:g})\n"
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
        try:
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
        except Exception:
            conn.rollback()
            raise


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


def _drink_rows(chat_id, days=None):
    params = [str(chat_id)]
    where = "WHERE chat_id=?"
    if days is not None:
        where += " AND created_at>=?"
        params.append(time.time() - days * 86400)
    with db_lock:
        try:
            return conn.execute(
                f"""SELECT user_id,
                       COALESCE(NULLIF(username,''), ''),
                       COALESCE(NULLIF(display_name,''), ''),
                       COUNT(*) AS total,
                       ROUND(SUM(volume_liters)::numeric, 1) AS liters,
                       MAX(created_at) AS last_at
                FROM drink_game_events {where}
                GROUP BY user_id
                ORDER BY total DESC, last_at ASC""",
                params,
            ).fetchall()
        except Exception:
            conn.rollback()
            raise


def _drink_table(title, rows):
    lines = [f"🥤 <b>{html.escape(title)}</b>"]
    if not rows:
        lines.append("— пока нет данных")
        return lines
    lines.append("<pre>№  Пользователь                 Игры   Литры</pre>")
    for idx, (uid, username, display_name, total, liters, _last) in enumerate(rows[:50], 1):
        label = html.escape(_person_label(username, display_name, uid))
        if len(label) > 25:
            label = label[:22] + "..."
        lines.append(f"<code>{idx:>2}. {label:<25} {int(total):>4}   {float(liters or 0):>5.1f}</code>")
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
        drink = _drink_rows(message.chat.id, days)
        lines = [f"📊 <b>Стата мини-игр — {label}</b>", ""]
        lines.extend(_table("Сигареты", smoke, "🚬"))
        lines.append("")
        lines.extend(_table("Кофе", coffee, "☕"))
        lines.append("")
        lines.extend(_drink_table("Выпить", drink))
        text = "\n".join(lines)
        if len(text) <= 4000:
            bot.reply_to(message, text, parse_mode="HTML")
        else:
            # Telegram ограничивает текст сообщения; таблицы режем безопасно.
            bot.reply_to(message, text[:4000], parse_mode="HTML")
    except Exception:
        LOG.exception("minigame stats failed")
