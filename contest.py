# -*- coding: utf-8 -*-
"""Конкурсы/запись: регистрация по реферальным приглашениям и тихий режим Лизы."""
import html
import logging
import threading
import time
from utils import _lookup_username, remember_user

from telebot import types

from database import db_get, db_set, db_update_json
from runtime import bot, BOT_ID
from utils import is_chat_admin

LOG = logging.getLogger("contest")
_LOCK = threading.RLock()
KEY = "contest_sessions"
INTERVAL = 60


def _config_from_session(session):
    return session.get("config", {}) or {}


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
    return types.InlineKeyboardMarkup(row_width=1).add(
        types.InlineKeyboardButton("📝 Записаться", callback_data=f"contest|register|{cid}"),
    )


def _render(cid, session):
    cfg = _config_from_session(session)
    requirement = cfg.get("requirement", "invite")
    required = int(cfg.get("required", 1) or 1)
    participants = session.get("participants", {}) or {}
    text = _escape(cfg.get("text") or session.get("text") or "🎉 Розыгрыш!")
    lines = [text, "", "👥 <b>Участники:</b>"]
    if participants:
        for i, p in enumerate(participants.values(), 1):
            username = p.get("username") or p.get("name") or str(p.get("id", ""))
            prefix = "@" if p.get("username") else ""
            lines.append(f"{i}. {prefix}{_escape(username)}")
    else:
        lines.append("Пока никто не записался.")
    if requirement == "invite":
        lines += ["", f"📋 Условие участия: пригласить <b>{required}</b> чел. в группу."]
    else:
        lines += ["", "📋 Условие участия: <b>без дополнительных требований</b>."]
    return "\n".join(lines)


def _send(cid, session):
    """Публикует новую версию сначала, затем аккуратно заменяет старую.

    Внешний Telegram API не вызывается под _LOCK. Состояние сохраняется
    атомарно и объединяется с актуальной сессией из PostgreSQL, поэтому
    параллельная регистрация участника не затирается публикацией.
    """
    previous_message_id = session.get("last_message_id")
    cfg = _config_from_session(session)
    markup = _button_markup(cid, session)
    if cfg.get("photo") and cfg.get("photo", {}).get("file_id"):
        msg = bot.send_photo(
            cid, cfg["photo"]["file_id"], caption=_render(cid, session),
            reply_markup=markup, parse_mode="HTML",
        )
    else:
        msg = bot.send_message(
            cid, _render(cid, session), reply_markup=markup, parse_mode="HTML"
        )

    new_message_id = getattr(msg, "message_id", None)
    sent_at = time.time()
    session_id = session.get("started_at")

    def mutate(data):
        current = data.get(str(cid))
        if not current:
            return data
        # Contest was restarted/stopped while Telegram was processing the send.
        if session_id is not None and current.get("started_at") != session_id:
            return data
        current["last_message_id"] = new_message_id
        current["last_sent_at"] = sent_at
        current["send_in_progress"] = False
        return data

    data = db_update_json(KEY, mutate, {})
    current = data.get(str(cid), session)
    if current.get("started_at") != session_id and new_message_id:
        try:
            bot.delete_message(cid, new_message_id)
        except Exception:
            LOG.warning("Contest publication became stale: %s/%s", cid, new_message_id)
        return None

    if previous_message_id and previous_message_id != new_message_id:
        try:
            bot.delete_message(cid, previous_message_id)
        except Exception as exc:
            LOG.warning("Не удалось удалить предыдущее сообщение конкурса %s/%s: %s", cid, previous_message_id, exc)
    return msg


def preview_config(gid, chat_id):
    cfg = __import__("contest_settings").get_config(gid)
    session = {"config": cfg, "participants": {
        "preview1": {"username": "пример_участника"},
        "preview2": {"username": "ещё_один"},
    }}
    if cfg.get("photo", {}).get("file_id"):
        return bot.send_photo(chat_id, cfg["photo"]["file_id"], caption=_render(gid, session), reply_markup=_button_markup(gid, session), parse_mode="HTML")
    return bot.send_message(chat_id, _render(gid, session), reply_markup=_button_markup(gid, session), parse_mode="HTML")


def _next_run_from_config(cfg):
    if cfg.get("start_mode") == "now":
        return time.time()
    raw = cfg.get("start_time")
    if not raw: return time.time()
    try:
        import datetime
        now = datetime.datetime.now().astimezone()
        hh, mm = map(int, raw.split(":"))
        dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dt <= now: dt += datetime.timedelta(days=1)
        return dt.timestamp()
    except Exception:
        return time.time()


def start_from_config(message, cfg):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id):
        return bot.reply_to(message, "⛔ Только администратор может запустить розыгрыш.")
    if not (cfg.get("text") or cfg.get("photo")):
        return bot.reply_to(message, "⚠️ Сначала настройте текст или фото розыгрыша.")
    session = {
        "active": True, "config": dict(cfg), "owner_id": message.from_user.id,
        "started_at": time.time(), "last_sent_at": 0, "last_message_id": None,
        "participants": {}, "invites": {}, "invited_users": {}, "send_in_progress": False,
        "next_run": _next_run_from_config(cfg),
    }
    def start_mutate(data):
        old=data.get(str(cid))
        if old and old.get("active"):
            old["active"]=False; old["stopped_at"]=time.time()
        data[str(cid)]=session; return data
    db_update_json(KEY, start_mutate, {})
    if session["next_run"] <= time.time():
        _send(cid, session)
        interval = cfg.get("interval_seconds")
        if interval:
            next_run=time.time()+int(interval)
            def set_next(data):
                current=data.get(str(cid))
                if current and current.get("started_at")==session.get("started_at"):
                    current["next_run"]=next_run; current["send_in_progress"]=False
                return data
            db_update_json(KEY, set_next, {})
    else:
        bot.reply_to(message, f"⏰ Розыгрыш запланирован на {cfg.get('start_time')}.")
    return session


def cmd_start(message, args):
    cid = message.chat.id
    if message.chat.type not in ("group", "supergroup"):
        return bot.reply_to(message, "⚠️ Команда записи работает только в группе.")
    if not is_chat_admin(cid, message.from_user.id):
        return bot.reply_to(message, "⛔ Только администратор может запустить запись.")
    raw = (args or "").strip(); parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit() or int(parts[0]) < 1:
        return bot.reply_to(message, "⚠️ Формат: <code>запись 2 текст конкурса</code>")
    required = int(parts[0]); text = parts[1].strip()
    if not text: return bot.reply_to(message, "⚠️ Укажи текст конкурса.")
    if is_active(cid): return bot.reply_to(message, "⚠️ Запись уже идёт. Сначала: <code>стоп запись</code>")
    cfg = __import__("contest_settings").get_config(cid)
    cfg.update({"text": text, "requirement": "invite", "required": required, "start_mode": "now"})
    return start_from_config(message, cfg)


def cmd_stop(message):
    cid = message.chat.id
    if message.chat.type not in ("group", "supergroup"):
        return
    if not is_chat_admin(cid, message.from_user.id):
        bot.reply_to(message, "⛔ Только администратор может остановить запись.")
        return
    changed={"ok":False}
    def stop_mutate(data):
        session=data.get(str(cid))
        if session and session.get("active"):
            session["active"]=False; session["stopped_at"]=time.time(); session["send_in_progress"]=False; changed["ok"]=True
        return data
    db_update_json(KEY, stop_mutate, {})
    if not changed["ok"]:
        bot.reply_to(message, "ℹ️ Активной записи нет.")
        return
    bot.reply_to(message, "🛑 Запись остановлена.")





def _refresh_message(cid, session):
    mid = session.get("last_message_id")
    if not mid: return
    cfg = _config_from_session(session)
    try:
        if cfg.get("photo", {}).get("file_id"):
            bot.edit_message_caption(_render(cid, session), cid, mid, reply_markup=_button_markup(cid, session), parse_mode="HTML")
        else:
            bot.edit_message_text(_render(cid, session), cid, mid, reply_markup=_button_markup(cid, session), parse_mode="HTML")
    except Exception:
        LOG.exception("failed to refresh contest message %s/%s", cid, mid)

def _register(call, cid, session=None):
    user = call.from_user
    uid = user.id
    username = _username(user)
    if not username:
        return bot.answer_callback_query(call.id, "Сначала создай username в Telegram. После этого сможешь записаться.", show_alert=True)

    outcome = {"ok": False, "reason": "not_active", "session": None}

    def mutate(data):
        current = data.get(str(cid))
        if not current or not current.get("active"):
            outcome["reason"] = "not_active"
            return data
        outcome["session"] = current
        participants = current.setdefault("participants", {})
        if str(uid) in participants:
            outcome["reason"] = "already"
            return data
        cfg = _config_from_session(current)
        if cfg.get("requirement", "invite") == "invite":
            invited = current.setdefault("invited_users", {}).get(str(uid), [])
            required = int(cfg.get("required", 1) or 1)
            if len(invited) < required:
                outcome["reason"] = "requirements"
                outcome["remaining"] = required - len(invited)
                return data
        participants[str(uid)] = {
            "id": uid, "username": username,
            "name": user.first_name or username,
            "registered_at": time.time(),
        }
        outcome["ok"] = True
        outcome["reason"] = "ok"
        return data

    data = db_update_json(KEY, mutate, {})
    if not outcome["ok"]:
        reason = outcome["reason"]
        if reason == "already":
            return bot.answer_callback_query(call.id, "Ты уже записан(а).", show_alert=True)
        if reason == "requirements":
            return bot.answer_callback_query(call.id, f"Нужно пригласить ещё {outcome['remaining']} чел.", show_alert=True)
        return bot.answer_callback_query(call.id, "Эта запись уже остановлена.", show_alert=True)
    current = data.get(str(cid))
    bot.answer_callback_query(call.id, "✅ Ты записан(а) в конкурс!")
    _refresh_message(cid, current)


def add_participant_by_username(cid, username):
    """Добавляет username в активный конкурс атомарно."""
    username = str(username or "").strip().lstrip("@").lower()
    if not username:
        return False, "⚠️ Укажи username после @."
    outcome = {"ok": False, "session": None}
    participant_key = f"username:{username}"
    def mutate(data):
        session = data.get(str(cid))
        if not session or not session.get("active"):
            return data
        participants = session.setdefault("participants", {})
        if participant_key in participants:
            outcome["reason"] = "exists"
            outcome["session"] = session
            return data
        participants[participant_key] = {"username": username, "name": username, "registered_at": time.time(), "manual": True}
        outcome["ok"] = True; outcome["session"] = session
        return data
    data=db_update_json(KEY, mutate, {})
    session=data.get(str(cid))
    if not session or not session.get("active"):
        return False, "ℹ️ Активной записи нет."
    if not outcome["ok"]:
        return False, f"ℹ️ @{_escape(username)} уже записан(а)."
    if session.get("last_message_id"):
        _refresh_message(cid, session)
    return True, f"✅ @{_escape(username)} добавлен(а) в список участников."


def remove_participant(cid, participant_key):
    """Удаляет только участие в текущем конкурсе; приглашения не меняет."""
    outcome={"removed":None, "count":0}
    def mutate(data):
        session=data.get(str(cid))
        if not session or not session.get("active"):
            return data
        participants=session.setdefault("participants", {})
        participant=participants.pop(str(participant_key), None)
        if participant is not None:
            outcome["removed"]=participant
        return data
    data=db_update_json(KEY, mutate, {})
    session=data.get(str(cid))
    if not session or not session.get("active"):
        return False, "ℹ️ Активной записи нет."
    participant=outcome["removed"]
    if participant is None:
        return False, "ℹ️ Участник уже удалён или не найден."
    if session.get("last_message_id"):
        _refresh_message(cid, session)
    username=participant.get("username")
    label=f"@{_escape(username)}" if username else _escape(participant.get("name") or participant.get("id") or "участник")
    return True, f"🗑 {label} удалён(а) из списка участников."


def clear_participants(cid):
    """Очищает текущий список участников, не трогая историю приглашений."""
    outcome={"count":0}
    def mutate(data):
        session=data.get(str(cid))
        if not session or not session.get("active"):
            return data
        participants=session.setdefault("participants", {})
        outcome["count"]=len(participants)
        session["participants"]={}
        return data
    data=db_update_json(KEY, mutate, {})
    session=data.get(str(cid))
    if not session or not session.get("active"):
        return False, "ℹ️ Активной записи нет."
    if session.get("last_message_id"):
        _refresh_message(cid, session)
    return True, f"🗑 Список участников очищен. Удалено: {outcome['count']}."


def cmd_add_participant(message, args):
    """Админская ручная запись по username: «записать @username»."""
    cid = message.chat.id
    if message.chat.type not in ("group", "supergroup"):
        return bot.reply_to(message, "⚠️ Команда работает только в группе.")
    if not is_chat_admin(cid, message.from_user.id):
        return bot.reply_to(message, "⛔ Только администратор может добавлять участников.")
    raw = (args or "").strip()
    token = raw.split()[0] if raw else ""
    if not token.startswith("@") or len(token) < 2:
        return bot.reply_to(message, "⚠️ Формат: <code>записать @username</code>")
    ok, text = add_participant_by_username(cid, token)
    return bot.reply_to(message, text, parse_mode="HTML")

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
    if not inviter_id or not user_ids or inviter_id == BOT_ID:
        return False
    changed={"value":False}
    def mutate(data):
        session=data.get(str(cid))
        if not session or not session.get("active"):
            return data
        invited_map=session.setdefault("invited_users", {})
        invited=invited_map.setdefault(str(inviter_id), [])
        existing=set(invited)
        for raw_uid in user_ids:
            try: uid=int(raw_uid)
            except (TypeError,ValueError): continue
            if uid in (inviter_id,BOT_ID) or uid in existing: continue
            invited.append(uid); existing.add(uid); changed["value"]=True
        return data
    db_update_json(KEY, mutate, {})
    return changed["value"]


def handle_new_members(message):
    if getattr(message.chat, "type", "") not in ("group", "supergroup"):
        return
    if not is_active(message.chat.id):
        return
    inviter = getattr(getattr(message, "from_user", None), "id", None)
    members = getattr(message, "new_chat_members", None) or []
    # Запоминаем username/ID новых участников, чтобы админ мог позже
    # использовать «записать @username».
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
    due=[]
    def collect(data):
        for cid_text, session in list(data.items()):
            if not session.get("active"): continue
            if session.get("send_in_progress"): continue
            next_run=float(session.get("next_run") or 0)
            if next_run>now: continue
            session["send_in_progress"]=True
            due.append((int(cid_text), dict(session)))
        return data
    db_update_json(KEY, collect, {})

    for cid, snapshot in due:
        try:
            _send(cid, snapshot)
            interval=_config_from_session(snapshot).get("interval_seconds")
            def finish(data):
                current=data.get(str(cid))
                if not current or current.get("started_at")!=snapshot.get("started_at"):
                    return data
                current["send_in_progress"]=False
                if interval:
                    nr=max(float(current.get("next_run") or 0), now)
                    while nr<=now: nr+=int(interval)
                    current["next_run"]=nr
                else:
                    current["active"]=False
                return data
            db_update_json(KEY, finish, {})
        except Exception:
            LOG.exception("contest periodic send failed for %s", cid)
            def failed(data):
                current=data.get(str(cid))
                if current and current.get("started_at")==snapshot.get("started_at"):
                    current["send_in_progress"]=False
                    current["next_run"]=time.time()+max(10, min(int(_config_from_session(current).get("interval_seconds") or INTERVAL), 300))
                return data
            db_update_json(KEY, failed, {})


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


