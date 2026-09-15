# -*- coding: utf-8 -*-
"""Social relationship system: friendship requests, levels, XP and RP actions."""
from __future__ import annotations

import html
import logging
import secrets
import time
from datetime import datetime

from telebot import types

from config import TZ
from database import db_get, db_update_json
from utils import extract_target, get_mention

MAX_LEVEL = 40
DAILY_XP_CAP = 100
REQUEST_TTL = 48 * 3600

# One shared relationship = one record for two users.
# Actions are deliberately neutral/social, not romantic.
ACTIONS = (
    {"name": "поздороваться", "emoji": "👋", "xp": 2, "cooldown": 10 * 60, "level": 1, "text": "поздоровался с"},
    {"name": "дать пять", "emoji": "✋", "xp": 3, "cooldown": 15 * 60, "level": 1, "text": "дал пять"},
    {"name": "поблагодарить", "emoji": "🙏", "xp": 3, "cooldown": 15 * 60, "level": 2, "text": "поблагодарил"},
    {"name": "похвалить", "emoji": "👍", "xp": 4, "cooldown": 20 * 60, "level": 3, "text": "похвалил"},
    {"name": "поддержать", "emoji": "💚", "xp": 5, "cooldown": 25 * 60, "level": 4, "text": "поддержал"},
    {"name": "подбодрить", "emoji": "💪", "xp": 5, "cooldown": 25 * 60, "level": 4, "text": "подбодрил"},
    {"name": "помочь", "emoji": "🤝", "xp": 6, "cooldown": 30 * 60, "level": 5, "text": "помог"},
    {"name": "угостить", "emoji": "🍕", "xp": 7, "cooldown": 40 * 60, "level": 6, "text": "угостил"},
    {"name": "подколоть", "emoji": "😏", "xp": 6, "cooldown": 45 * 60, "level": 7, "text": "подколол"},
    {"name": "рассмешить", "emoji": "😂", "xp": 8, "cooldown": 60 * 60, "level": 8, "text": "попытался рассмешить"},
    {"name": "выручить", "emoji": "🫡", "xp": 9, "cooldown": 75 * 60, "level": 10, "text": "выручил"},
    {"name": "подарить", "emoji": "🎁", "xp": 10, "cooldown": 2 * 3600, "level": 12, "text": "подарил подарок"},
    {"name": "поиграть", "emoji": "🎮", "xp": 11, "cooldown": 2 * 3600, "level": 14, "text": "предложил сыграть"},
    {"name": "позвать в приключение", "emoji": "🗺️", "xp": 12, "cooldown": 3 * 3600, "level": 16, "text": "позвал в приключение"},
    {"name": "защитить", "emoji": "🛡️", "xp": 14, "cooldown": 4 * 3600, "level": 18, "text": "встал на защиту"},
    {"name": "устроить прикол", "emoji": "🤣", "xp": 15, "cooldown": 5 * 3600, "level": 20, "text": "устроил прикол для"},
    {"name": "выручить по-крупному", "emoji": "🚀", "xp": 17, "cooldown": 6 * 3600, "level": 23, "text": "серьёзно выручил"},
    {"name": "совместный челлендж", "emoji": "🏆", "xp": 20, "cooldown": 8 * 3600, "level": 26, "text": "вызвал на совместный челлендж"},
    {"name": "особая поддержка", "emoji": "⭐", "xp": 22, "cooldown": 10 * 3600, "level": 30, "text": "оказал особую поддержку"},
    {"name": "легендарный прикол", "emoji": "💥", "xp": 25, "cooldown": 12 * 3600, "level": 35, "text": "устроил легендарный прикол для"},
    {"name": "легендарная выручка", "emoji": "👑", "xp": 30, "cooldown": 24 * 3600, "level": 40, "text": "легендарно выручил"},
)
ACTION_MAP = {x["name"]: x for x in ACTIONS}


def _key(a, b):
    a, b = str(a), str(b)
    return f"{min(a, b)}:{max(a, b)}"


def _xp_to_level(xp):
    """Cumulative curve: level 1 starts at 0 XP; max takes a long time."""
    xp = max(0, int(xp or 0))
    level = 1
    # Required XP grows by 20 each level: 100, 120, 140 ...
    for next_level in range(2, MAX_LEVEL + 1):
        need = 100 + (next_level - 2) * 20
        if xp < _xp_for_level(next_level):
            break
        level = next_level
    return min(MAX_LEVEL, level)


def _xp_for_level(level):
    level = max(1, min(MAX_LEVEL, int(level)))
    # XP needed to reach level N from level 1.
    # 100 + 120 + 140 ...
    n = level - 1
    return n * 100 + 10 * n * (n - 1)


def _level_title(level):
    titles = {
        1: "Знакомые", 2: "Приятели", 3: "Друзья", 5: "Хорошие друзья",
        10: "Близкие друзья", 15: "Надёжные друзья", 20: "Лучшие друзья",
        25: "Проверенные друзья", 30: "Сильная дружба", 35: "Неразлучные друзья",
        40: "Легендарная дружба",
    }
    best = max(k for k in titles if k <= level)
    return titles[best]


def _chat(store, chat_id):
    return store.setdefault(str(chat_id), {})


def _display_user(user_id, fallback="Пользователь"):
    try:
        return get_mention(user_id, fallback)
    except Exception:
        return html.escape(fallback)


def _clean_old_requests(chat):
    now = time.time()
    requests = chat.setdefault("relationship_requests", {})
    for token, req in list(requests.items()):
        if now - float(req.get("created_at", 0)) > REQUEST_TTL:
            requests.pop(token, None)


def _get_relationship(chat_id, user_a, user_b):
    store = db_get("stats", {}) or {}
    chat = store.get(str(chat_id), {}) or {}
    rel = (chat.get("relationships", {}) or {}).get(_key(user_a, user_b))
    return rel.copy() if isinstance(rel, dict) else None


def _is_friends(rel):
    return bool(rel and rel.get("status") == "active")


def _ensure_relation_defaults(rel, user_a, user_b):
    rel.setdefault("user_a", str(user_a))
    rel.setdefault("user_b", str(user_b))
    rel.setdefault("status", "active")
    rel.setdefault("xp", 0)
    rel.setdefault("level", 1)
    rel.setdefault("created_at", time.time())
    rel.setdefault("updated_at", time.time())
    rel.setdefault("cooldowns", {})
    rel.setdefault("action_counts", {})
    rel.setdefault("daily_xp", {"date": datetime.now(TZ).strftime("%Y-%m-%d"), "xp": 0})
    rel.setdefault("history", [])
    return rel


def create_request(message):
    if message.chat.type not in ("group", "supergroup"):
        return False
    actor = getattr(message, "from_user", None)
    if not actor or getattr(actor, "is_bot", False):
        return True
    target_id, target_name, _ = extract_target(message, "")
    if not target_id:
        # For +отн @user the dispatcher passes the args separately; this branch
        # is used only if called directly without args.
        bot_reply(message, "🤝 Укажи участника: <code>+отн @username</code> или ответь этой командой на его сообщение.")
        return True
    return _create_request_to(message, actor.id, target_id, target_name)


def _create_request_to(message, actor_id, target_id, target_name=None):
    if str(actor_id) == str(target_id):
        bot_reply(message, "😄 Нельзя предложить дружбу самому себе.")
        return True
    rel = _get_relationship(message.chat.id, actor_id, target_id)
    if _is_friends(rel):
        bot_reply(message, "🤝 Вы уже друзья.")
        return True

    store_result = {"token": None, "message_id": None, "already": False}
    def mutate(store):
        chat = _chat(store, message.chat.id)
        _clean_old_requests(chat)
        requests = chat.setdefault("relationship_requests", {})
        for req in requests.values():
            if req.get("from_id") == str(actor_id) and req.get("to_id") == str(target_id):
                store_result["token"] = next(k for k, v in requests.items() if v is req)
                store_result["already"] = True
                return store
        token = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
        requests[token] = {
            "from_id": str(actor_id), "to_id": str(target_id),
            "from_name": getattr(message.from_user, "first_name", None) or "Пользователь",
            "to_name": target_name or "Пользователь", "created_at": time.time(),
        }
        store_result["token"] = token
        return store
    db_update_json("stats", mutate, {})
    if store_result["already"]:
        bot_reply(message, "⏳ Ты уже отправлял(а) этому участнику предложение дружбы.")
        return True

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🤝 Принять", callback_data=f"rel|accept|{message.chat.id}|{actor_id}|{target_id}|{store_result['token']}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rel|decline|{message.chat.id}|{actor_id}|{target_id}|{store_result['token']}"),
    )
    sender = _display_user(actor_id, getattr(message.from_user, "first_name", None) or "Пользователь")
    text = f"🤝 <b>Новое предложение дружбы!</b>\n\n{sender} предлагает вам начать дружбу.\n\nВыберите действие ниже."
    try:
        sent = message.bot.send_message(target_id, text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        bot_reply(message, "📩 Не получилось отправить предложение в личку этому участнику. Пусть он сначала откроет чат с Лизой и напишет ей <code>старт</code>.")
        # Keep the request so it can be retried/handled later only if DM becomes available.
        return True
    def save_message(store):
        chat = _chat(store, message.chat.id)
        req = chat.setdefault("relationship_requests", {}).get(store_result["token"])
        if req:
            req["message_id"] = getattr(sent, "message_id", None)
        return store
    db_update_json("stats", save_message, {})
    bot_reply(message, "📩 Предложение дружбы отправлено.")
    return True


def create_request_command(message, args):
    if message.chat.type not in ("group", "supergroup"):
        bot_reply(message, "🤝 Дружбу можно создавать только в группе.")
        return
    target_id, target_name, remaining = extract_target(message, args or "")
    if remaining and not target_id and not message.reply_to_message:
        bot_reply(message, "🤝 Формат: <code>+отн @username</code> или ответь <code>+отн</code> на сообщение участника.")
        return
    if not target_id:
        bot_reply(message, "🤝 Укажи участника: <code>+отн @username</code> или ответь этой командой на его сообщение.")
        return
    _create_request_to(message, message.from_user.id, target_id, target_name)


def _finish_request(call, accepted):
    parts = call.data.split("|")
    if len(parts) != 6:
        return
    _, action, chat_id, from_id, to_id, token = parts
    actor_id = getattr(call.from_user, "id", None)
    if str(actor_id) != str(to_id):
        call.bot.answer_callback_query(call.id, "Это предложение предназначено другому участнику.", show_alert=True)
        return
    result = {"ok": False, "from_name": "Пользователь", "to_name": "Пользователь", "level": 1}
    def mutate(store):
        chat = _chat(store, chat_id)
        requests = chat.setdefault("relationship_requests", {})
        req = requests.get(token)
        if not req or str(req.get("from_id")) != str(from_id) or str(req.get("to_id")) != str(to_id):
            return store
        result["from_name"] = req.get("from_name") or "Пользователь"
        result["to_name"] = req.get("to_name") or "Пользователь"
        if accepted:
            key = _key(from_id, to_id)
            rels = chat.setdefault("relationships", {})
            existing = rels.get(key)
            if existing and existing.get("status") == "ended":
                # Preserve progress when friendship is restored.
                rel = existing
                rel["status"] = "active"
                rel["updated_at"] = time.time()
            else:
                rel = _ensure_relation_defaults({}, from_id, to_id)
                rels[key] = rel
            result["level"] = int(rel.get("level", 1))
            result["ok"] = True
        requests.pop(token, None)
        return store
    db_update_json("stats", mutate, {})
    if not result["ok"]:
        call.bot.answer_callback_query(call.id, "Предложение уже недействительно.", show_alert=True)
        try:
            call.message.edit_text("⌛ <b>Предложение дружбы недействительно.</b>", parse_mode="HTML")
        except Exception:
            pass
        return
    if accepted:
        text = (
            "🎉 <b>Дружба началась!</b>\n\n"
            f"{_display_user(from_id, result['from_name'])} и {_display_user(to_id, result['to_name'])} теперь друзья. 🤝\n\n"
            f"⭐ Уровень отношений: <b>{result['level']}</b>\n"
            "📈 Развивайте дружбу совместными действиями."
        )
        call.bot.answer_callback_query(call.id, "Дружба создана! 🤝")
    else:
        text = f"❌ <b>Предложение отклонено.</b>\n\n{_display_user(to_id, result['to_name'])} отклонил(а) предложение дружбы от {_display_user(from_id, result['from_name'])}."
        call.bot.answer_callback_query(call.id, "Предложение отклонено.")
    try:
        call.message.edit_text(text, parse_mode="HTML")
    except Exception:
        logging.exception("[relationships] failed to edit request message")
    if accepted:
        try:
            call.bot.send_message(int(chat_id), text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass


def _relationship_list(chat_id, user_id):
    store = db_get("stats", {}) or {}
    chat = store.get(str(chat_id), {}) or {}
    rels = chat.get("relationships", {}) or {}
    out = []
    for rel in rels.values():
        if rel.get("status") != "active":
            continue
        if str(user_id) not in (str(rel.get("user_a")), str(rel.get("user_b"))):
            continue
        other = rel.get("user_b") if str(rel.get("user_a")) == str(user_id) else rel.get("user_a")
        out.append((other, rel))
    out.sort(key=lambda x: (-int(x[1].get("level", 1)), -int(x[1].get("xp", 0))))
    return out


def _main_user(chat_id, user_id):
    store = db_get("stats", {}) or {}
    chat = store.get(str(chat_id), {}) or {}
    return (chat.get("relationship_main", {}) or {}).get(str(user_id))


def set_main(message, args):
    target_id, target_name, _ = extract_target(message, args or "")
    if not target_id:
        bot_reply(message, "⭐ Формат: <code>отн основа @username</code> или ответь <code>отн основа</code> на сообщение друга.")
        return
    rel = _get_relationship(message.chat.id, message.from_user.id, target_id)
    if not _is_friends(rel):
        bot_reply(message, "⚠️ Основным можно назначить только участника, с которым у тебя есть активная дружба.")
        return
    def mutate(store):
        chat = _chat(store, message.chat.id)
        chat.setdefault("relationship_main", {})[str(message.from_user.id)] = str(target_id)
        return store
    db_update_json("stats", mutate, {})
    bot_reply(message, f"⭐ Основные отношения установлены: {_display_user(target_id, target_name or 'Пользователь')}.")


def remove_main(message):
    def mutate(store):
        chat = _chat(store, message.chat.id)
        chat.setdefault("relationship_main", {}).pop(str(message.from_user.id), None)
        return store
    db_update_json("stats", mutate, {})
    bot_reply(message, "🗑️ Основные отношения удалены.")


def end_relationship(message, args):
    target_id, target_name, _ = extract_target(message, args or "")
    if not target_id:
        bot_reply(message, "💔 Укажи друга: <code>-отн @username</code> или ответь <code>-отн</code> на его сообщение.")
        return
    rel = _get_relationship(message.chat.id, message.from_user.id, target_id)
    if not _is_friends(rel):
        bot_reply(message, "⚠️ Активной дружбы с этим участником нет.")
        return
    kb = types.InlineKeyboardMarkup()
    token = secrets.token_hex(6)
    # Confirmation is kept in the relationship record to avoid another store key.
    def mutate(store):
        chat = _chat(store, message.chat.id)
        chat.setdefault("relationship_confirmations", {})[token] = {
            "user": str(message.from_user.id), "target": str(target_id), "created_at": time.time()
        }
        return store
    db_update_json("stats", mutate, {})
    kb.row(
        types.InlineKeyboardButton("💔 Расторгнуть", callback_data=f"rel|end|{message.chat.id}|{message.from_user.id}|{target_id}|{token}"),
        types.InlineKeyboardButton("↩️ Отмена", callback_data=f"rel|cancel_end|{message.chat.id}|{message.from_user.id}|{target_id}|{token}"),
    )
    bot_reply(message, f"💔 <b>Расторгнуть дружбу с {_display_user(target_id, target_name or 'Пользователь')}?</b>\n\nПрогресс отношений будет сохранён на случай восстановления.\n\nПодтверди действие:", reply_markup=kb)


def _confirm_end(call, accepted):
    parts = call.data.split("|")
    _, action, chat_id, user_id, target_id, token = parts
    if str(call.from_user.id) != str(user_id):
        call.bot.answer_callback_query(call.id, "Подтверждение доступно только автору команды.", show_alert=True)
        return
    result = {"ok": False, "name": "Пользователь"}
    def mutate(store):
        chat = _chat(store, chat_id)
        confirms = chat.setdefault("relationship_confirmations", {})
        item = confirms.get(token)
        if not item or time.time() - float(item.get("created_at", 0)) > 10 * 60:
            confirms.pop(token, None)
            return store
        if accepted:
            rel = chat.setdefault("relationships", {}).get(_key(user_id, target_id))
            if rel and rel.get("status") == "active":
                rel["status"] = "ended"
                rel["ended_at"] = time.time()
                rel["updated_at"] = time.time()
                result["ok"] = True
                result["name"] = rel.get("target_name") or "Пользователь"
            main = chat.setdefault("relationship_main", {})
            if main.get(str(user_id)) == str(target_id):
                main.pop(str(user_id), None)
            if main.get(str(target_id)) == str(user_id):
                main.pop(str(target_id), None)
        confirms.pop(token, None)
        return store
    db_update_json("stats", mutate, {})
    call.bot.answer_callback_query(call.id, "Готово." if accepted else "Отмена.")
    text = "💔 Дружба завершена. Прогресс сохранён, его можно восстановить при новом предложении." if accepted and result["ok"] else "↩️ Расторжение отменено."
    try:
        call.message.edit_text(text)
    except Exception:
        pass


def show_relationship(message, args):
    target_id, target_name, remaining = extract_target(message, args or "")
    if not target_id:
        target_id = _main_user(message.chat.id, message.from_user.id)
        if not target_id:
            rels = _relationship_list(message.chat.id, message.from_user.id)
            if not rels:
                bot_reply(message, "🤝 У тебя пока нет активных дружеских отношений.\n\nНачать: <code>+отн @username</code> или ответом на сообщение участника.")
                return
            lines = ["🤝 <b>Твои отношения</b>"]
            for uid, rel in rels[:15]:
                lines.append(f"• {_display_user(uid)} — <b>{rel.get('level', 1)} ур.</b>, {rel.get('xp', 0)} XP")
            bot_reply(message, "\n".join(lines), disable_web_page_preview=True)
            return
    rel = _get_relationship(message.chat.id, message.from_user.id, target_id)
    if not _is_friends(rel):
        bot_reply(message, "⚠️ С этим участником нет активной дружбы.")
        return
    level = int(rel.get("level", 1))
    xp = int(rel.get("xp", 0))
    next_xp = _xp_for_level(level + 1) if level < MAX_LEVEL else xp
    current_floor = _xp_for_level(level)
    progress = max(0, xp - current_floor)
    need = max(1, next_xp - current_floor)
    main = _main_user(message.chat.id, message.from_user.id)
    marker = " ⭐ основа" if str(main) == str(target_id) else ""
    text = (
        f"🤝 <b>Отношения с {_display_user(target_id, target_name or 'Пользователь')}</b>{marker}\n\n"
        f"🏷️ {html.escape(_level_title(level))}\n"
        f"⭐ Уровень: <b>{level}/{MAX_LEVEL}</b>\n"
        f"📈 Опыт: <b>{xp}</b> XP\n"
        f"📊 До следующего уровня: <b>{max(0, next_xp - xp)}</b> XP\n"
        f"🎭 Действий выполнено: <b>{sum(int(v) for v in (rel.get('action_counts') or {}).values())}</b>"
    )
    if level < MAX_LEVEL:
        text += f"\n\nПрогресс: <b>{progress}/{need}</b>"
    else:
        text += "\n\n👑 Максимальный уровень достигнут."
    bot_reply(message, text, disable_web_page_preview=True)


def actions_dm(message):
    if message.chat.type != "private":
        try:
            send_actions_to_user(message, message.from_user.id, message.chat.id)
            bot_reply(message, "📩 Список действий отправила тебе в личку.")
        except Exception:
            bot_reply(message, "📩 Не могу написать тебе в личку. Сначала открой чат с Лизой и напиши ей <code>старт</code>, затем повтори <code>отн действия</code>.")
        return
    send_actions_to_user(message, message.from_user.id, None)


def _fmt_cd(seconds):
    seconds = int(seconds)
    if seconds % 3600 == 0 and seconds >= 3600:
        return f"{seconds // 3600}ч"
    if seconds % 60 == 0:
        return f"{seconds // 60}м"
    return f"{seconds}с"


def send_actions_to_user(message, user_id, chat_id=None):
    main_level = None
    if chat_id is not None:
        main_id = _main_user(chat_id, user_id)
        if main_id:
            rel = _get_relationship(chat_id, user_id, main_id)
            if _is_friends(rel):
                main_level = int(rel.get("level", 1))
    lines = [
        "🎭 <b>Доступные RP-действия</b>",
        "",
        "🟢 — доступно по уровню",
        "🔒 — ещё не открыто",
        "",
    ]
    for item in ACTIONS:
        unlocked = main_level is not None and item["level"] <= main_level
        icon = "🟢" if (item["level"] <= 1 or unlocked) else "🔒"
        # The command is intentionally plain text: no slash and spaces instead of underscores.
        if item["level"] <= 1:
            unlock = "доступно"
        elif main_level is not None and unlocked:
            unlock = "доступно"
        else:
            unlock = f"уровень {item['level']}"
        lines.append(f"{icon} <code>{html.escape(item['name'])}</code> — +{item['xp']} XP · ⏱ {_fmt_cd(item['cooldown'])} · {unlock}")
    lines.append("")
    lines.append("💡 Действие можно написать ответом на сообщение друга или указать <code>@username</code>.")
    if main_level is not None:
        lines.append(f"⭐ Для основного друга сейчас открыт уровень: <b>{main_level}</b>.")
    else:
        lines.append("⭐ Чтобы список показывал точный прогресс, сначала установи основного друга через <code>отн основа</code> в группе.")
    message.bot.send_message(user_id, "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


def execute_action(message, action_name, args):
    item = ACTION_MAP.get(action_name)
    if not item:
        return False
    if message.chat.type not in ("group", "supergroup"):
        bot_reply(message, "🎭 RP-действия работают в группе.")
        return True
    target_id, target_name, remaining = extract_target(message, args or "")
    if remaining and not target_id and not message.reply_to_message:
        # A non-target suffix is not meaningful for these actions.
        bot_reply(message, f"🎭 Формат: <code>{html.escape(action_name)} @username</code> или ответь этой командой на сообщение друга.")
        return True
    if not target_id:
        target_id = _main_user(message.chat.id, message.from_user.id)
        if not target_id:
            bot_reply(message, f"🤔 Не знаю, с кем выполнить «{html.escape(action_name)}».\n\nОтветь этой командой на сообщение друга или используй <code>{html.escape(action_name)} @username</code>.\n\n⭐ Можно также установить основного друга: <code>отн основа</code> ответом на его сообщение.")
            return True
    if str(target_id) == str(message.from_user.id):
        bot_reply(message, "😄 RP-действия на себя не работают.")
        return True
    rel = _get_relationship(message.chat.id, message.from_user.id, target_id)
    if not _is_friends(rel):
        bot_reply(message, "⚠️ Это действие доступно только между друзьями.")
        return True
    level = int(rel.get("level", 1))
    if level < item["level"]:
        bot_reply(message, f"🔒 «{html.escape(action_name)}» откроется на <b>{item['level']} уровне</b>. Сейчас у вас уровень <b>{level}</b>.")
        return True

    now = time.time()
    key = action_name
    result = {"ok": False, "reason": "", "remaining": 0, "new_xp": int(rel.get("xp", 0)), "new_level": level}
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    def mutate(store):
        chat = _chat(store, message.chat.id)
        rels = chat.setdefault("relationships", {})
        current = rels.get(_key(message.from_user.id, target_id))
        if not current or current.get("status") != "active":
            result["reason"] = "нет дружбы"
            return store
        cooldowns = current.setdefault("cooldowns", {})
        last = float(cooldowns.get(key, 0) or 0)
        remaining = item["cooldown"] - (now - last)
        if remaining > 0:
            result["reason"] = "cooldown"
            result["remaining"] = int(remaining) + 1
            return store
        daily = current.setdefault("daily_xp", {"date": today, "xp": 0})
        if daily.get("date") != today:
            daily["date"] = today
            daily["xp"] = 0
        daily_xp = int(daily.get("xp", 0) or 0)
        grant = min(item["xp"], max(0, DAILY_XP_CAP - daily_xp))
        if grant <= 0:
            result["reason"] = "daily_cap"
            return store
        old_xp = int(current.get("xp", 0) or 0)
        old_level = int(current.get("level", _xp_to_level(old_xp)))
        new_xp = old_xp + grant
        new_level = _xp_to_level(new_xp)
        current["xp"] = new_xp
        current["level"] = new_level
        current["updated_at"] = now
        cooldowns[key] = now
        counts = current.setdefault("action_counts", {})
        counts[key] = int(counts.get(key, 0) or 0) + 1
        daily["xp"] = daily_xp + grant
        history = current.setdefault("history", [])
        history.append({"action": key, "actor_id": str(message.from_user.id), "target_id": str(target_id), "xp": grant, "at": now})
        if len(history) > 100:
            del history[:-100]
        result.update(ok=True, new_xp=new_xp, new_level=new_level, old_level=old_level, granted=grant)
        return store
    db_update_json("stats", mutate, {})
    if result["reason"] == "cooldown":
        bot_reply(message, f"⏳ Это действие пока на перезарядке. Осталось: <b>{_fmt_cd(result['remaining'])}</b>.")
        return True
    if result["reason"] == "daily_cap":
        bot_reply(message, f"📈 На сегодня лимит развития этой дружбы исчерпан: <b>{DAILY_XP_CAP} XP</b>. Возвращайся завтра.")
        return True
    if not result["ok"]:
        bot_reply(message, "⚠️ Не удалось выполнить действие. Попробуй ещё раз.")
        return True

    actor_name = getattr(message.from_user, "first_name", None) or "Пользователь"
    target_mention = _display_user(target_id, target_name or "Пользователь")
    text = f"{item['emoji']} {html.escape(actor_name)} {item['text']} {target_mention}.\n⭐ <b>+{result['granted']} XP отношениям</b>"
    if result["new_level"] > result["old_level"]:
        text += f"\n\n🎉 <b>Новый уровень!</b> {result['old_level']} → {result['new_level']}\n🏷️ {_level_title(result['new_level'])}"
    bot_reply(message, text, disable_web_page_preview=True)
    return True


def bot_reply(message, text, **kwargs):
    return message.bot.reply_to(message, text, parse_mode=kwargs.pop("parse_mode", "HTML"), **kwargs)


@__import__("runtime").bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("rel|"))
def relationship_callback(call):
    try:
        parts = call.data.split("|")
        if len(parts) != 6:
            return
        action = parts[1]
        if action == "accept":
            _finish_request(call, True)
        elif action == "decline":
            _finish_request(call, False)
        elif action == "end":
            _confirm_end(call, True)
        elif action == "cancel_end":
            _confirm_end(call, False)
    except Exception:
        logging.exception("[relationships] callback failed")
        try:
            call.bot.answer_callback_query(call.id, "⚠️ Не удалось обработать действие.", show_alert=True)
        except Exception:
            pass
