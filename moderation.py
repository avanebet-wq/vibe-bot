# -*- coding: utf-8 -*-
"""Модерация — только баны, муты и варны (по требованию: ранги и ириски убраны)."""
import time
import logging
from telebot.types import ChatPermissions

from runtime import bot, BOT_ID
from config import DEFAULT_WARN_LIMIT, DEFAULT_WARN_ACTION, DEFAULT_WARN_MUTE_SECONDS
from database import db_get, db_set
from utils import (
    extract_target, parse_duration, format_seconds, get_mention,
    is_chat_admin, is_protected,
)


def _store():
    return db_get("moderation", {})


def _save(store):
    db_set("moderation", store)


def _chat_bucket(cid):
    store = _store()
    return store.setdefault(str(cid), {"bans": {}, "mutes": {}, "warns": {}})


def _admin_only_reply(message):
    bot.reply_to(message, "⛔ Эта команда только для админов чата.")


def _need_target(message):
    bot.reply_to(
        message,
        "🤔 Не поняла, кого. Ответь этой командой на сообщение человека "
        "или укажи @username.\n\nПример: <code>Лиза, бан @username причина</code>",
    )


# ---------------------------------------------------------------- БАН ----

def cmd_ban(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id):
        return _admin_only_reply(message)

    target_id, target_name, rest = extract_target(message, args_text)
    if not target_id:
        return _need_target(message)
    if is_protected(cid, target_id):
        return bot.reply_to(message, "🙅 Этого пользователя банить нельзя.")

    reason = rest.strip() or "без причины"
    try:
        bot.ban_chat_member(cid, target_id)
    except Exception as e:
        logging.error(f"[ban] {e}")
        return bot.reply_to(message, "⚠️ Не получилось забанить. Проверь, есть ли у меня права на бан.")

    store = _store()
    bucket = store.setdefault(str(cid), {"bans": {}, "mutes": {}, "warns": {}})
    bucket["bans"][str(target_id)] = {
        "name": target_name, "reason": reason,
        "by": message.from_user.id, "at": time.time(),
    }
    _save(store)

    bot.reply_to(
        message,
        f"🔨 {get_mention(target_id, target_name)} забанен(а).\nПричина: {reason}",
    )


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

    store = _store()
    bucket = store.setdefault(str(cid), {"bans": {}, "mutes": {}, "warns": {}})
    bucket["bans"].pop(str(target_id), None)
    _save(store)
    bot.reply_to(message, f"✅ {get_mention(target_id, target_name)} разбанен(а).")


def cmd_banlist(message):
    cid = message.chat.id
    bucket = _chat_bucket(cid)
    bans = bucket.get("bans", {})
    if not bans:
        return bot.reply_to(message, "📋 Банов в этом чате нет.")
    lines = ["📋 <b>Забаненные:</b>"]
    for uid, info in list(bans.items())[:30]:
        lines.append(f"• {get_mention(uid, info.get('name', uid))} — {info.get('reason', '—')}")
    bot.reply_to(message, "\n".join(lines))


# ---------------------------------------------------------------- МУТ ----

def cmd_mute(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id):
        return _admin_only_reply(message)

    target_id, target_name, rest = extract_target(message, args_text)
    if not target_id:
        return _need_target(message)
    if is_protected(cid, target_id):
        return bot.reply_to(message, "🙅 Этого пользователя мутить нельзя.")

    parts = rest.split(maxsplit=1) if rest else []
    duration_str = parts[0] if parts else ""
    reason = parts[1] if len(parts) > 1 else "без причины"
    seconds, ok = parse_duration(duration_str) if duration_str else (0, True)
    if not ok:
        return bot.reply_to(
            message,
            "⚠️ Не поняла срок. Примеры: <code>10м</code>, <code>2ч</code>, <code>1д</code>, "
            "или <code>навсегда</code>.",
        )

    until = int(time.time() + seconds) if seconds else 0
    try:
        bot.restrict_chat_member(
            cid, target_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until if until else None,
        )
    except Exception as e:
        logging.error(f"[mute] {e}")
        return bot.reply_to(message, "⚠️ Не получилось замутить. Проверь мои права.")

    store = _store()
    bucket = store.setdefault(str(cid), {"bans": {}, "mutes": {}, "warns": {}})
    bucket["mutes"][str(target_id)] = {
        "name": target_name, "reason": reason, "until": until,
        "by": message.from_user.id, "at": time.time(),
    }
    _save(store)

    dur_txt = format_seconds(seconds) if seconds else "навсегда"
    bot.reply_to(
        message,
        f"🔇 {get_mention(target_id, target_name)} в муте на {dur_txt}.\nПричина: {reason}",
    )


def cmd_unmute(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id):
        return _admin_only_reply(message)
    target_id, target_name, _ = extract_target(message, args_text)
    if not target_id:
        return _need_target(message)
    try:
        bot.restrict_chat_member(
            cid, target_id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_media_messages=True,
                can_send_other_messages=True, can_add_web_page_previews=True,
            ),
        )
    except Exception as e:
        logging.error(f"[unmute] {e}")
        return bot.reply_to(message, "⚠️ Не получилось снять мут.")

    store = _store()
    bucket = store.setdefault(str(cid), {"bans": {}, "mutes": {}, "warns": {}})
    bucket["mutes"].pop(str(target_id), None)
    _save(store)
    bot.reply_to(message, f"🔊 {get_mention(target_id, target_name)} снова может писать.")


def cmd_mutelist(message):
    cid = message.chat.id
    bucket = _chat_bucket(cid)
    mutes = bucket.get("mutes", {})
    now = time.time()
    active = {uid: info for uid, info in mutes.items() if not info.get("until") or info["until"] > now}
    if not active:
        return bot.reply_to(message, "📋 Активных мутов нет.")
    lines = ["📋 <b>В муте сейчас:</b>"]
    for uid, info in list(active.items())[:30]:
        until = info.get("until")
        left = format_seconds(int(until - now)) if until else "навсегда"
        lines.append(f"• {get_mention(uid, info.get('name', uid))} — ещё {left}")
    bot.reply_to(message, "\n".join(lines))


# --------------------------------------------------------------- ВАРН ----

def cmd_warn(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id):
        return _admin_only_reply(message)

    target_id, target_name, rest = extract_target(message, args_text)
    if not target_id:
        return _need_target(message)
    if is_protected(cid, target_id):
        return bot.reply_to(message, "🙅 Этому пользователю варн не выдать.")

    reason = rest.strip() or "без причины"
    store = _store()
    bucket = store.setdefault(str(cid), {"bans": {}, "mutes": {}, "warns": {}})
    warns = bucket["warns"].setdefault(str(target_id), {"name": target_name, "items": []})
    warns["name"] = target_name
    warns["items"].append({"reason": reason, "by": message.from_user.id, "at": time.time()})
    count = len(warns["items"])
    _save(store)

    limit = DEFAULT_WARN_LIMIT
    text = (
        f"⚠️ {get_mention(target_id, target_name)} получил(а) предупреждение "
        f"({count}/{limit}).\nПричина: {reason}"
    )

    if count >= limit:
        # Автодействие по достижении лимита
        try:
            if DEFAULT_WARN_ACTION == "ban":
                bot.ban_chat_member(cid, target_id)
                text += "\n\n🔨 Лимит варнов исчерпан — бан."
            elif DEFAULT_WARN_ACTION == "kick":
                bot.ban_chat_member(cid, target_id)
                bot.unban_chat_member(cid, target_id)
                text += "\n\n👢 Лимит варнов исчерпан — кик."
            else:
                until = int(time.time() + DEFAULT_WARN_MUTE_SECONDS)
                bot.restrict_chat_member(
                    cid, target_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until,
                )
                text += f"\n\n🔇 Лимит варнов исчерпан — мут на {format_seconds(DEFAULT_WARN_MUTE_SECONDS)}."
            warns["items"] = []  # сбрасываем счётчик после автодействия
            _save(store)
        except Exception as e:
            logging.error(f"[warn autoaction] {e}")
            text += "\n\n⚠️ Не получилось применить автодействие — проверь мои права."

    bot.reply_to(message, text)


def cmd_unwarn(message, args_text):
    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id):
        return _admin_only_reply(message)
    target_id, target_name, _ = extract_target(message, args_text)
    if not target_id:
        return _need_target(message)

    store = _store()
    bucket = store.setdefault(str(cid), {"bans": {}, "mutes": {}, "warns": {}})
    warns = bucket["warns"].get(str(target_id))
    if not warns or not warns.get("items"):
        return bot.reply_to(message, f"У {get_mention(target_id, target_name)} нет предупреждений.")
    warns["items"].pop()
    _save(store)
    bot.reply_to(
        message,
        f"✅ Снято одно предупреждение у {get_mention(target_id, target_name)} "
        f"(осталось {len(warns['items'])}).",
    )


def cmd_mywarns(message):
    cid = message.chat.id
    bucket = _chat_bucket(cid)
    warns = bucket.get("warns", {}).get(str(message.from_user.id))
    items = warns.get("items", []) if warns else []
    if not items:
        return bot.reply_to(message, "🙂 У тебя нет предупреждений.")
    lines = [f"⚠️ У тебя {len(items)}/{DEFAULT_WARN_LIMIT} предупреждений:"]
    for it in items[-10:]:
        lines.append(f"• {it.get('reason', '—')}")
    bot.reply_to(message, "\n".join(lines))


def cmd_warns_of(message, args_text):
    cid = message.chat.id
    target_id, target_name, _ = extract_target(message, args_text)
    if not target_id:
        return _need_target(message)
    bucket = _chat_bucket(cid)
    warns = bucket.get("warns", {}).get(str(target_id))
    items = warns.get("items", []) if warns else []
    if not items:
        return bot.reply_to(message, f"🙂 У {get_mention(target_id, target_name)} нет предупреждений.")
    lines = [f"⚠️ У {get_mention(target_id, target_name)} {len(items)}/{DEFAULT_WARN_LIMIT} предупреждений:"]
    for it in items[-10:]:
        lines.append(f"• {it.get('reason', '—')}")
    bot.reply_to(message, "\n".join(lines))
