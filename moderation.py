# -*- coding: utf-8 -*-
"""Расширенная модерация: временные санкции, история, журнал и гибкие настройки."""
import time
import logging
import threading
from telebot.types import ChatPermissions

from runtime import bot, BOT_ID
from config import DEFAULT_WARN_LIMIT, DEFAULT_WARN_ACTION, DEFAULT_WARN_MUTE_SECONDS
from database import db_get, db_set, db_update_json
from reliability import stopped
from utils import (
    extract_target, parse_duration, format_seconds, get_mention,
    is_chat_admin, is_protected,
)

_LOG_LIMIT = 200
_HISTORY_LIMIT = 200
_LOCK = threading.RLock()

_DEFAULTS = {
    "warn_limit": DEFAULT_WARN_LIMIT,
    "warn_action": DEFAULT_WARN_ACTION,
    "warn_mute_seconds": DEFAULT_WARN_MUTE_SECONDS,
    "auto_delete": True,
    "protect_admins": True,
}


def _store():
    return db_get("moderation", {})


def _save(store):
    db_set("moderation", store)


def _chat_bucket(cid):
    store = _store()
    bucket = store.setdefault(str(cid), {})
    bucket.setdefault("bans", {})
    bucket.setdefault("mutes", {})
    bucket.setdefault("warns", {})
    bucket.setdefault("history", [])
    bucket.setdefault("action_log", [])
    cfg = bucket.setdefault("config", {})
    for key, value in _DEFAULTS.items():
        cfg.setdefault(key, value)
    return bucket




def update_chat_config(cid, **fields):
    """Атомарно меняет конфигурацию модерации одного чата."""
    def mutate(store):
        bucket = store.setdefault(str(cid), {})
        bucket.setdefault("bans", {})
        bucket.setdefault("mutes", {})
        bucket.setdefault("warns", {})
        bucket.setdefault("history", [])
        bucket.setdefault("action_log", [])
        cfg = bucket.setdefault("config", {})
        for key, value in _DEFAULTS.items():
            cfg.setdefault(key, value)
        cfg.update(fields)
        return store
    result = db_update_json("moderation", mutate, {})
    return result.get(str(cid), {})

def _cfg(bucket, key):
    return bucket.get("config", {}).get(key, _DEFAULTS[key])


def _append_limited(items, item, limit):
    items.append(item)
    if len(items) > limit:
        del items[:-limit]


def _log_action(cid, action, target_id=None, target_name=None, actor_id=None,
                reason="", duration=0, message_id=None):
    store = _store()
    bucket = _chat_bucket(cid)
    entry = {
        "action": action,
        "target_id": target_id,
        "target_name": target_name,
        "by": actor_id,
        "reason": reason,
        "duration": duration,
        "at": time.time(),
        "message_id": message_id,
    }
    _append_limited(bucket["history"], entry, _HISTORY_LIMIT)
    _append_limited(bucket["action_log"], entry, _LOG_LIMIT)
    _save(store)
    try:
        from stats import record_moderation
        record_moderation(cid, action)
    except Exception:
        pass


def _admin_only_reply(message):
    bot.reply_to(message, "⛔ Эта команда только для админов чата.")


def _need_target(message):
    bot.reply_to(
        message,
        "🤔 Не поняла, кого. Ответь этой командой на сообщение человека "
        "или укажи @username.\n\nПример: <code>бан @username причина</code>",
    )


def _target_is_protected(cid, target_id, bucket=None):
    if is_protected(cid, target_id):
        return True
    bucket = bucket or _chat_bucket(cid)
    if _cfg(bucket, "protect_admins"):
        try:
            member = bot.get_chat_member(cid, target_id)
            return member.status in ("administrator", "creator")
        except Exception:
            return False
    return False


def _try_delete_violation(message, bucket):
    if not _cfg(bucket, "auto_delete") or not getattr(message, "reply_to_message", None):
        return False
    target = message.reply_to_message
    try:
        bot.delete_message(message.chat.id, target.message_id)
        return True
    except Exception as e:
        logging.debug(f"[moderation delete] {e}")
        return False


# ---------------------------------------------------------------- БАН ----

def cmd_ban(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id):
        return _admin_only_reply(message)

    target_id, target_name, rest = extract_target(message, args_text)
    if not target_id:
        return _need_target(message)
    bucket = _chat_bucket(cid)
    if _target_is_protected(cid, target_id, bucket):
        return bot.reply_to(message, "🙅 Администраторов и Лизу банить нельзя.")

    parts = rest.split(maxsplit=1) if rest else []
    duration = 0
    reason = rest.strip() or "без причины"
    if parts:
        parsed, ok = parse_duration(parts[0])
        if ok:
            duration = parsed
            reason = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "без причины"

    until = int(time.time() + duration) if duration else 0
    try:
        bot.ban_chat_member(cid, target_id, until_date=until if until else None)
    except Exception as e:
        logging.error(f"[ban] {e}")
        return bot.reply_to(message, "⚠️ Не получилось забанить. Проверь, есть ли у меня права на бан.")

    store = _store()
    bucket = _chat_bucket(cid)
    bucket["bans"][str(target_id)] = {
        "name": target_name, "reason": reason,
        "by": message.from_user.id, "at": time.time(), "until": until,
    }
    _save(store)
    _log_action(cid, "ban", target_id, target_name, message.from_user.id, reason, duration,
                getattr(getattr(message, "reply_to_message", None), "message_id", None))
    deleted = _try_delete_violation(message, bucket)

    dur_txt = format_seconds(duration) if duration else "навсегда"
    extra = "\n🗑️ Нарушение удалено." if deleted else ""
    bot.reply_to(message, f"🔨 {get_mention(target_id, target_name)} забанен(а) на {dur_txt}.\nПричина: {reason}{extra}")


def cmd_unban(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id):
        return _admin_only_reply(message)
    target_id, target_name, _ = extract_target(message, args_text)
    if not target_id:
        return _need_target(message)
    try:
        bot.unban_chat_member(cid, target_id, only_if_banned=True)
    except Exception as e:
        logging.error(f"[unban] {e}")
        return bot.reply_to(message, "⚠️ Не получилось снять бан.")

    store = _store(); bucket = _chat_bucket(cid)
    old = bucket["bans"].pop(str(target_id), None)
    _save(store)
    _log_action(cid, "unban", target_id, target_name, message.from_user.id,
                "снятие бана", 0, getattr(getattr(message, "reply_to_message", None), "message_id", None))
    bot.reply_to(message, f"✅ {get_mention(target_id, target_name)} разбанен(а).")


def cmd_banlist(message):
    cid = message.chat.id; bucket = _chat_bucket(cid); bans = bucket.get("bans", {})
    now = time.time()
    active = {uid: info for uid, info in bans.items() if not info.get("until") or info["until"] > now}
    if not active:
        return bot.reply_to(message, "📋 Активных банов в этом чате нет.")
    lines = ["📋 <b>Забаненные:</b>"]
    for uid, info in list(active.items())[:30]:
        until = info.get("until")
        left = format_seconds(int(until-now)) if until else "навсегда"
        lines.append(f"• {get_mention(uid, info.get('name', uid))} — {left} — {info.get('reason', '—')}")
    bot.reply_to(message, "\n".join(lines))


# ---------------------------------------------------------------- МУТ ----

def cmd_mute(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id):
        return _admin_only_reply(message)
    target_id, target_name, rest = extract_target(message, args_text)
    if not target_id: return _need_target(message)
    bucket = _chat_bucket(cid)
    if _target_is_protected(cid, target_id, bucket):
        return bot.reply_to(message, "🙅 Администраторов и Лизу мутить нельзя.")

    parts = rest.split(maxsplit=1) if rest else []
    duration_str = parts[0] if parts else ""
    reason = parts[1] if len(parts) > 1 else "без причины"
    seconds, ok = parse_duration(duration_str) if duration_str else (0, True)
    if not ok:
        return bot.reply_to(message, "⚠️ Не поняла срок. Примеры: <code>10м</code>, <code>2ч</code>, <code>1д</code>, или <code>навсегда</code>.")

    until = int(time.time() + seconds) if seconds else 0
    try:
        bot.restrict_chat_member(cid, target_id, permissions=ChatPermissions(can_send_messages=False), until_date=until if until else None)
    except Exception as e:
        logging.error(f"[mute] {e}")
        return bot.reply_to(message, "⚠️ Не получилось замутить. Проверь мои права.")

    store = _store(); bucket = _chat_bucket(cid)
    bucket["mutes"][str(target_id)] = {"name": target_name, "reason": reason, "until": until, "by": message.from_user.id, "at": time.time()}
    _save(store)
    _log_action(cid, "mute", target_id, target_name, message.from_user.id, reason, seconds,
                getattr(getattr(message, "reply_to_message", None), "message_id", None))
    deleted = _try_delete_violation(message, bucket)
    dur_txt = format_seconds(seconds) if seconds else "навсегда"
    extra = "\n🗑️ Нарушение удалено." if deleted else ""
    bot.reply_to(message, f"🔇 {get_mention(target_id, target_name)} в муте на {dur_txt}.\nПричина: {reason}{extra}")


def cmd_unmute(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id): return _admin_only_reply(message)
    target_id, target_name, _ = extract_target(message, args_text)
    if not target_id: return _need_target(message)
    try:
        bot.restrict_chat_member(cid, target_id, permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True, can_add_web_page_previews=True))
    except Exception as e:
        logging.error(f"[unmute] {e}")
        return bot.reply_to(message, "⚠️ Не получилось снять мут.")
    def mutate(store):
        bucket=store.setdefault(str(cid), {})
        bucket.setdefault("mutes", {}).pop(str(target_id), None)
        return store
    db_update_json("moderation", mutate, {})
    _log_action(cid, "unmute", target_id, target_name, message.from_user.id, "снятие мута")
    bot.reply_to(message, f"🔊 {get_mention(target_id, target_name)} снова может писать.")


def cmd_mutelist(message):
    cid = message.chat.id; bucket = _chat_bucket(cid); mutes = bucket.get("mutes", {}); now = time.time()
    active = {uid: info for uid, info in mutes.items() if not info.get("until") or info["until"] > now}
    if not active: return bot.reply_to(message, "📋 Активных мутов нет.")
    lines = ["📋 <b>В муте сейчас:</b>"]
    for uid, info in list(active.items())[:30]:
        until = info.get("until"); left = format_seconds(int(until-now)) if until else "навсегда"
        lines.append(f"• {get_mention(uid, info.get('name', uid))} — ещё {left} — {info.get('reason', '—')}")
    bot.reply_to(message, "\n".join(lines))


# --------------------------------------------------------------- ВАРН ----

def cmd_warn(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id): return _admin_only_reply(message)
    target_id, target_name, rest = extract_target(message, args_text)
    if not target_id: return _need_target(message)
    bucket = _chat_bucket(cid)
    if _target_is_protected(cid, target_id, bucket):
        return bot.reply_to(message, "🙅 Администраторов и Лизу нельзя предупреждать.")

    reason = rest.strip() or "без причины"
    store = _store(); bucket = _chat_bucket(cid)
    warns = bucket["warns"].setdefault(str(target_id), {"name": target_name, "items": []})
    warns["name"] = target_name
    warns["items"].append({"reason": reason, "by": message.from_user.id, "at": time.time()})
    count = len(warns["items"]); limit = int(_cfg(bucket, "warn_limit"))
    _save(store)
    _log_action(cid, "warn", target_id, target_name, message.from_user.id, reason,
                0, getattr(getattr(message, "reply_to_message", None), "message_id", None))

    text = f"⚠️ {get_mention(target_id, target_name)} получил(а) предупреждение ({count}/{limit}).\nПричина: {reason}"
    deleted = _try_delete_violation(message, bucket)
    if deleted: text += "\n🗑️ Нарушение удалено."

    if count >= limit:
        action = _cfg(bucket, "warn_action")
        try:
            if action == "ban":
                bot.ban_chat_member(cid, target_id)
                bucket["bans"][str(target_id)] = {"name": target_name, "reason": "лимит варнов", "by": message.from_user.id, "at": time.time(), "until": 0}
                text += "\n\n🔨 Лимит варнов исчерпан — бан."
            elif action == "kick":
                bot.ban_chat_member(cid, target_id); bot.unban_chat_member(cid, target_id)
                text += "\n\n👢 Лимит варнов исчерпан — кик."
            else:
                seconds = int(_cfg(bucket, "warn_mute_seconds")); until = int(time.time()+seconds)
                bot.restrict_chat_member(cid, target_id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
                bucket["mutes"][str(target_id)] = {"name": target_name, "reason": "лимит варнов", "until": until, "by": message.from_user.id, "at": time.time()}
                text += f"\n\n🔇 Лимит варнов исчерпан — мут на {format_seconds(seconds)}."
            _log_action(cid, "warn_autoaction", target_id, target_name, message.from_user.id, "лимит варнов", 0)
            warns["items"] = []
            _save(store)
        except Exception as e:
            logging.error(f"[warn autoaction] {e}")
            text += "\n\n⚠️ Не получилось применить автодействие — проверь мои права."
    bot.reply_to(message, text)


def cmd_unwarn(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id): return _admin_only_reply(message)
    target_id, target_name, _ = extract_target(message, args_text)
    if not target_id: return _need_target(message)
    store = _store(); bucket = _chat_bucket(cid); warns = bucket["warns"].get(str(target_id))
    if not warns or not warns.get("items"): return bot.reply_to(message, f"У {get_mention(target_id, target_name)} нет предупреждений.")
    warns["items"].pop(); _save(store); _log_action(cid, "unwarn", target_id, target_name, message.from_user.id, "снятие варна")
    bot.reply_to(message, f"✅ Снято одно предупреждение у {get_mention(target_id, target_name)} (осталось {len(warns['items'])}).")


def cmd_mywarns(message):
    bucket = _chat_bucket(message.chat.id); warns = bucket["warns"].get(str(message.from_user.id)); items = warns.get("items", []) if warns else []
    limit = int(_cfg(bucket, "warn_limit"))
    if not items: return bot.reply_to(message, "🙂 У тебя нет предупреждений.")
    lines = [f"⚠️ У тебя {len(items)}/{limit} предупреждений:"]
    for it in items[-10:]: lines.append(f"• {it.get('reason', '—')}")
    bot.reply_to(message, "\n".join(lines))


def cmd_warns_of(message, args_text):
    cid = message.chat.id; target_id, target_name, _ = extract_target(message, args_text)
    if not target_id: return _need_target(message)
    bucket = _chat_bucket(cid); warns = bucket["warns"].get(str(target_id)); items = warns.get("items", []) if warns else []; limit = int(_cfg(bucket, "warn_limit"))
    if not items: return bot.reply_to(message, f"🙂 У {get_mention(target_id, target_name)} нет предупреждений.")
    lines = [f"⚠️ У {get_mention(target_id, target_name)} {len(items)}/{limit} предупреждений:"]
    for it in items[-10:]: lines.append(f"• {it.get('reason', '—')}")
    bot.reply_to(message, "\n".join(lines))


# ----------------------------------------------------------- НАСТРОЙКИ ----

def _admin_guard(message):
    if not is_chat_admin(message.chat.id, message.from_user.id):
        _admin_only_reply(message); return False
    return True


def cmd_moderation_settings(message):
    if not _admin_guard(message): return
    bucket = _chat_bucket(message.chat.id); cfg = bucket["config"]
    action = cfg["warn_action"]
    delete = "вкл" if cfg["auto_delete"] else "выкл"
    protect = "вкл" if cfg["protect_admins"] else "выкл"
    bot.reply_to(message, f"🛡️ <b>Модерация</b>\nВарны: {cfg['warn_limit']}\nАвтодействие: {action}\nМут за лимит: {format_seconds(int(cfg['warn_mute_seconds']))}\nУдаление нарушений: {delete}\nЗащита админов: {protect}")


def cmd_set_warn_limit(message, args_text):
    if not _admin_guard(message): return
    try: value = int(args_text.strip())
    except Exception: return bot.reply_to(message, "⚠️ Укажи число от 1 до 20.")
    if not 1 <= value <= 20: return bot.reply_to(message, "⚠️ Лимит должен быть от 1 до 20.")
    update_chat_config(message.chat.id, warn_limit=value)
    _log_action(message.chat.id, "settings", actor_id=message.from_user.id, reason=f"лимит варнов: {value}")
    bot.reply_to(message, f"✅ Лимит варнов: {value}.")


def cmd_set_warn_action(message, args_text):
    if not _admin_guard(message): return
    action = args_text.strip().lower()
    aliases = {"мут":"mute", "бан":"ban", "кик":"kick", "mute":"mute", "ban":"ban", "kick":"kick"}
    action = aliases.get(action)
    if action not in ("mute", "ban", "kick"): return bot.reply_to(message, "⚠️ Варианты: <code>мут</code>, <code>бан</code>, <code>кик</code>.")
    update_chat_config(message.chat.id, warn_action=action)
    _log_action(message.chat.id, "settings", actor_id=message.from_user.id, reason=f"автодействие варнов: {action}")
    bot.reply_to(message, f"✅ Автодействие за лимит варнов: {action}.")


def cmd_set_warn_mute_duration(message, args_text):
    if not _admin_guard(message): return
    seconds, ok = parse_duration(args_text.strip())
    if not ok or seconds <= 0: return bot.reply_to(message, "⚠️ Пример: <code>2ч</code> или <code>30м</code>.")
    update_chat_config(message.chat.id, warn_mute_seconds=seconds)
    _log_action(message.chat.id, "settings", actor_id=message.from_user.id, reason=f"мут за варны: {seconds}с")
    bot.reply_to(message, f"✅ Мут за лимит варнов: {format_seconds(seconds)}.")


def cmd_set_auto_delete(message, enabled):
    if not _admin_guard(message): return
    update_chat_config(message.chat.id, auto_delete=bool(enabled))
    _log_action(message.chat.id, "settings", actor_id=message.from_user.id, reason=f"удаление нарушений: {'вкл' if enabled else 'выкл'}")
    bot.reply_to(message, f"✅ Автоудаление нарушений: {'включено' if enabled else 'выключено'}.")


def cmd_set_protect_admins(message, enabled):
    if not _admin_guard(message): return
    update_chat_config(message.chat.id, protect_admins=bool(enabled))
    _log_action(message.chat.id, "settings", actor_id=message.from_user.id, reason=f"защита админов: {'вкл' if enabled else 'выкл'}")
    bot.reply_to(message, f"✅ Защита администраторов: {'включена' if enabled else 'выключена'}.")


def cmd_modlog(message, args_text=""):
    if not is_chat_admin(message.chat.id, message.from_user.id): return _admin_only_reply(message)
    bucket = _chat_bucket(message.chat.id); history = bucket.get("history", [])
    if not history: return bot.reply_to(message, "📜 Журнал модерации пуст.")
    lines = ["📜 <b>Журнал модерации:</b>"]
    for item in history[-15:][::-1]:
        ts = time.strftime("%d.%m %H:%M", time.localtime(item.get("at", 0)))
        action = item.get("action", "?")
        target = item.get("target_name") or item.get("target_id") or "—"
        reason = item.get("reason") or "—"
        lines.append(f"• {ts} — {action} — {target} — {reason}")
    bot.reply_to(message, "\n".join(lines))


# --------------------------------------------------------- АВТОСБРОС ----

def process_expired_moderation():
    """Снимает истёкшие санкции без удержания общего lock во время Telegram API."""
    now=time.time(); expired=[]
    def collect(store):
        for cid_str,bucket in list(store.items()):
            try: cid=int(cid_str)
            except Exception: continue
            for uid,info in list((bucket.get("bans") or {}).items()):
                until=info.get("until",0)
                if until and until<=now: expired.append(("ban",cid,uid,info))
            for uid,info in list((bucket.get("mutes") or {}).items()):
                until=info.get("until",0)
                if until and until<=now: expired.append(("mute",cid,uid,info))
        return store
    db_update_json("moderation", collect, {})
    for kind,cid,uid,info in expired:
        success=True
        if kind=="ban":
            try: bot.unban_chat_member(cid,int(uid),only_if_banned=True)
            except Exception as exc:
                success=False; logging.debug("[ban expiry %s/%s] %s",cid,uid,exc)
        elif kind=="mute":
            try:
                bot.restrict_chat_member(
                    cid, int(uid),
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_media_messages=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                    ),
                )
            except Exception as exc:
                success=False; logging.debug("[mute expiry %s/%s] %s",cid,uid,exc)
        def finalize(store):
            bucket=store.get(str(cid))
            if not bucket: return store
            section=bucket.get("bans" if kind=="ban" else "mutes", {})
            current=section.get(str(uid))
            if current and current.get("until",0) and current.get("until",0)<=now and success:
                section.pop(str(uid),None)
                if kind=="ban":
                    _append_limited(bucket.setdefault("history",[]), {"action":"ban_expired","target_id":int(uid),"target_name":info.get("name"),"at":now,"reason":"срок истёк"}, _HISTORY_LIMIT)
            return store
        db_update_json("moderation", finalize, {})


def start_moderation_scheduler():
    def loop():
        while not stopped():
            try: process_expired_moderation()
            except Exception as e: logging.error(f"[moderation scheduler] {e}", exc_info=True)
            if stopped():
                break
            from reliability import _STOP
            _STOP.wait(30)
    threading.Thread(target=loop, daemon=True, name="liza-moderation-scheduler").start()

def _with_moderation_lock(fn):
    def wrapped(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    wrapped.__name__ = getattr(fn, "__name__", "moderation_handler")
    wrapped.__doc__ = fn.__doc__
    return wrapped

for _name in (
    "cmd_ban", "cmd_unban", "cmd_mute", "cmd_unmute",
    "cmd_warn", "cmd_unwarn", "cmd_set_warn_limit",
    "cmd_set_warn_action", "cmd_set_warn_mute_duration",
    "cmd_set_auto_delete", "cmd_set_protect_admins",
):
    _fn = globals().get(_name)
    if _fn is not None and not getattr(_fn, "_liza_locked", False):
        _wrapped = _with_moderation_lock(_fn)
        _wrapped._liza_locked = True
        globals()[_name] = _wrapped

