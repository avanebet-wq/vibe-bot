# -*- coding: utf-8 -*-
"""Хранилище данных для меню настроек: капча, повторяющиеся публикации, удаление."""
import threading

from database import db_get, db_set, db_update_json

_lock = threading.RLock()

# 10 типов системных сообщений, которыми можно управлять в разделе удаления
SYSTEM_MESSAGE_TYPES = [
    ("join", "👋 Вступление в группу"),
    ("leave", "🚪 Выход из группы"),
    ("title", "✏️ Смена названия группы"),
    ("photo", "🖼 Смена фото группы"),
    ("delphoto", "🗑 Удаление фото группы"),
    ("pin", "📌 Закрепление сообщения"),
    ("vc_start", "🎥 Начало видеочата"),
    ("vc_end", "🎥 Завершение видеочата"),
    ("vc_scheduled", "🗓 Запланированный видеочат"),
    ("other", "🔧 Другие системные сообщения"),
]

WEEKDAYS = [("mon", "Пн"), ("tue", "Вт"), ("wed", "Ср"), ("thu", "Чт"),
            ("fri", "Пт"), ("sat", "Сб"), ("sun", "Вс")]

MAX_POSTS = 20


def _default_settings():
    return {
        "captcha": {"enabled": False},
        "posts": {},          # id(str) -> post dict
        "deletion": {
            "silence": False,
            "system": {key: False for key, _ in SYSTEM_MESSAGE_TYPES},
        },
        "liza": {
            "reply_mode": "everyone",  # everyone | mention | silent
            "autoactivity": False,
            "chatter_chance": 0.05,
            "stories": True,
            "memory": True,
            "minigames": True,
            "polite": False,
            "angry": False,
        },
    }


def _default_post():
    return {
        "enabled": False,
        "text": None,
        "media": None,        # {"type": "photo"/"video"/..., "file_id": ..., "caption": ...}
        "buttons": None,      # список рядов [{"text":.., "url"/"popup"/"alert"/"share"/"copy"/"rules":..}]
        "topic_id": None,
        "time": None,         # "HH:MM" — время старта публикаций
        "interval_seconds": None,   # интервал повторения
        "weekdays": [],       # если пусто — без ограничения по дням недели
        "monthdays": [],      # если пусто — без ограничения по дням месяца
        "auto_off_seconds": None,   # через сколько времени публикация сама выключится
        "auto_off_at": None,        # вычисленная метка времени выключения
        "start_date": None,   # "ДД.ММ.ГГГГ"
        "end_date": None,
        "pin": False,
        "delete_last": False,
        "delete_timer_seconds": None,
        "last_message_id": None,
        "last_chat_id": None,
        "next_run": None,
    }


def get_all_settings(gid):
    with _lock:
        store = db_get("group_settings", {})
        chat = store.get(str(gid))
        if chat is None:
            chat = _default_settings()
            store[str(gid)] = chat
            db_set("group_settings", store)
        else:
            # добиваем недостающие ключи, если структура была создана раньше
            defaults = _default_settings()
            changed = False
            for k, v in defaults.items():
                if k not in chat:
                    chat[k] = v
                    changed = True
            if "system" not in chat.get("deletion", {}):
                chat["deletion"]["system"] = {key: False for key, _ in SYSTEM_MESSAGE_TYPES}
                changed = True
            liza_defaults = defaults.get("liza", {})
            chat.setdefault("liza", {})
            for key, value in liza_defaults.items():
                if key not in chat["liza"]:
                    chat["liza"][key] = value
                    changed = True
            for key, _ in SYSTEM_MESSAGE_TYPES:
                if key not in chat["deletion"]["system"]:
                    chat["deletion"]["system"][key] = False
                    changed = True
            if changed:
                store[str(gid)] = chat
                db_set("group_settings", store)
        return chat


def save_all_settings(gid, chat):
    with _lock:
        def mutate(store):
            store[str(gid)] = chat
            return store
        db_update_json("group_settings", mutate, {})


def get_liza(gid):
    return get_all_settings(gid)["liza"]


def set_liza_value(gid, key, value):
    with _lock:
        chat = get_all_settings(gid)
        chat["liza"][key] = value
        save_all_settings(gid, chat)


def update_liza(gid, **fields):
    with _lock:
        chat = get_all_settings(gid)
        chat["liza"].update(fields)
        save_all_settings(gid, chat)
        return chat["liza"]


def get_captcha(gid):
    return get_all_settings(gid)["captcha"]


def set_captcha_enabled(gid, enabled):
    with _lock:
        chat = get_all_settings(gid)
        chat["captcha"]["enabled"] = bool(enabled)
        save_all_settings(gid, chat)


def get_deletion(gid):
    return get_all_settings(gid)["deletion"]


def set_silence(gid, enabled):
    with _lock:
        chat = get_all_settings(gid)
        chat["deletion"]["silence"] = bool(enabled)
        save_all_settings(gid, chat)


def toggle_system_message(gid, key):
    with _lock:
        chat = get_all_settings(gid)
        cur = chat["deletion"]["system"].get(key, False)
        chat["deletion"]["system"][key] = not cur
        save_all_settings(gid, chat)
        return not cur


def get_posts(gid):
    return get_all_settings(gid)["posts"]


def get_post(gid, pid):
    return get_all_settings(gid)["posts"].get(str(pid))


def add_post(gid):
    with _lock:
        chat = get_all_settings(gid)
        posts = chat["posts"]
        if len(posts) >= MAX_POSTS:
            return None
        n = 1
        while str(n) in posts:
            n += 1
        posts[str(n)] = _default_post()
        save_all_settings(gid, chat)
        return str(n)


def delete_post(gid, pid):
    with _lock:
        chat = get_all_settings(gid)
        chat["posts"].pop(str(pid), None)
        save_all_settings(gid, chat)


def update_post(gid, pid, **fields):
    with _lock:
        chat = get_all_settings(gid)
        post = chat["posts"].get(str(pid))
        if post is None:
            return None
        post.update(fields)
        save_all_settings(gid, chat)
        return post


def toggle_post_field(gid, pid, field):
    with _lock:
        chat = get_all_settings(gid)
        post = chat["posts"].get(str(pid))
        if post is None:
            return None
        post[field] = not post.get(field, False)
        save_all_settings(gid, chat)
        return post[field]


def toggle_post_day(gid, pid, list_field, day):
    with _lock:
        chat = get_all_settings(gid)
        post = chat["posts"].get(str(pid))
        if post is None:
            return None
        days = post.setdefault(list_field, [])
        if day in days:
            days.remove(day)
        else:
            days.append(day)
        save_all_settings(gid, chat)
        return days


# ---------------------------------------------------------------------------
# Реестр групп, в которых сейчас находится бот (для списка «выберите чат» в ЛС)
# ---------------------------------------------------------------------------

def get_known_groups():
    with _lock:
        return db_get("known_groups", {})


def register_known_group(gid, title):
    with _lock:
        def mutate(groups):
            groups[str(gid)] = title or str(gid)
            return groups
        db_update_json("known_groups", mutate, {})


def remove_known_group(gid):
    with _lock:
        def mutate(groups):
            groups.pop(str(gid), None)
            return groups
        db_update_json("known_groups", mutate, {})


# ---------------------------------------------------------------------------
# Состояния "ожидаю ввод от пользователя" (текст / медиа / кнопки / дата и т.п.)
# Ключ: (asker_chat_id, user_id) -> {"kind":.., "gid":.., "pid":.., ...}
# ---------------------------------------------------------------------------
_pending = {}
_pending_lock = threading.RLock()


def set_pending(chat_id, user_id, data):
    with _pending_lock:
        _pending[(chat_id, user_id)] = data


def get_pending(chat_id, user_id):
    with _pending_lock:
        return _pending.get((chat_id, user_id))


def clear_pending(chat_id, user_id):
    with _pending_lock:
        _pending.pop((chat_id, user_id), None)


# ---------------------------------------------------------------------------
# Какую группу сейчас настраивает пользователь в личке с ботом
# ---------------------------------------------------------------------------
_active_group = {}
_active_lock = threading.RLock()


def set_active_group(user_id, gid):
    with _active_lock:
        _active_group[user_id] = gid


def get_active_group(user_id):
    with _active_lock:
        return _active_group.get(user_id)


# ---------------------------------------------------------------------------
# Ожидающие подтверждения капчи: (gid, user_id) -> {"msg_chat":.., "msg_id":.., "joined_at":..}
# ---------------------------------------------------------------------------
_captcha_pending = {}
_captcha_lock = threading.RLock()


def set_captcha_pending(gid, user_id, data):
    with _captcha_lock:
        _captcha_pending[(gid, user_id)] = data


def get_captcha_pending(gid, user_id):
    with _captcha_lock:
        return _captcha_pending.get((gid, user_id))


def clear_captcha_pending(gid, user_id):
    with _captcha_lock:
        _captcha_pending.pop((gid, user_id), None)


def is_captcha_pending(gid, user_id):
    with _captcha_lock:
        return (gid, user_id) in _captcha_pending

# updated 2026-09-18
