# -*- coding: utf-8 -*-
"""Настройки игры «Слова» для конкретной публикации.

Меню открывается в ЛС администратора командой «настройки слова».
Все игровые параметры хранятся внутри выбранной публикации, а не глобально.
"""
from __future__ import annotations

import html
import re

from telebot import types

from runtime import bot
from utils import is_chat_admin, parse_duration, format_seconds
import settings_store as store

PREFIX = "wgs"


def _esc(value):
    return html.escape(str(value or ""), quote=False)


def _posts(gid):
    return sorted(store.get_posts(gid).items(), key=lambda x: int(x[0]))


def _ensure_selected(gid, pid=None):
    if pid is not None and store.get_post(gid, pid):
        store.set_word_game_post_id(gid, pid)
        return str(pid)
    selected = store.get_word_game_post_id(gid)
    if selected and store.get_post(gid, selected):
        return str(selected)
    posts = _posts(gid)
    if len(posts) == 1:
        selected = str(posts[0][0])
        store.set_word_game_post_id(gid, selected)
        return selected
    return None


def _cfg(gid, pid):
    return store.get_word_game_config(gid, pid) or {}


def _cfg_label(cfg):
    interval = cfg.get("interval_seconds")
    interval_txt = format_seconds(interval) if interval else "не задан"
    start = cfg.get("start_time") or "сразу после команды"
    prizes = cfg.get("prizes") or []
    prizes_txt = ", ".join(f"{i + 1}. {p}" for i, p in enumerate(prizes)) or "не заданы"
    return interval_txt, start, prizes_txt


def settings_text(gid, pid):
    post = store.get_post(gid, pid) or {}
    cfg = _cfg(gid, pid)
    interval_txt, start_txt, prizes_txt = _cfg_label(cfg)
    text_status = "задан" if post.get("text") else "не задан"
    media = post.get("media") or {}
    photo_status = "задано" if media.get("type") == "photo" else "не задано"
    delete_txt = "включено ✅" if cfg.get("delete_last", True) else "выключено ❌"
    reward = cfg.get("reward_username") or "не задан"
    return (
        "🎯 <b>НАСТРОЙКИ СЛОВ</b>\n\n"
        f"Публикация: <b>№{_esc(pid)}</b>\n\n"
        f"1. 📝 Текст: <b>{text_status}</b>\n"
        f"2. 📷 Фото: <b>{photo_status}</b>\n"
        f"3. 🔁 Интервал: <b>{_esc(interval_txt)}</b>\n"
        f"4. 🗑 Удалять последнее: <b>{delete_txt}</b>\n"
        f"5. ⏰ Время начала: <b>{_esc(start_txt)}</b>\n"
        f"6. ⏱ Время на ответ: <b>{int(cfg.get('answer_time_minutes', 1) or 1)} мин.</b>\n"
        f"7. 🏆 Призовых мест: <b>{int(cfg.get('prize_places', 3) or 3)}</b>\n"
        f"8. 🎁 Призы: <b>{_esc(prizes_txt)}</b>\n"
        f"9. 👥 Максимум участников: <b>{int(cfg.get('max_participants', 10) or 10)}</b>\n"
        f"10. 🔤 Всего слов: <b>{int(cfg.get('total_words', 20) or 20)}</b>\n"
        f"11. 💰 Юзернейм для награды: <b>{_esc(reward)}</b>\n\n"
        "Настройки относятся только к этой публикации.\n"
        "Команда «запись слова» использует выбранную публикацию."
    )


def settings_kb(gid, pid):
    cfg = _cfg(gid, pid)
    delete_label = "4. 🗑 Удалять последнее ✅" if cfg.get("delete_last", True) else "4. 🗑 Удалять последнее ❌"
    rows = [
        [types.InlineKeyboardButton("1. 📝 Настроить текст", callback_data=f"{PREFIX}|text|{gid}|{pid}")],
        [types.InlineKeyboardButton("2. 📷 Настроить фото", callback_data=f"{PREFIX}|photo|{gid}|{pid}")],
        [types.InlineKeyboardButton("3. 🔁 Настроить интервал", callback_data=f"{PREFIX}|interval|{gid}|{pid}")],
        [types.InlineKeyboardButton(delete_label, callback_data=f"{PREFIX}|toggle_delete|{gid}|{pid}")],
        [types.InlineKeyboardButton("5. ⏰ Время начала", callback_data=f"{PREFIX}|start|{gid}|{pid}")],
        [types.InlineKeyboardButton("6. ⏱ Время на ответ", callback_data=f"{PREFIX}|answer|{gid}|{pid}")],
        [types.InlineKeyboardButton("7. 🏆 Призовые места", callback_data=f"{PREFIX}|places|{gid}|{pid}")],
        [types.InlineKeyboardButton("8. 🎁 Призы", callback_data=f"{PREFIX}|prizes|{gid}|{pid}")],
        [types.InlineKeyboardButton("9. 👥 Участники", callback_data=f"{PREFIX}|max|{gid}|{pid}")],
        [types.InlineKeyboardButton("🔤 Количество слов", callback_data=f"{PREFIX}|words|{gid}|{pid}")],
        [types.InlineKeyboardButton("💰 Юзернейм для награды", callback_data=f"{PREFIX}|reward|{gid}|{pid}")],
        [types.InlineKeyboardButton("🔄 Выбрать другую публикацию", callback_data=f"{PREFIX}|choose|{gid}")],
        [types.InlineKeyboardButton("❌ Закрыть", callback_data=f"{PREFIX}|close|{gid}|{pid}")],
    ]
    return types.InlineKeyboardMarkup(keyboard=rows)


def _choose_text(gid):
    posts = _posts(gid)
    if not posts:
        return "📭 В этом чате пока нет публикаций. Сначала создайте публикацию в настройках Лизы.", types.InlineKeyboardMarkup()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for pid, post in posts:
        status = "✅" if post.get("enabled") else "❌"
        has_content = "есть текст/фото" if (post.get("text") or post.get("media")) else "пустая"
        kb.add(types.InlineKeyboardButton(
            f"№{pid} {status} — {has_content}", callback_data=f"{PREFIX}|select|{gid}|{pid}"
        ))
    kb.add(types.InlineKeyboardButton("❌ Закрыть", callback_data=f"{PREFIX}|close_list|{gid}"))
    return "🎯 <b>Выберите публикацию для игры «Слова»</b>\n\nИменно её текст/фото будет использоваться для записи.", kb


def open_settings_in_dm(user_id, gid, pid=None):
    if not is_chat_admin(gid, user_id):
        return bot.send_message(user_id, "⛔ Вы не администратор этого чата.")
    selected = _ensure_selected(gid, pid)
    if not selected:
        text, kb = _choose_text(gid)
    else:
        text, kb = settings_text(gid, selected), settings_kb(gid, selected)
    return bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML")


def open_from_group(message, args=""):
    if message.chat.type not in ("group", "supergroup"):
        return bot.reply_to(message, "⚠️ Команда работает из группы, чтобы понять, какую группу настраивать.")
    gid = message.chat.id
    uid = message.from_user.id
    if not is_chat_admin(gid, uid):
        return bot.reply_to(message, "⛔ Только администратор может настраивать игру «Слова».")
    raw = (args or "").strip()
    pid = raw.split()[0] if raw and raw.split()[0].isdigit() else None
    try:
        msg = open_settings_in_dm(uid, gid, pid)
        bot.reply_to(message, "🎯 Настройки игры «Слова» отправила вам в личные сообщения.")
        return msg
    except Exception:
        from runtime import BOT_USERNAME
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton(
            "⚙️ Открыть настройки в ЛС", url=f"https://t.me/{BOT_USERNAME}?start=wordcfg-{gid}"
        ))
        return bot.reply_to(message, "📩 Сначала откройте личные сообщения с Лизой и нажмите кнопку ниже.", reply_markup=kb)


def _set_pending(call, kind, gid, pid):
    store.set_pending(call.message.chat.id, call.from_user.id, {"kind": f"word_{kind}", "gid": gid, "pid": str(pid)})
    bot.answer_callback_query(call.id)


def _prompt(call, kind, text, gid, pid):
    _set_pending(call, kind, gid, pid)
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Отмена", callback_data=f"{PREFIX}|back|{gid}|{pid}"))
    return bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode="HTML")


def _edit(call, gid, pid):
    try:
        bot.edit_message_text(settings_text(gid, pid), call.message.chat.id, call.message.message_id,
                              reply_markup=settings_kb(gid, pid), parse_mode="HTML")
    except Exception:
        pass


def _parse_prizes(raw, places):
    result = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\s*(?:\d+\s*[.)-]\s*)?(.+?)\s*$", line)
        if m and m.group(1).strip():
            result.append(m.group(1).strip())
    if len(result) != places:
        return None
    return result


def handle_callback(call):
    parts = (call.data or "").split("|")
    if len(parts) < 3 or parts[0] != PREFIX:
        return False
    action = parts[1]
    try:
        gid = int(parts[2])
    except (TypeError, ValueError):
        return True
    if not is_chat_admin(gid, call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Только администратор.", show_alert=True)
        return True
    if action == "choose":
        bot.answer_callback_query(call.id)
        text, kb = _choose_text(gid)
        return bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    if action == "select":
        pid = parts[3] if len(parts) > 3 else ""
        if not store.get_post(gid, pid):
            return bot.answer_callback_query(call.id, "⚠️ Публикация не найдена.", show_alert=True)
        store.set_word_game_post_id(gid, pid)
        bot.answer_callback_query(call.id, f"Публикация №{pid} выбрана.")
        return _edit(call, gid, pid)
    if len(parts) < 4:
        return True
    pid = parts[3]
    if not store.get_post(gid, pid):
        return bot.answer_callback_query(call.id, "⚠️ Публикация не найдена.", show_alert=True)
    store.set_word_game_post_id(gid, pid)

    if action == "text":
        return _prompt(call, "text", "📝 <b>Настроить текст</b>\n\nОтправьте текст сообщения набора.", gid, pid)
    if action == "photo":
        return _prompt(call, "photo", "📷 <b>Настроить фото</b>\n\nОтправьте фотографию для публикации.", gid, pid)
    if action == "interval":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        for sec, label in [(300,"5 мин"),(600,"10 мин"),(1800,"30 мин"),(3600,"1 час"),(7200,"2 часа"),(21600,"6 часов"),(43200,"12 часов")]:
            kb.add(types.InlineKeyboardButton(label, callback_data=f"{PREFIX}|iset|{gid}|{pid}|{sec}"))
        kb.add(types.InlineKeyboardButton("✏️ Своё значение", callback_data=f"{PREFIX}|interval_custom|{gid}|{pid}"))
        return bot.edit_message_text("🔁 <b>Интервал повторения публикации</b>\n\nВыберите вариант или задайте свой.", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    if action == "iset":
        try: sec = int(parts[4])
        except Exception: sec = 0
        if sec <= 0: return bot.answer_callback_query(call.id, "⚠️ Некорректный интервал.", show_alert=True)
        store.update_word_game_config(gid, pid, interval_seconds=sec)
        bot.answer_callback_query(call.id, "✅ Интервал сохранён.")
        return _edit(call, gid, pid)
    if action == "interval_custom":
        return _prompt(call, "interval", "🔁 Отправьте интервал: <code>30м</code>, <code>2ч</code> или <code>1д</code>.", gid, pid)
    if action == "toggle_delete":
        cfg = _cfg(gid, pid)
        new = not bool(cfg.get("delete_last", True))
        store.update_word_game_config(gid, pid, delete_last=new)
        bot.answer_callback_query(call.id, "🗑 Удаление " + ("включено." if new else "выключено."))
        return _edit(call, gid, pid)
    if action == "start":
        return _prompt(call, "start", "⏰ <b>Время начала</b>\n\nВведите время в формате <code>ЧЧ:ММ</code>, например <code>20:30</code>.\nЕсли оставить пустое время нельзя — для немедленного старта используйте «запись слова» без времени.", gid, pid)
    if action == "answer":
        return _prompt(call, "answer", "⏱ <b>Время на ответ</b>\n\nВведите количество минут от 1 до 60.", gid, pid)
    if action == "places":
        return _prompt(call, "places", "🏆 <b>Призовые места</b>\n\nВведите количество мест от 1 до 10.", gid, pid)
    if action == "prizes":
        places = int(_cfg(gid, pid).get("prize_places", 3) or 3)
        return _prompt(call, "prizes", f"🎁 <b>Призы</b>\n\nВведите ровно {places} строк(и), например:\n<code>1. 350₴\n2. 300₴\n3. 250₴</code>", gid, pid)
    if action == "max":
        return _prompt(call, "max", "👥 <b>Максимальное количество участников</b>\n\nВведите число от 2 до 500.", gid, pid)
    if action == "words":
        return _prompt(call, "words", "🔤 <b>Количество слов в игре</b>\n\nВведите число от 1 до 500.", gid, pid)
    if action == "reward":
        return _prompt(call, "reward", "💰 <b>Юзернейм для получения награды</b>\n\nВведите username в формате <code>@username</code>.", gid, pid)
    if action == "back":
        store.clear_pending(call.message.chat.id, call.from_user.id)
        bot.answer_callback_query(call.id)
        return _edit(call, gid, pid)
    if action == "close":
        store.clear_pending(call.message.chat.id, call.from_user.id)
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception: pass
        return True
    if action == "close_list":
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception: pass
        return True
    return True


def handle_pending(message):
    if not getattr(message, "from_user", None):
        return False
    pending = store.get_pending(message.chat.id, message.from_user.id)
    if not pending or not str(pending.get("kind", "")).startswith("word_"):
        return False
    kind = str(pending["kind"])[5:]
    gid, pid = pending.get("gid"), str(pending.get("pid"))
    if not is_chat_admin(gid, message.from_user.id):
        store.clear_pending(message.chat.id, message.from_user.id)
        return True
    if not store.get_post(gid, pid):
        store.clear_pending(message.chat.id, message.from_user.id)
        return True

    if kind == "text":
        if not message.text or len(message.text) > 3900:
            bot.reply_to(message, "⚠️ Отправьте текст до 3900 символов.")
            return True
        store.update_post(gid, pid, text=message.text)
        store.clear_pending(message.chat.id, message.from_user.id)
        bot.reply_to(message, "✅ Текст сохранён.")
        return True
    if kind == "photo":
        if not getattr(message, "photo", None):
            bot.reply_to(message, "⚠️ Отправьте именно фотографию.")
            return True
        store.update_post(gid, pid, media={"type": "photo", "file_id": message.photo[-1].file_id, "caption": message.caption})
        store.clear_pending(message.chat.id, message.from_user.id)
        bot.reply_to(message, "✅ Фото сохранено.")
        return True
    if kind == "interval":
        seconds, ok = parse_duration((message.text or "").strip())
        if not ok or seconds < 60 or seconds > 7 * 86400:
            bot.reply_to(message, "⚠️ Интервал должен быть от 1 минуты до 7 дней. Пример: <code>30м</code> или <code>2ч</code>.")
            return True
        store.update_word_game_config(gid, pid, interval_seconds=seconds)
        store.clear_pending(message.chat.id, message.from_user.id)
        bot.reply_to(message, f"✅ Интервал: {format_seconds(seconds)}.")
        return True
    if kind == "start":
        raw = (message.text or "").strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw):
            bot.reply_to(message, "⚠️ Формат: <code>ЧЧ:ММ</code>, например <code>20:30</code>.")
            return True
        store.update_word_game_config(gid, pid, start_time=raw)
        store.clear_pending(message.chat.id, message.from_user.id)
        bot.reply_to(message, f"✅ Время начала: {raw}.")
        return True
    if kind in ("answer", "places", "max", "words"):
        try: n = int((message.text or "").strip())
        except Exception: n = 0
        bounds = {"answer": (1,60), "places": (1,10), "max": (2,500), "words": (1,500)}
        lo, hi = bounds[kind]
        if not lo <= n <= hi:
            bot.reply_to(message, f"⚠️ Укажите число от {lo} до {hi}.")
            return True
        field = {"answer":"answer_time_minutes", "places":"prize_places", "max":"max_participants", "words":"total_words"}[kind]
        changes = {field:n}
        if kind == "places":
            old = _cfg(gid, pid).get("prizes") or []
            if len(old) != n:
                changes["prizes"] = old[:n] + ["Уточняется"] * max(0, n-len(old))
        store.update_word_game_config(gid, pid, **changes)
        store.clear_pending(message.chat.id, message.from_user.id)
        labels = {"answer":"Время на ответ", "places":"Призовых мест", "max":"Максимум участников", "words":"Всего слов"}
        bot.reply_to(message, f"✅ {labels[kind]}: {n}.")
        return True
    if kind == "prizes":
        places = int(_cfg(gid, pid).get("prize_places", 3) or 3)
        prizes = _parse_prizes(message.text or "", places)
        if prizes is None:
            bot.reply_to(message, f"⚠️ Нужно указать ровно {places} призов — по одному на строку.")
            return True
        store.update_word_game_config(gid, pid, prizes=prizes)
        store.clear_pending(message.chat.id, message.from_user.id)
        bot.reply_to(message, "✅ Призы сохранены.")
        return True
    if kind == "reward":
        raw = (message.text or "").strip()
        if not re.fullmatch(r"@[A-Za-z0-9_]{3,32}", raw):
            bot.reply_to(message, "⚠️ Формат: <code>@username</code>.")
            return True
        store.update_word_game_config(gid, pid, reward_username=raw)
        store.clear_pending(message.chat.id, message.from_user.id)
        bot.reply_to(message, f"✅ Юзернейм для награды: {raw}.")
        return True
    return False


def has_pending(message):
    try:
        p = store.get_pending(message.chat.id, message.from_user.id)
        return bool(p and str(p.get("kind", "")).startswith("word_"))
    except Exception:
        return False


def _callback_router(call):
    """Маршрутизатор callback-кнопок меню «Настройки слова»."""
    try:
        handled = handle_callback(call)
        if handled is False:
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
    except Exception:
        import logging
        logging.getLogger("word_settings").exception("word settings callback failed")
        try:
            bot.answer_callback_query(call.id, "⚠️ Ошибка настройки. Попробуйте ещё раз.", show_alert=True)
        except Exception:
            pass


def _pending_text_router(message):
    return handle_pending(message)


def _pending_photo_router(message):
    return handle_pending(message)


def register_handlers():
    """Зарегистрировать обработчики после загрузки всех модулей проекта."""
    if getattr(bot, "_liza_word_settings_handlers_registered", False):
        return
    bot.register_callback_query_handler(
        _callback_router,
        func=lambda c: bool(c.data and c.data.startswith("wgs|")),
    )
    bot.register_message_handler(_pending_text_router, content_types=["text"], func=has_pending)
    bot.register_message_handler(_pending_photo_router, content_types=["photo"], func=has_pending)
    bot._liza_word_settings_handlers_registered = True
