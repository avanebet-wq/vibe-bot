# -*- coding: utf-8 -*-
"""Общие утилиты: настройки чата, парсинг длительности, поиск цели команды."""
import re
import time
import html
import logging

from runtime import bot
from database import db_get, db_set

# ---------- Настройки чата (простое key/value по чатам) ----------

def get_setting(cid, key, default=None):
    store = db_get("chat_settings", {})
    return store.get(str(cid), {}).get(key, default)


def set_setting(cid, key, value):
    store = db_get("chat_settings", {})
    chat = store.setdefault(str(cid), {})
    chat[key] = value
    db_set("chat_settings", store)


# ---------- Разбор длительности ("10м", "2ч", "1д", "навсегда") ----------

_UNITS = {
    "с": 1, "сек": 1, "s": 1,
    "м": 60, "мин": 60, "m": 60,
    "ч": 3600, "час": 3600, "h": 3600,
    "д": 86400, "дн": 86400, "d": 86400,
}


def parse_duration(time_str):
    """Возвращает (секунды, ok). 0 секунд = навсегда."""
    if not time_str:
        return 0, True
    t = time_str.strip().lower()
    if t in ("навсегда", "forever", "0"):
        return 0, True
    m = re.match(r'^(\d+)\s*([a-zA-Zа-яё]+)$', t)
    if not m:
        return 0, False
    value = int(m.group(1))
    unit = m.group(2)
    for prefix, mult in sorted(_UNITS.items(), key=lambda x: -len(x[0])):
        if unit.startswith(prefix):
            return value * mult, True
    return 0, False


def format_seconds(secs):
    if secs <= 0:
        return "навсегда"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes:
        parts.append(f"{minutes}м")
    return " ".join(parts) if parts else "меньше минуты"


# ---------- Извлечение цели команды (реплай / @username / id) ----------

def get_mention(user_id, name):
    name = html.escape(name or str(user_id))
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def remember_user(user):
    """Кэш username -> id, чтобы потом можно было банить/мутить по @username."""
    if not user or not user.username:
        return
    cache = db_get("known_users", {})
    cache[user.username.lower()] = {"id": user.id, "name": user.first_name or user.username}
    db_set("known_users", cache)


def _lookup_username(chat_id, username):
    username = username.lower()
    cache = db_get("known_users", {})
    hit = cache.get(username)
    if hit:
        return hit["id"], hit["name"]

    # Не встречали такого пользователя раньше — пробуем узнать через Telegram напрямую.
    try:
        admins = bot.get_chat_administrators(chat_id)
        for a in admins:
            if (a.user.username or "").lower() == username:
                remember_user(a.user)
                return a.user.id, (a.user.first_name or a.user.username)
    except Exception as e:
        logging.warning(f"[extract_target] admin lookup failed: {e}")

    try:
        chat_obj = bot.get_chat(f"@{username}")
        member = bot.get_chat_member(chat_id, chat_obj.id)
        if member and member.status not in ("left", "kicked"):
            remember_user(chat_obj)
            return chat_obj.id, (chat_obj.first_name or username)
    except Exception:
        pass

    return None, None


def extract_target(message, args_text):
    """
    Возвращает (target_id, target_name, remaining_text) или (None, None, remaining_text).
    Цель берётся из реплая, либо из первого @username / числового ID в args_text.
    """
    remaining = args_text.strip()

    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, (u.first_name or u.username or str(u.id)), remaining

    if not remaining:
        return None, None, remaining

    parts = remaining.split(maxsplit=1)
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if first.startswith("@"):
        target_id, target_name = _lookup_username(message.chat.id, first[1:])
        if target_id:
            return target_id, target_name, rest
        return None, first, rest

    if first.isdigit():
        return int(first), first, rest

    return None, None, remaining


def is_chat_admin(chat_id, user_id):
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


def is_protected(chat_id, user_id):
    """Создателя чата и саму Лизу трогать нельзя."""
    from runtime import BOT_ID
    if user_id == BOT_ID:
        return True
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status == "creator"
    except Exception:
        return False
