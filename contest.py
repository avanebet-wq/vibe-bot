# -*- coding: utf-8 -*-
"""Конкурсы/запись: регистрация по реферальным приглашениям и тихий режим Лизы."""
import html
import logging
import threading
import time
from utils import _lookup_username, remember_user

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
    # Пользователь сам открывает профиль/карточку группы и нажимает
    # системную кнопку Telegram «Добавить участников». Лиза отслеживает
    # фактические добавления по service-message new_chat_members.
    return types.InlineKeyboardMarkup(row_width=1).add(
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
    # Перед новой публикацией удаляем предыдущую, чтобы в чате оставалось
    # только актуальное сообщение с текущим списком участников.
    previous_message_id = session.get("last_message_id")
    if previous_message_id:
        try:
            bot.delete_message(cid, previous_message_id)
        except Exception as exc:
            # Сообщение могло быть удалено вручную или Telegram мог запретить
            # удаление из-за срока/прав. Это не должно останавливать запись.
            LOG.warning("Не удалось удалить предыдущее сообщение конкурса %s/%s: %s", cid, previous_message_id, exc)

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
    # Уникальные люди, которых этот пользователь реально добавил в группу.
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


def cmd_add_participant(message, args):
    """Админская ручная запись по username: «Лиза записать @username».

    Для ручной записи намеренно не нужен Telegram user ID и не выполняется
    поиск пользователя через Bot API. В список сохраняется ровно username.
    """
    cid = message.chat.id
    if message.chat.type not in ("group", "supergroup"):
        return bot.reply_to(message, "⚠️ Команда работает только в группе.")
    if not is_chat_admin(cid, message.from_user.id):
        return bot.reply_to(message, "⛔ Только администратор может добавлять участников.")
    if not is_active(cid):
        return bot.reply_to(message, "ℹ️ Активной записи нет.")

    raw = (args or "").strip()
    token = raw.split()[0] if raw else ""
    if not token.startswith("@") or len(token) < 2:
        return bot.reply_to(message, "⚠️ Формат: <code>Лиза записать @username</code>")

    username = token[1:].strip().lower()
    if not username:
        return bot.reply_to(message, "⚠️ Укажи username после @.")

    with _LOCK:
        data = _store()
        session = data.get(str(cid))
        if not session or not session.get("active"):
            return bot.reply_to(message, "ℹ️ Активной записи нет.")

        participants = session.setdefault("participants", {})
        # Ключом является username — Telegram ID вообще не требуется.
        participant_key = f"username:{username}"
        if participant_key in participants:
            return bot.reply_to(message, f"ℹ️ @{_escape(username)} уже записан(а).", parse_mode="HTML")

        participants[participant_key] = {
            "username": username,
            "name": username,
            "registered_at": time.time(),
            "manual": True,
        }
        data[str(cid)] = session
        _save(data)

    bot.reply_to(message, f"✅ @{_escape(username)} добавлен(а) в список участников.", parse_mode="HTML")
    try:
        if session.get("last_message_id"):
            bot.edit_message_text(
                _render(cid, session), cid, session["last_message_id"],
                reply_markup=_button_markup(cid, session), parse_mode="HTML"
            )
    except Exception:
        LOG.exception("failed to update contest participant list")

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
    if action == "register":
        _register(call, cid, session)
    else:
        bot.answer_callback_query(call.id)


def _count_direct_add(cid, inviter_id, user_ids):
    """Засчитывает реальных добавленных в группу пользователей по service-log.

    Важно: Telegram сообщает sender (message.from_user) для сервисного
    сообщения new_chat_members. Это позволяет считать именно прямые добавления,
    без создания персональных invite-ссылок.
    """
    if not inviter_id or not user_ids:
        return False
    with _LOCK:
        data = _store()
        session = data.get(str(cid))
        if not session or not session.get("active"):
            return False
        if inviter_id == BOT_ID:
            return False
        invited_map = session.setdefault("invited_users", {})
        invited = invited_map.setdefault(str(inviter_id), [])
        changed = False
        for uid in user_ids:
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                continue
            if uid == inviter_id or uid == BOT_ID:
                continue
            if uid not in invited:
                invited.append(uid)
                changed = True
        if changed:
            data[str(cid)] = session
            _save(data)
        return changed


def handle_new_members(message):
    if getattr(message.chat, "type", "") not in ("group", "supergroup"):
        return
    if not is_active(message.chat.id):
        return
    inviter = getattr(getattr(message, "from_user", None), "id", None)
    members = getattr(message, "new_chat_members", None) or []
    # Запоминаем username/ID новых участников, чтобы админ мог позже
    # использовать «Лиза записать @username».
    for member in members:
        try:
            remember_user(member)
        except Exception:
            LOG.exception("failed to cache contest new member")
    ids = [getattr(u, "id", None) for u in members]
    if _count_direct_add(message.chat.id, inviter, ids):
        added = [uid for uid in ids if uid]
        LOG.info(
            "contest invite log: chat=%s who_added=%s whom=%s",
            message.chat.id, inviter, added,
        )
        for added_uid in added:
            if added_uid != inviter and added_uid != BOT_ID:
                LOG.info(
                    "contest invite event: chat=%s inviter=%s added_user=%s",
                    message.chat.id, inviter, added_uid,
                )


def handle_chat_member(update):
    """Совместимость для входов по invite-link.

    Без уникальных ссылок Telegram не сообщает, какой участник поделился
    общей ссылкой, поэтому такие входы НЕ засчитываем. Реальный учёт идёт
    по service-message new_chat_members, то есть по факту прямого добавления.
    """
    return


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


@bot.message_handler(content_types=["new_chat_members"])
def _contest_new_members_router(message):
    try:
        handle_new_members(message)
    except Exception as exc:
        LOG.exception("contest new members handler failed")


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


