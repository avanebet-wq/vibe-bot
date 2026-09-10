# -*- coding: utf-8 -*-
"""Конкурсы/запись: регистрация по реферальным приглашениям и тихий режим Лизы."""
import html
import logging
import threading
import time
import uuid
from urllib.parse import quote

from telebot import types

from database import db_get, db_set
from runtime import bot, BOT_ID
from utils import is_chat_admin

LOG = logging.getLogger("contest")
_LOCK = threading.RLock()
KEY = "contest_sessions"
INTERVAL = 60


def _store():
    return db_get(KEY, {}) or {}


def _save(data):
    db_set(KEY, data)


def _get(cid):
    return _store().get(str(cid))


def is_active(cid):
    with _LOCK:
        x = _get(cid)
        return bool(x and x.get("active"))


def _escape(x):
    return html.escape(str(x or ""), quote=False)


def _username(user):
    return getattr(user, "username", None) or ""


def _button_markup(cid, session):
    required = int(session.get("required", 0))
    return types.InlineKeyboardMarkup(row_width=2).add(
        types.InlineKeyboardButton("➕ Добавить людей", callback_data=f"contest|invite|{cid}"),
        types.InlineKeyboardButton("📝 Записаться", callback_data=f"contest|register|{cid}"),
    )


def _render(cid, session):
    required = int(session.get("required", 0))
    participants = session.get("participants", {}) or {}
    lines = [_escape(session.get("text", "")), "", "👥 <b>Участники:</b>"]
    if participants:
        for i, p in enumerate(participants.values(), 1):
            lines.append(f"{i}. @{_escape(p.get('username'))}")
    else:
        lines.append("Пока никто не записался.")
    lines += ["", f"➕ Для записи нужно пригласить: <b>{required}</b> чел."]
    return "\n".join(lines)


def _send(cid, session):
    msg = bot.send_message(cid, _render(cid, session), reply_markup=_button_markup(cid, session), parse_mode="HTML")
    session["last_message_id"] = getattr(msg, "message_id", None)
    session["last_sent_at"] = time.time()
    data = _store(); data[str(cid)] = session; _save(data)
    return msg


def cmd_start(message, args):
    cid = message.chat.id
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, "⚠️ Команда записи работает только в группе.")
        return
    if not is_chat_admin(cid, message.from_user.id):
        bot.reply_to(message, "⛔ Только администратор может запустить запись.")
        return
    raw = (args or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit() or int(parts[0]) < 1:
        bot.reply_to(message, "⚠️ Формат: <code>Лиза запись 2 текст конкурса</code>")
        return
    required = int(parts[0])
    text = parts[1].strip()
    if not text:
        bot.reply_to(message, "⚠️ Укажи текст конкурса.")
        return
    if is_active(cid):
        bot.reply_to(message, "⚠️ Запись уже идёт. Сначала: <code>Лиза стоп запись</code>")
        return
    session = {
        "active": True,
        "required": required,
        "text": text,
        "owner_id": message.from_user.id,
        "started_at": time.time(),
        "last_sent_at": 0,
        "last_message_id": None,
        "participants": {},
        "invites": {},
        "invited_users": {},
    }
    with _LOCK:
        data = _store(); data[str(cid)] = session; _save(data)
    _send(cid, session)


def cmd_stop(message):
    cid = message.chat.id
    if message.chat.type not in ("group", "supergroup"):
        return
    if not is_chat_admin(cid, message.from_user.id):
        bot.reply_to(message, "⛔ Только администратор может остановить запись.")
        return
    with _LOCK:
        data = _store(); session = data.get(str(cid))
        if not session or not session.get("active"):
            bot.reply_to(message, "ℹ️ Активной записи нет.")
            return
        session["active"] = False
        session["stopped_at"] = time.time()
        data[str(cid)] = session; _save(data)
    bot.reply_to(message, "🛑 Запись остановлена.")


def _create_invite(cid, uid, session):
    # Уникальная ссылка на каждого участника позволяет Telegram сообщить нам,
    # кто именно пришёл по этой ссылке.
    name = f"liza-{cid}-{uid}-{uuid.uuid4().hex[:8]}"
    try:
        inv = bot.create_chat_invite_link(cid, name=name, creates_join_request=False)
    except TypeError:
        inv = bot.create_chat_invite_link(cid, name=name)
    url = getattr(inv, "invite_link", None)
    if not url:
        raise RuntimeError("Telegram не вернул invite link")
    session.setdefault("invites", {})[url] = {"owner_id": uid, "created_at": time.time()}
    return url


def _invite_text(cid, uid, session):
    url = _create_invite(cid, uid, session)
    n = int(session.get("required", 0))
    return (f"➕ Твоя ссылка для приглашения людей:\n\n{url}\n\n"
            f"Пригласи <b>{n}</b> человек. Когда нужное количество будет приглашено, нажми «Записаться».")


def _register(call, cid, session):
    user = call.from_user
    uid = user.id
    username = _username(user)
    if not username:
        return bot.answer_callback_query(call.id, "Сначала создай username в Telegram. После этого сможешь записаться.", show_alert=True)
    participants = session.setdefault("participants", {})
    if str(uid) in participants:
        return bot.answer_callback_query(call.id, "Ты уже записан(а).", show_alert=True)
    invited = session.setdefault("invited_users", {}).get(str(uid), [])
    # Уникальные люди, пришедшие по персональной ссылке.
    invited_count = len(invited)
    required = int(session.get("required", 0))
    if invited_count < required:
        return bot.answer_callback_query(call.id, f"Нужно пригласить ещё {required - invited_count} чел.", show_alert=True)
    participants[str(uid)] = {"id": uid, "username": username, "name": user.first_name or username, "registered_at": time.time()}
    data = _store(); data[str(cid)] = session; _save(data)
    bot.answer_callback_query(call.id, "✅ Ты записан(а) в конкурс!")
    try:
        bot.edit_message_text(_render(cid, session), cid, call.message.message_id, reply_markup=_button_markup(cid, session), parse_mode="HTML")
    except Exception:
        pass


def handle_callback(call):
    parts = (call.data or "").split("|")
    if len(parts) < 3 or parts[0] != "contest":
        return
    action, cid_text = parts[1], parts[2]
    try: cid = int(cid_text)
    except ValueError: return bot.answer_callback_query(call.id, "Некорректная запись.", show_alert=True)
    session = _get(cid)
    if not session or not session.get("active"):
        return bot.answer_callback_query(call.id, "Эта запись уже остановлена.", show_alert=True)
    if action == "invite":
        uid = call.from_user.id
        try:
            with _LOCK:
                text = _invite_text(cid, uid, session)
                data = _store(); data[str(cid)] = session; _save(data)
            bot.send_message(uid, text, parse_mode="HTML", disable_web_page_preview=True)
            bot.answer_callback_query(call.id, "Ссылка отправлена в личные сообщения.")
        except Exception as e:
            LOG.exception("invite link failed")
            bot.answer_callback_query(call.id, "Не удалось создать ссылку. Проверь права Лизы администратора.", show_alert=True)
    elif action == "register":
        _register(call, cid, session)
    else:
        bot.answer_callback_query(call.id)


def handle_chat_member(update):
    cid = getattr(getattr(update, "chat", None), "id", None)
    if cid is None or not is_active(cid):
        return
    old = getattr(getattr(update, "old_chat_member", None), "status", None)
    new = getattr(getattr(update, "new_chat_member", None), "status", None)
    if new not in ("member", "administrator") or old in ("member", "administrator"):
        return
    invite = getattr(update, "invite_link", None)
    invite_url = getattr(invite, "invite_link", None) if invite else None
    if not invite_url:
        return
    user = getattr(update, "new_chat_member", None)
    uid = getattr(user, "user", None)
    uid = getattr(uid, "id", None)
    if uid is None:
        return
    with _LOCK:
        data = _store(); session = data.get(str(cid))
        if not session or not session.get("active"):
            return
        meta = session.setdefault("invites", {}).get(invite_url)
        if not meta:
            return
        owner = int(meta.get("owner_id"))
        if owner == uid or uid == BOT_ID:
            return
        invited = session.setdefault("invited_users", {}).setdefault(str(owner), [])
        if uid not in invited:
            invited.append(uid)
        data[str(cid)] = session; _save(data)


def tick(bot_instance=None):
    now = time.time()
    changed = False
    with _LOCK:
        data = _store()
        for cid_text, session in list(data.items()):
            if not session.get("active"):
                continue
            if now - float(session.get("last_sent_at") or 0) < INTERVAL:
                continue
            try:
                _send(int(cid_text), session)
            except Exception:
                LOG.exception("contest periodic send failed for %s", cid_text)
        if changed:
            _save(data)


# Регистрация Telegram-обработчиков здесь, чтобы конкурсный режим был независим
# от порядка импорта остальных обработчиков.
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("contest|"))
def _contest_callback_router(call):
    try:
        handle_callback(call)
    except Exception as exc:
        LOG.exception("contest callback failed")
        try:
            bot.answer_callback_query(call.id, "⚠️ Что-то пошло не так.", show_alert=True)
        except Exception:
            pass


if hasattr(bot, "chat_member_handler"):
    @bot.chat_member_handler()
    def _contest_chat_member_router(update):
        try:
            handle_chat_member(update)
        except Exception as exc:
            LOG.exception("contest chat member handler failed")
