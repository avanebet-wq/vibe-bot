# -*- coding: utf-8 -*-
"""Меню настроек Лизы: капча, повторяющиеся публикации, удаление сообщений."""
import re
import time
import json
import logging
import threading
import os
import html
from urllib.parse import urlencode, urlparse, quote
from collections import deque
from datetime import datetime, timedelta

from telebot import types
from telebot.apihelper import ApiTelegramException
from telebot.types import ChatPermissions

from runtime import bot, BOT_ID, BOT_USERNAME
from config import TZ, MINIAPP_PUBLIC_URL
from utils import is_chat_admin, parse_duration, format_seconds, get_mention
from reliability import stopped

import settings_store as store
import settings_ui as ui
import premium_emoji as pe
import rich_text as rt

log = logging.getLogger("settings")

# Кольцевой буфер последних message_id по каждому чату — для массового удаления.
# Только в памяти: переживает работу процесса, но не перезапуск (это ок для этой функции).
_MAX_TRACKED = 2000
_recent_messages = {}
_recent_lock = threading.RLock()


def track_message(chat_id, message_id, chat_title=None):
    # Не вызываем get_chat() на каждое сообщение: title/type уже приходят
    # в Telegram update для входящего сообщения. Для внутренних исходящих
    # сообщений registry обновляется только при явном переданном названии.
    if chat_title:
        try:
            store.register_known_group(chat_id, chat_title)
        except Exception as e:
            log.debug("[known_groups] не удалось обновить чат %s: %s", chat_id, e)
    with _recent_lock:
        dq = _recent_messages.setdefault(chat_id, deque(maxlen=_MAX_TRACKED))
        dq.append(message_id)


def _tracked_ids(chat_id):
    with _recent_lock:
        return list(_recent_messages.get(chat_id, ()))


# =============================================================================
# Навигация: открыть тот или иной "экран" меню в конкретном чате/сообщении
# =============================================================================

def _authorized(gid, uid):
    return is_chat_admin(gid, uid)


def _chat_title(gid):
    try:
        chat = bot.get_chat(gid)
        title = chat.title or str(gid)
        store.register_known_group(gid, title)
        return title
    except Exception:
        return str(gid)


def _render(target, gid, pid=None):
    if target == "root":
        return ui.root_text(_chat_title(gid)), ui.root_kb(gid)
    if target == "settings_liza":
        return ui.liza_settings_text(_chat_title(gid)), ui.liza_settings_kb(gid)
    if target == "settings_chat":
        return ui.chat_settings_text(_chat_title(gid)), ui.chat_settings_kb(gid)
    if target == "liza":
        return ui.liza_text(gid), ui.liza_kb(gid)
    if target == "chance":
        return ui.chance_text(gid), ui.chance_kb(gid)
    if target == "sleep":
        return ui.sleep_text(gid), ui.sleep_kb(gid)
    if target == "mem":
        return ui.memory_text(gid), ui.memory_kb(gid)
    if target == "mem_clear_confirm":
        return ("⚠️ <b>Очистить память чата?</b>\n\nБудут удалены все сохранённые факты пользователей этого чата. Другие чаты и настройки не затрагиваются."), ui._kb([
            [ui._btn("✅ Да, очистить", "mem_clear", gid), ui._btn("❌ Отмена", "back", gid, "mem")]
        ])
    if target == "fun":
        return ui.fun_text(gid), ui.fun_kb(gid)
    if target == "chat":
        return ui.chat_text(gid), ui.chat_kb(gid)
    if target == "mod":
        return ui.mod_text(gid), ui.mod_kb(gid)
    if target == "pers":
        return ui.personality_text(gid), ui.personality_kb(gid)
    if target == "status":
        return ui.status_text(gid), ui.status_kb(gid)
    if target == "reset":
        return ui.reset_text(), ui.reset_kb(gid)
    if target == "reset_confirm":
        return ("⚠️ <b>Подтверждение сброса</b>\n\n"
                "Будут сброшены только настройки поведения Лизы: ответы, активность, память-флаг, развлечения и стиль.\n\n"
                "Действие можно повторить, но вернуть пользовательские данные из этого меню нельзя."), ui._kb([
                    [ui._btn("✅ Да, сбросить", "reset_do", gid), ui._btn("❌ Отмена", "back", gid, "reset")]
                ])
    if target == "security":
        return ui.security_text(_chat_title(gid)), ui.security_kb(gid)
    if target == "cap":
        return ui.captcha_text(gid), ui.captcha_kb(gid)
    if target == "cap_type":
        return ui.cap_type_text(gid), ui.cap_type_kb(gid)
    if target == "pst":
        return ui.posts_list_text(gid), ui.posts_list_kb(gid)
    if target == "popen":
        return ui.post_edit_text(gid, pid), ui.post_edit_kb(gid, pid)
    if target == "pmsg":
        return ui.post_message_text(gid, pid), ui.post_message_kb(gid, pid)
    if target == "pwd":
        return ui.post_edit_text(gid, pid) + "\n\n🗓️ Выберите дни недели:", ui.weekdays_kb(gid, pid)
    if target == "pmd":
        return ui.post_edit_text(gid, pid) + "\n\n📆 Выберите дни месяца:", ui.monthdays_kb(gid, pid)
    if target == "del":
        return ui.deletion_text(), ui.deletion_kb(gid)
    if target == "delsys":
        return ui.sysmsgs_text(), ui.sysmsgs_kb(gid)
    if target == "delmass":
        return ui.massdel_text(), ui.massdel_kb(gid)
    return ui.root_text(_chat_title(gid)), ui.root_kb(gid)


def _show(chat_id, message_id, target, gid, pid=None):
    text, kb = _render(target, gid, pid)
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                               reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        if "message is not modified" not in str(e):
            log.warning(f"[settings show] {e}")


def _send(chat_id, target, gid, pid=None, thread_id=None):
    text, kb = _render(target, gid, pid)
    msg = bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML",
                            message_thread_id=thread_id)
    track_message(chat_id, msg.message_id)
    return msg


# =============================================================================
# Добавление бота в группу — приветствие + /start в разных контекстах
# =============================================================================

MANUAL_URL = "https://telegra.ph/Liza--manual-po-botu-09-07"


def _addbot_url():
    return f"https://t.me/{BOT_USERNAME}?startgroup=true"


def _offer_settings(chat_id, thread_id=None):
    text = (
        "👋Всем привет, спасибо что добавили\n\n"
        "👅Я — Лиза, развлеку и помогу с самым нужным😼\n\n"
        "Для начала напишите /start"
    )
    msg = bot.send_message(chat_id, text, message_thread_id=thread_id)
    track_message(chat_id, msg.message_id)


def send_dm_start_intro(chat_id):
    text = (
        "Привет! Я — Лиза👅, умный ИИ-помощник для вашего чата😼\n\n"
        "Я умею:\n"
        "• 💬 Отвечать на сообщения и поддерживать общение с участниками\n"
        "• 🛡️ Помогать с модерацией: бан, мут, варн и другие действия\n"
        "• 📢 Автоматически публиковать посты и настраивать автопостинг\n"
        "• ⚙️ Гибко настраиваться под правила и формат вашего чата\n"
        "• 🧠 Использовать ИИ для общения и помощи участникам\n\n"
        "Чтобы попробовать мои возможности, просто добавь меня в свой чат и напиши:\n\n"
        "«настройки»\n\n"
        "Я покажу доступные функции и помогу всё настроить.\n\n"
        f'👀 <a href="{MANUAL_URL}">Все команды и возможности</a>'
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("➕ Добавить в чат", url=_addbot_url()))
    msg = bot.send_message(chat_id, text, reply_markup=kb)
    track_message(chat_id, msg.message_id)


def _bot_is_in_chat(gid):
    """Проверяет, что бот всё ещё состоит в чате из постоянного реестра."""
    try:
        member = bot.get_chat_member(gid, BOT_ID)
        return member.status not in ("left", "kicked")
    except Exception:
        return False


def send_dm_start_group_picker(chat_id, user_id):
    """Показывает чаты из постоянного реестра, где бот состоит, а пользователь админ."""
    groups = store.get_known_groups()
    my_groups = []
    stale = []
    for gid_str, title in groups.items():
        try:
            gid = int(gid_str)
        except (TypeError, ValueError):
            continue
        if not _bot_is_in_chat(gid):
            stale.append(gid)
            continue
        if _authorized(gid, user_id):
            actual_title = _chat_title(gid)
            my_groups.append((gid, actual_title if actual_title != str(gid) else (title or str(gid))))

    for gid in stale:
        try:
            store.remove_known_group(gid)
        except Exception:
            log.exception("[known_groups] failed to remove stale chat %s", gid)

    if not my_groups:
        return False

    text = (
        "👋 Я — Лиза, ИИ-помощник ваших чатов.\n\n"
        "Я готова помогать с общением, модерацией и другими задачами 😼\n\n"
        "Выберите чат ниже, чтобы открыть настройки👇"
    )
    kb = types.InlineKeyboardMarkup()
    for gid, title in my_groups:
        kb.row(types.InlineKeyboardButton(title, callback_data=f"cf|selectgroup|{gid}"))
    msg = bot.send_message(chat_id, text, reply_markup=kb)
    track_message(chat_id, msg.message_id)
    return True


def send_group_start(message):
    gid = message.chat.id
    text = (
        "👋 Я — Лиза, ИИ-помощник этого чата👅\n\n"
        "Готова помогать с общением, модерацией и автоматизацией😼\n\n"
        "Выберите действие ниже, чтобы начать настройку👇"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("⚙️ Настройки", callback_data=f"cf|askwhere|{gid}"))
    msg = bot.send_message(gid, text, reply_markup=kb,
                            message_thread_id=getattr(message, "message_thread_id", None))
    track_message(gid, msg.message_id)


def open_settings_in_dm(user_id, gid):
    if not _authorized(gid, user_id):
        bot.send_message(user_id, "⛔ Вы не администратор этого чата.")
        return
    store.set_active_group(user_id, gid)
    text, kb = _render("root", gid)
    msg = bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML")
    track_message(msg.chat.id, msg.message_id)


def cmd_settings_command(message):
    if message.chat.type == "private":
        if send_dm_start_group_picker(message.chat.id, message.from_user.id):
            return
        return bot.reply_to(
            message,
            "⛔ Я не нашла чатов, где я добавлена и где вы являетесь администратором."
        )

    gid = message.chat.id
    uid = message.from_user.id
    if not _authorized(gid, uid):
        return bot.reply_to(message, "⛔ Эта команда только для админов чата.")

    store.set_active_group(uid, gid)
    text, kb = _render("root", gid)
    try:
        msg = bot.send_message(uid, text, reply_markup=kb, parse_mode="HTML")
        track_message(msg.chat.id, msg.message_id)
        bot.reply_to(message, "⚙️ Отправила выбор настроек вам в личные сообщения.")
    except Exception:
        fallback = types.InlineKeyboardMarkup()
        fallback.row(types.InlineKeyboardButton(
            "⚙️ Открыть настройки в ЛС",
            url=f"https://t.me/{BOT_USERNAME}?start=cfg-{gid}"
        ))
        msg = bot.reply_to(
            message,
            "📩 Сначала откройте мои личные сообщения, затем нажмите кнопку ниже — настройки откроются там.",
            reply_markup=fallback,
        )
        track_message(gid, msg.message_id)


# =============================================================================
# Капча
# =============================================================================

def _schedule_message_autodelete(chat_id, message_id, delay=10.0):
    """Удаляет сообщение через `delay` секунд, если оно ещё существует."""
    def _job():
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
    threading.Timer(delay, _job).start()


def handle_new_members(message):
    gid = message.chat.id
    for user in message.new_chat_members:
        if user.id == BOT_ID:
            store.register_known_group(gid, message.chat.title)
            _offer_settings(gid, thread_id=getattr(message, "message_thread_id", None))
            continue

        captcha = store.get_captcha(gid)
        # Капча «подписка на канал» не блокирует вход — она проверяется прямо
        # при попытке написать сообщение (см. enforce_captcha ниже).
        if captcha.get("enabled", False) and captcha.get("type", "button") == "button":
            _start_captcha(message, user)


def _button_captcha_prompt_kb(gid, uid):
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("✅ Я не робот", callback_data=f"cf|capver|{gid}|{uid}"))
    return kb


def _send_button_captcha_prompt(gid, user, thread_id=None):
    mention = get_mention(user.id, user.first_name or user.username or str(user.id))
    text = (
        f"🧠 {mention}, подтвердите, что вы не робот, нажав на кнопку ниже 👇\n"
        "Пока вы этого не сделаете, писать в чат нельзя."
    )
    sent = bot.send_message(gid, text, reply_markup=_button_captcha_prompt_kb(gid, user.id),
                             message_thread_id=thread_id)
    track_message(gid, sent.message_id)
    store.set_captcha_pending(gid, user.id, {
        "msg_chat": sent.chat.id, "msg_id": sent.message_id, "joined_at": time.time(),
    })
    _schedule_message_autodelete(sent.chat.id, sent.message_id, 10.0)
    return sent


def _start_captcha(message, user):
    gid = message.chat.id
    try:
        bot.restrict_chat_member(gid, user.id, permissions=ChatPermissions(can_send_messages=False))
    except Exception as e:
        log.warning(f"[captcha restrict] {e}")

    _send_button_captcha_prompt(gid, user, thread_id=getattr(message, "message_thread_id", None))


def _reprompt_button_captcha(message, uid):
    """Пользователь ещё не прошёл капчу и попытался написать — напоминаем."""
    gid = message.chat.id
    pending = store.get_captcha_pending(gid, uid)
    if pending:
        try:
            bot.delete_message(pending["msg_chat"], pending["msg_id"])
        except Exception:
            pass
    _send_button_captcha_prompt(gid, message.from_user, thread_id=getattr(message, "message_thread_id", None))


def _is_subscribed(channel_id, user_id):
    try:
        member = bot.get_chat_member(channel_id, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning(f"[captcha subscribe check] {e}")
        return False


def _channel_invite_link(channel_id):
    try:
        chat = bot.get_chat(channel_id)
        if getattr(chat, "username", None):
            return f"https://t.me/{chat.username}"
        if getattr(chat, "invite_link", None):
            return chat.invite_link
        return bot.export_chat_invite_link(channel_id)
    except Exception as e:
        log.warning(f"[captcha channel link] {e}")
        return None


def _enforce_subscribe_captcha(message):
    gid = message.chat.id
    uid = message.from_user.id
    captcha = store.get_captcha(gid)
    channel_id = captcha.get("channel_id")
    if not channel_id:
        return False

    pending = store.get_captcha_sub_prompt(gid, uid)

    if _is_subscribed(channel_id, uid):
        # Пользователь подписан — убираем напоминание, если оно ещё висит,
        # и пропускаем сообщение.
        if pending:
            try:
                bot.delete_message(pending["msg_chat"], pending["msg_id"])
            except Exception:
                pass
            store.clear_captcha_sub_prompt(gid, uid)
        return False

    try:
        bot.delete_message(gid, message.message_id)
    except Exception:
        pass

    if pending:
        try:
            bot.delete_message(pending["msg_chat"], pending["msg_id"])
        except Exception:
            pass

    kb = types.InlineKeyboardMarkup()
    link = _channel_invite_link(channel_id)
    if link:
        kb.row(types.InlineKeyboardButton("📢 Подписаться", url=link))

    sent = bot.send_message(
        gid, "🔔 Чтобы писать в чат, нужно подписаться на канал 👇",
        reply_markup=kb, message_thread_id=getattr(message, "message_thread_id", None),
    )
    track_message(gid, sent.message_id)
    store.set_captcha_sub_prompt(gid, uid, {"msg_chat": sent.chat.id, "msg_id": sent.message_id})
    _schedule_message_autodelete(sent.chat.id, sent.message_id, 10.0)
    return True


def enforce_captcha(message):
    gid = message.chat.id
    uid = message.from_user.id if message.from_user else None
    if uid is None:
        return False

    captcha = store.get_captcha(gid)
    if not captcha.get("enabled", False):
        return False

    if captcha.get("type", "button") == "subscribe":
        return _enforce_subscribe_captcha(message)

    if not store.is_captcha_pending(gid, uid):
        return False
    try:
        bot.delete_message(gid, message.message_id)
    except Exception:
        pass
    _reprompt_button_captcha(message, uid)
    return True


def _captcha_callback(call, gid, target_uid):
    if call.from_user.id != int(target_uid):
        return bot.answer_callback_query(call.id, "Эта кнопка не для вас 🙅", show_alert=True)

    pending = store.get_captcha_pending(gid, call.from_user.id)
    try:
        bot.restrict_chat_member(
            gid, call.from_user.id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_media_messages=True,
                can_send_other_messages=True, can_add_web_page_previews=True,
            ),
        )
    except Exception as e:
        log.warning(f"[captcha verify] {e}")

    store.clear_captcha_pending(gid, call.from_user.id)
    bot.answer_callback_query(call.id, "Готово, добро пожаловать! ✅")

    if pending:
        try:
            bot.delete_message(pending["msg_chat"], pending["msg_id"])
        except Exception:
            pass


# =============================================================================
# Каналы для капчи «подписка» — какие показывать в списке выбора
# =============================================================================

def _channel_admin_ids(channel_id):
    try:
        admins = bot.get_chat_administrators(channel_id)
        return {a.user.id for a in admins if a.status in ("creator", "administrator")}
    except Exception as e:
        log.warning(f"[cap channel admins] {e}")
        return set()


def _eligible_channels(uid):
    """Каналы, где бот состоит, а запрашивающий пользователь — создатель/админ."""
    channels = store.get_known_channels()
    result = []
    for cid_str, title in channels.items():
        try:
            cid = int(cid_str)
        except (TypeError, ValueError):
            continue
        if uid in _channel_admin_ids(cid):
            result.append((cid, title))
    return result


# =============================================================================
# Системные сообщения / массовое удаление
# =============================================================================

_SYS_CONTENT_MAP = {
    "new_chat_members": "join",
    "left_chat_member": "leave",
    "new_chat_title": "title",
    "new_chat_photo": "photo",
    "delete_chat_photo": "delphoto",
    "pinned_message": "pin",
    "video_chat_started": "vc_start",
    "video_chat_ended": "vc_end",
    "video_chat_scheduled": "vc_scheduled",
}

def enforce_system_message_deletion(message):
    gid = message.chat.id
    key = _SYS_CONTENT_MAP.get(message.content_type, "other" if message.content_type not in
                                ("text", "photo", "video", "sticker", "document", "voice",
                                 "audio", "animation", "location", "contact", "poll") else None)
    if key is None:
        return False
    system = store.get_deletion(gid).get("system", {})
    if not system.get(key, False):
        return False
    try:
        bot.delete_message(gid, message.message_id)
    except Exception:
        pass
    return True


def cmd_mass_delete(gid):
    ids = _tracked_ids(gid)
    deleted = 0
    for mid in ids:
        try:
            bot.delete_message(gid, mid)
            deleted += 1
        except Exception:
            pass
    with _recent_lock:
        _recent_messages.pop(gid, None)
    return deleted


# =============================================================================
# Разбор URL-кнопок по синтаксису из ТЗ
# =============================================================================

_SPECIAL_PREFIXES = ("popup:", "alert:", "share:", "copy:", "rules")

# Домен должен состоять из меток через точку (например example.com) —
# просто непустой netloc (как "cujxjsbsdj") Telegram не принимает и роняет
# отправку с "Wrong HTTP URL".
_DOMAIN_RE = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


def _is_valid_button_url(value: str) -> bool:
    """Строгая проверка URL для инлайн-кнопки: http(s) + домен с точкой (TLD)."""
    if not value:
        return False
    parsed = urlparse(value.strip())
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname or ""
    return bool(_DOMAIN_RE.match(host))


def parse_url_buttons(raw_text):
    rows = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        row = []
        for chunk in line.split("&&"):
            chunk = chunk.strip()
            if " - " not in chunk:
                return None, f"Не поняла строку: «{chunk}». Нужен формат «Название - ссылка».'"
            label, value = chunk.split(" - ", 1)
            label, value = label.strip(), value.strip()
            if not label or not value:
                return None, f"Не поняла строку: «{chunk}»."
            btn = {"text": label}
            low = value.lower()
            if low == "rules" or low.startswith("rules"):
                btn["rules"] = True
            elif low.startswith("popup:"):
                btn["popup"] = value.split(":", 1)[1].strip()
            elif low.startswith("alert:"):
                btn["alert"] = value.split(":", 1)[1].strip()
            elif low.startswith("share:"):
                btn["share"] = value.split(":", 1)[1].strip()
            elif low.startswith("copy:"):
                btn["copy"] = value.split(":", 1)[1].strip()
            else:
                if not _is_valid_button_url(value):
                    return None, (
                        f"⚠️ Некорректная ссылка: «{value}». "
                        "Ссылка должна начинаться с http:// или https:// "
                        "и содержать настоящий домен, например https://example.com."
                    )
                btn["url"] = value
            row.append(btn)
        rows.append(row)
    if not rows:
        return None, "Не нашла ни одной кнопки."
    return rows, None


def build_markup_from_buttons(rows, gid=None, pid=None):
    if not rows:
        return None
    kb = types.InlineKeyboardMarkup()
    for row_index, row in enumerate(rows):
        line = []
        for btn_index, b in enumerate(row):
            text = b.get("text", "Кнопка")
            if b.get("url"):
                value = str(b.get("url") or "").strip()
                if not _is_valid_button_url(value):
                    log.warning("[settings buttons] skipped invalid URL: %r", value)
                    continue
                line.append(types.InlineKeyboardButton(text, url=value))
            elif b.get("share") is not None:
                share_url = f"https://t.me/share/url?text={b['share']}"
                line.append(types.InlineKeyboardButton(text, url=share_url))
            elif gid is not None and pid is not None:
                callback_data = f"cf|postbtn|{gid}|{pid}|{row_index}:{btn_index}"
                line.append(types.InlineKeyboardButton(text, callback_data=callback_data[:64]))
            else:
                line.append(types.InlineKeyboardButton(text, callback_data="cf|noop|0"))
        kb.row(*line)
    return kb


# Безопасный предел длины сериализованного состояния кнопок, которое зашивается
# прямо в ссылку конструктора (см. _miniapp_state_param). У Telegram Bot API нет
# официально задокументированного лимита именно на длину url= в инлайн-кнопке,
# поэтому берём разумный запас с большим накидом. Если превышен — просто не
# добавляем "state=" в ссылку, и конструктор прозрачно откатится на
# GET /api/context (запасной путь, который продолжает работать как раньше).
MINIAPP_STATE_MAX_LEN = 1500


def _miniapp_state_param(post):
    """Сериализовать текущие кнопки публикации в компактный JSON для query-параметра
    "state" ссылки конструктора — так Mini App получает актуальные кнопки сразу при
    открытии, без отдельного похода на бэкенд (и без риска рассинхронизации формата,
    которая раньше приводила к пустому экрану при повторном открытии).
    Возвращает готовую строку "&state=..." либо "", если зашивать не стоит
    (кнопок нет или сериализация вышла слишком длинной).
    """
    ui_rows = store.stored_rows_to_ui(post.get("buttons") or [])
    if not any(ui_rows):
        return ""
    for row in ui_rows:
        for btn in row:
            emoji_id = btn.get("custom_emoji_id")
            if emoji_id:
                btn["custom_emoji_preview"] = pe.preview_proxy_url(emoji_id)
    try:
        state_json = json.dumps(ui_rows, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        log.exception("[settings buttons] Не удалось сериализовать state для Mini App")
        return ""
    if len(state_json) > MINIAPP_STATE_MAX_LEN:
        log.warning(
            "[settings buttons] state для Mini App превысил безопасную длину (%d), "
            "пропускаю — конструктор откроется через /api/context", len(state_json),
        )
        return ""
    return "&state=" + quote(state_json, safe="")


# =============================================================================
# Публикации: превью и рассылка
# =============================================================================

class _SentMessage:
    """Минимальный объект, совместимый с кодом, которому нужен только message_id."""

    def __init__(self, message_id):
        self.message_id = message_id


_ZWSP = "\u200b"  # невидимый символ: Telegram не принимает пустой текст


def _post_body(post):
    """(текст или подпись, entities) публикации.

    entities is None — публикация сохранена до появления поддержки entities:
    её текст, как и раньше, разбирается как HTML.
    """
    media = post.get("media")
    if media and media.get("caption"):
        return media["caption"], media.get("caption_entities")
    return post.get("text"), post.get("text_entities")


_POST_MEDIA_TYPES = (
    "photo", "video", "animation", "document", "voice", "audio",
)


def _send_body(chat_id, media, body, entities, markup, thread_id=None):
    """Отправить текст или медиа с подписью. Возвращает объект с message_id.

    entities is not None: текст уходит 1 в 1 с сохранёнными entities
    (premium emoji, жирный и т.д.) напрямую в Bot API. Если Telegram отклонил
    именно форматирование, публикация не теряется: уходит простым текстом.
    entities is None: старый путь через parse_mode=HTML.
    """
    if entities is not None:
        try:
            markup_dict = rt.markup_to_dict(markup)
            if media:
                result = rt.send_media(chat_id, media["type"], media["file_id"], body,
                                       entities, markup_dict, thread_id)
            else:
                result = rt.send_text(chat_id, body or _ZWSP, entities, markup_dict, thread_id)
            return _SentMessage(result["message_id"])
        except rt.RichSendError as exc:
            if not rt.is_format_error(exc):
                raise
            log.warning("[posts] Telegram отклонил форматирование, отправляю простым текстом: %s", exc)
            # дальше — обычный путь; текст экранируем, т.к. у бота parse_mode=HTML
            body = html.escape(body, quote=False) if body else body

    if media:
        senders = {
            "photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation,
            "document": bot.send_document, "voice": bot.send_voice, "audio": bot.send_audio,
        }
        return senders[media["type"]](chat_id, media["file_id"], caption=body,
                                      reply_markup=markup, message_thread_id=thread_id)
    return bot.send_message(chat_id, body or _ZWSP, reply_markup=markup, message_thread_id=thread_id)


def _deliver_post(chat_id, post, thread_id=None, pid=None):
    raw_rows = post.get("buttons") or []
    media = post.get("media")
    body, entities = _post_body(post)

    # Если есть premium emoji в кнопках — используем raw HTTP (Bot API 9.4+,
    # icon_custom_emoji_id), текст при этом тоже уходит с сохранёнными entities.
    if pe.has_emoji_buttons(raw_rows):
        pe_rows = _rows_to_pe_format(raw_rows, gid=chat_id, pid=pid)
        text = body or _ZWSP
        text_entities = entities if body else None
        if media and media["type"] not in ("sticker", "voice"):
            resp = pe.send_media_with_emoji_buttons(
                chat_id, media["type"], media["file_id"], text,
                pe_rows, message_thread_id=thread_id, entities=text_entities,
            )
        elif not media:
            resp = pe.send_message_with_emoji_buttons(
                chat_id, text, pe_rows, message_thread_id=thread_id, entities=text_entities,
            )
        else:
            # sticker/voice не поддерживают emoji-кнопки — fallback
            resp = None

        if resp is not None and resp.get("ok"):
            msg_id = resp["result"]["message_id"]
            track_message(chat_id, msg_id)
            return _SentMessage(msg_id)
        # если raw-запрос не удался — падаем до обычного пути

    markup = build_markup_from_buttons(raw_rows, gid=chat_id, pid=pid)
    if media and media["type"] == "sticker":
        msg = bot.send_sticker(chat_id, media["file_id"], message_thread_id=thread_id)
    elif media and media["type"] not in _POST_MEDIA_TYPES:
        return None
    else:
        msg = _send_body(chat_id, media, body, entities, markup, thread_id)
    track_message(chat_id, msg.message_id)
    return msg


def _rows_to_pe_format(rows, gid=None, pid=None):
    """Конвертировать rows из settings_store в формат premium_emoji.send_*."""
    result = []
    for row_index, row in enumerate(rows):
        pe_row = []
        for btn_index, b in enumerate(row):
            text = b.get("text", "Кнопка")
            emoji_id = b.get("custom_emoji_id") or None
            btn = {"text": text, "custom_emoji_id": emoji_id}
            if b.get("url") and _is_valid_button_url(str(b.get("url") or "").strip()):
                btn["url"] = str(b.get("url")).strip()
            elif b.get("url"):
                # ссылка была сохранена раньше и невалидна (нет TLD и т.п.) —
                # не пытаемся отправить её в Telegram, превращаем кнопку в noop
                log.warning("[settings buttons] skipped invalid URL: %r", b.get("url"))
                if gid is not None and pid is not None:
                    cb = f"cf|postbtn|{gid}|{pid}|{row_index}:{btn_index}"
                    btn["callback_data"] = cb[:64]
                else:
                    btn["callback_data"] = "cf|noop|0"
            elif b.get("share") is not None:
                btn["url"] = f"https://t.me/share/url?text={b['share']}"
            elif gid is not None and pid is not None:
                cb = f"cf|postbtn|{gid}|{pid}|{row_index}:{btn_index}"
                btn["callback_data"] = cb[:64]
            else:
                btn["callback_data"] = "cf|noop|0"
            pe_row.append(btn)
        result.append(pe_row)
    return result


def _preview_post(chat_id, gid, pid):
    post = store.get_post(gid, pid)
    if not post or (not post.get("text") and not post.get("media")):
        bot.send_message(chat_id, "🤔 Сначала задайте текст или медиа публикации.")
        return
    delivered = _deliver_post(chat_id, post, thread_id=post.get("topic_id"), pid=pid)
    delivered_id = delivered.message_id if delivered else 0
    kb = ui._kb([[ui._btn("⬅️ Назад", "pprevback", gid, delivered_id)]])
    msg = bot.send_message(chat_id, "👆 Так выглядит публикация целиком.", reply_markup=kb)
    track_message(chat_id, msg.message_id)


# =============================================================================
# Планировщик повторяющихся публикаций
# =============================================================================

_WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _matches_filters(dt, post):
    weekdays = post.get("weekdays") or []
    monthdays = post.get("monthdays") or []
    if weekdays and _WEEKDAY_KEYS[dt.weekday()] not in weekdays:
        return False
    if monthdays and dt.day not in [int(d) for d in monthdays]:
        return False
    return True


def _find_next_valid(dt, post):
    for _ in range(400):
        if _matches_filters(dt, post):
            return dt
        dt += timedelta(days=1)
    return dt


def _compute_initial_next_run(post, now):
    if not post.get("time"):
        return None
    try:
        hh, mm = [int(x) for x in post["time"].split(":")]
    except Exception:
        return None
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    if post.get("start_date"):
        try:
            start = datetime.strptime(post["start_date"], "%d.%m.%Y").replace(tzinfo=TZ)
            if candidate < start:
                candidate = start.replace(hour=hh, minute=mm)
        except Exception:
            pass
    return _find_next_valid(candidate, post)


def _advance_next_run(post, last_run):
    interval = post.get("interval_seconds")
    if interval:
        candidate = last_run + timedelta(seconds=interval)
    else:
        candidate = last_run + timedelta(days=1)
    for _ in range(1000):
        if _matches_filters(candidate, post):
            return candidate
        candidate += timedelta(seconds=interval) if interval else timedelta(days=1)
    return candidate


def _is_past_end(post, now):
    if not post.get("end_date"):
        return False
    try:
        end = datetime.strptime(post["end_date"], "%d.%m.%Y").replace(
            hour=23, minute=59, second=59, tzinfo=TZ)
        return now > end
    except Exception:
        return False


def _scheduler_tick():
    now = datetime.now(TZ)
    from database import db_get
    data = db_get("group_settings", {})
    for gid_str, chat in list(data.items()):
        posts = chat.get("posts", {})
        for pid, post in list(posts.items()):
            if not post.get("enabled"):
                continue
            try:
                gid = int(gid_str)
            except ValueError:
                gid = gid_str

            if post.get("auto_off_at") and now.timestamp() >= post["auto_off_at"]:
                store.update_post(gid, pid, enabled=False)
                continue
            if _is_past_end(post, now):
                store.update_post(gid, pid, enabled=False)
                continue

            next_run = post.get("next_run")
            if next_run is None:
                nr = _compute_initial_next_run(post, now)
                if nr is None:
                    continue
                store.update_post(gid, pid, next_run=nr.timestamp())
                continue

            if now.timestamp() < next_run:
                continue

            try:
                if post.get("delete_last") and post.get("last_message_id") and post.get("last_chat_id"):
                    try:
                        bot.delete_message(post["last_chat_id"], post["last_message_id"])
                    except Exception:
                        pass

                msg = _deliver_post(gid, post, thread_id=post.get("topic_id"), pid=pid)
                if msg:
                    if post.get("pin"):
                        try:
                            bot.pin_chat_message(gid, msg.message_id, disable_notification=True)
                        except Exception:
                            pass
                    updates = {"last_message_id": msg.message_id, "last_chat_id": gid}
                    if post.get("delete_timer_seconds"):
                        threading.Timer(
                            post["delete_timer_seconds"],
                            _delayed_delete, args=(gid, msg.message_id),
                        ).start()
                    dt_now = datetime.now(TZ)
                    new_next = _advance_next_run(post, dt_now)
                    updates["next_run"] = new_next.timestamp()
                    store.update_post(gid, pid, **updates)
            except Exception as e:
                log.error(f"[scheduler publish {gid_str}/{pid}] {e}", exc_info=True)


def _delayed_delete(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def _scheduler_loop():
    while not stopped():
        try:
            _scheduler_tick()
        except Exception as e:
            log.error(f"[scheduler] {e}", exc_info=True)
        if stopped():
            break
        from reliability import _STOP
        _STOP.wait(20)


def start_scheduler():
    threading.Thread(target=_scheduler_loop, daemon=True, name="liza-posts-scheduler").start()


# =============================================================================
# Обработка ожидаемого пользовательского ввода (текст/медиа/кнопки/время/даты)
# =============================================================================

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_TOPIC_CMD_RE = re.compile(r"^/topic_rec(\d+)$")


def _finish_pending(chat_id, uid, gid, pid, note, target="popen"):
    store.clear_pending(chat_id, uid)
    msg = bot.send_message(chat_id, note)
    track_message(chat_id, msg.message_id)
    _send_post_screen_followup(chat_id, gid, pid, target)


def _send_post_screen_followup(chat_id, gid, pid, target="popen"):
    text, kb = _render(target, gid, pid)
    msg = bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    track_message(chat_id, msg.message_id)


def try_handle_pending_input(message):
    if not message.from_user:
        return False
    key_chat = message.chat.id
    uid = message.from_user.id

    m = _TOPIC_CMD_RE.match((message.text or "").strip())
    if m:
        pending = store.get_pending(message.chat.id, uid)
        if pending and pending.get("kind") == "topic" and pending.get("gid") == message.chat.id:
            thread_id = getattr(message, "message_thread_id", None)
            store.update_post(pending["gid"], pending["pid"], topic_id=thread_id)
            store.clear_pending(message.chat.id, uid)
            bot.reply_to(message, "✅ Тема для этой публикации сохранена.")
            return True

    pending = store.get_pending(key_chat, uid)
    if not pending:
        return False

    kind = pending["kind"]
    gid, pid = pending["gid"], pending.get("pid")

    if kind == "text":
        if not message.text:
            bot.reply_to(message, "Нужен текст сообщения. Или нажмите «Отмена».")
            return True
        store.update_post(gid, pid, text=message.text,
                          text_entities=rt.extract_entities(message))
        _finish_pending(key_chat, uid, gid, pid, "✅ Сообщение сохранено.", target="pmsg")
        return True

    if kind == "media":
        media = _extract_media(message)
        if not media:
            bot.reply_to(message, "Нужно фото, видео, гиф или стикер. Или нажмите «Отмена».")
            return True
        store.update_post(gid, pid, media=media)
        _finish_pending(key_chat, uid, gid, pid, "✅ Медиа сохранено.", target="pmsg")
        return True

    if kind == "buttons":
        if not message.text:
            bot.reply_to(message, "Нужен текст с описанием кнопок. Или нажмите «Отмена».")
            return True
        rows, err = parse_url_buttons(message.text)
        if err:
            bot.reply_to(message, f"⚠️ {err}")
            return True
        store.update_post(gid, pid, buttons=rows)
        _finish_pending(key_chat, uid, gid, pid, "✅ Кнопки сохранены.", target="pmsg")
        return True

    if kind == "time":
        text = (message.text or "").strip()
        if not _TIME_RE.match(text):
            bot.reply_to(message, "⚠️ Формат времени — ЧЧ:ММ, например 14:30.")
            return True
        store.update_post(gid, pid, time=text, next_run=None)
        _finish_pending(key_chat, uid, gid, pid, f"✅ Время публикации: {text}.")
        return True

    if kind == "interval":
        seconds, ok = parse_duration((message.text or "").strip())
        if not ok or seconds <= 0:
            bot.reply_to(message, "⚠️ Не поняла интервал. Примеры: <code>30м</code>, <code>2ч</code>, <code>1д</code>.")
            return True
        store.update_post(gid, pid, interval_seconds=seconds, next_run=None)
        _finish_pending(key_chat, uid, gid, pid, f"✅ Повторение: каждые {format_seconds(seconds)}.")
        return True

    if kind == "autooff":
        seconds, ok = parse_duration((message.text or "").strip())
        if not ok or seconds <= 0:
            bot.reply_to(message, "⚠️ Не поняла срок. Примеры: <code>2ч</code>, <code>1д</code>.")
            return True
        store.update_post(gid, pid, auto_off_seconds=seconds, auto_off_at=time.time() + seconds)
        _finish_pending(key_chat, uid, gid, pid,
                         f"✅ Публикация автоматически отключится через {format_seconds(seconds)}.")
        return True

    if kind == "deltimer":
        seconds, ok = parse_duration((message.text or "").strip())
        if not ok or seconds <= 0:
            bot.reply_to(message, "⚠️ Не поняла срок. Примеры: <code>10м</code>, <code>1ч</code>.")
            return True
        store.update_post(gid, pid, delete_timer_seconds=seconds)
        _finish_pending(key_chat, uid, gid, pid,
                         f"✅ Публикация будет удаляться через {format_seconds(seconds)} после отправки.")
        return True

    if kind in ("startdate", "enddate"):
        text = (message.text or "").strip()
        if not _DATE_RE.match(text):
            bot.reply_to(message, "⚠️ Формат даты — ДД.ММ.ГГГГ, например 25.12.2026.")
            return True
        try:
            datetime.strptime(text, "%d.%m.%Y")
        except ValueError:
            bot.reply_to(message, "⚠️ Такой даты не существует.")
            return True
        field = "start_date" if kind == "startdate" else "end_date"
        store.update_post(gid, pid, **{field: text, "next_run": None})
        label = "начала" if kind == "startdate" else "окончания"
        _finish_pending(key_chat, uid, gid, pid, f"✅ Дата {label}: {text}.")
        return True

    return False


def _extract_media(message):
    caption = message.caption
    # entities подписи сохраняем как есть — иначе premium emoji/жирный в подписи пропадут
    caption_entities = rt.extract_entities(message, caption=True) if caption else []
    if message.photo:
        return {"type": "photo", "file_id": message.photo[-1].file_id,
                "caption": caption, "caption_entities": caption_entities}
    if message.video:
        return {"type": "video", "file_id": message.video.file_id,
                "caption": caption, "caption_entities": caption_entities}
    if message.animation:
        return {"type": "animation", "file_id": message.animation.file_id,
                "caption": caption, "caption_entities": caption_entities}
    if message.document:
        return {"type": "document", "file_id": message.document.file_id,
                "caption": caption, "caption_entities": caption_entities}
    if message.voice:
        return {"type": "voice", "file_id": message.voice.file_id, "caption": None}
    if message.audio:
        return {"type": "audio", "file_id": message.audio.file_id,
                "caption": caption, "caption_entities": caption_entities}
    if message.sticker:
        return {"type": "sticker", "file_id": message.sticker.file_id, "caption": None}
    return None


# =============================================================================
# Регистрация обработчиков на боте
# =============================================================================

@bot.message_handler(content_types=["new_chat_members"])
def _on_new_members(message):
    try:
        handle_new_members(message)
    except Exception as e:
        log.error(f"[new_members] {e}", exc_info=True)
    try:
        enforce_system_message_deletion(message)
    except Exception as e:
        log.error(f"[new_members delete] {e}", exc_info=True)


if hasattr(bot, "my_chat_member_handler"):
    @bot.my_chat_member_handler()
    def _on_my_chat_member(update):
        try:
            gid = update.chat.id
            status = update.new_chat_member.status
            if update.chat.type == "channel":
                if status in ("left", "kicked"):
                    store.remove_known_channel(gid)
                else:
                    store.register_known_channel(gid, update.chat.title)
                return
            if status in ("left", "kicked"):
                store.remove_known_group(gid)
            else:
                store.register_known_group(gid, update.chat.title)
        except Exception as e:
            log.warning(f"[my_chat_member] {e}")
else:
    log.info("Установленная версия telebot не поддерживает my_chat_member_handler — "
             "список групп в реестре не будет очищаться при удалении бота из чата.")


@bot.message_handler(content_types=[
    "photo", "video", "animation", "document", "voice", "audio", "sticker", "video_note",
])
def _on_media_message(message):
    try:
        if message.chat.type in ("group", "supergroup"):
            if enforce_captcha(message):
                return
            track_message(message.chat.id, message.message_id)
        if try_handle_pending_input(message):
            return

        # Обработка голосовых сообщений и видеокружков через Groq Whisper
        if message.content_type in ("voice", "video_note"):
            from transcriber import handle_transcription
            handle_transcription(message)
            return

    except Exception as e:
        log.error(f"[media_message] {e}", exc_info=True)


@bot.message_handler(content_types=[
    "left_chat_member", "new_chat_title", "new_chat_photo", "delete_chat_photo",
    "pinned_message", "video_chat_started", "video_chat_ended", "video_chat_scheduled",
    "video_chat_participants_invited", "group_chat_created", "supergroup_chat_created",
    "channel_chat_created", "migrate_to_chat_id", "migrate_from_chat_id",
    "message_auto_delete_timer_changed", "forum_topic_created", "forum_topic_closed",
    "forum_topic_reopened", "forum_topic_edited", "general_forum_topic_hidden",
    "general_forum_topic_unhidden", "write_access_allowed", "user_shared", "chat_shared",
    "proximity_alert_triggered", "web_app_data",
])
def _on_service_message(message):
    try:
        enforce_system_message_deletion(message)
    except Exception as e:
        log.error(f"[service_message] {e}", exc_info=True)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("cf|"))
def _on_settings_callback(call):
    try:
        _dispatch_callback(call)
    except Exception as e:
        log.error(f"[settings callback] {e}", exc_info=True)
        try:
            bot.answer_callback_query(call.id, "⚠️ Что-то пошло не так.")
        except Exception:
            pass


def _dispatch_callback(call):
    parts = call.data.split("|")
    action = parts[1]
    gid = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else (parts[2] if len(parts) > 2 else None)
    rest = parts[3:]

    if action == "noop":
        return bot.answer_callback_query(call.id)

    if action == "askwhere":
        if not _authorized(gid, call.from_user.id):
            return bot.answer_callback_query(call.id, "⛔ Только для админов чата.", show_alert=True)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                "Где вы хотите открыть меню настроек чата?",
                chat_id=call.message.chat.id, message_id=call.message.message_id,
                reply_markup=ui.where_open_kb(gid),
            )
        except ApiTelegramException:
            pass
        return

    if action == "opnhere":
        if not _authorized(gid, call.from_user.id):
            return bot.answer_callback_query(call.id, "⛔ Только для админов чата.", show_alert=True)
        bot.answer_callback_query(call.id)
        return _show(call.message.chat.id, call.message.message_id, "root", gid)

    if action == "opnpm":
        if not _authorized(gid, call.from_user.id):
            return bot.answer_callback_query(call.id, "⛔ Только для админов чата.", show_alert=True)
        store.set_active_group(call.from_user.id, gid)
        try:
            text, kb = _render("root", gid)
            msg = bot.send_message(call.from_user.id, text, reply_markup=kb, parse_mode="HTML")
            track_message(msg.chat.id, msg.message_id)
            bot.answer_callback_query(call.id, "✅ Отправила настройки вам в личку.")
            try:
                bot.edit_message_reply_markup(chat_id=call.message.chat.id,
                                               message_id=call.message.message_id, reply_markup=None)
            except Exception:
                pass
        except Exception as e:
            log.info(f"[opnpm] couldn't DM user, falling back to deep link: {e}")
            bot.answer_callback_query(call.id)
            try:
                bot.edit_message_text(
                    "Сначала напишите мне в личные сообщения /start, а затем нажмите кнопку ещё раз.",
                    chat_id=call.message.chat.id, message_id=call.message.message_id,
                    reply_markup=ui.deeplink_kb(BOT_USERNAME, gid),
                )
            except Exception:
                pass
        return

    if action == "capver":
        return _captcha_callback(call, gid, rest[0])

    if not _authorized(gid, call.from_user.id):
        return bot.answer_callback_query(call.id, "⛔ Только для админов чата.", show_alert=True)

    chat_id, message_id = call.message.chat.id, call.message.message_id

    store.clear_pending(chat_id, call.from_user.id)
    if gid != chat_id:
        store.clear_pending(gid, call.from_user.id)

    if action == "postbtn":
        pid = rest[0] if rest else ""
        pos = rest[1] if len(rest) > 1 else ""
        try:
            row_index, btn_index = [int(x) for x in pos.split(":", 1)]
        except Exception:
            return bot.answer_callback_query(call.id, "⚠️ Некорректная кнопка.", show_alert=True)

        post = store.get_post(gid, pid)
        if not post:
            return bot.answer_callback_query(call.id, "⚠️ Публикация не найдена.", show_alert=True)

        rows = post.get("buttons") or []
        try:
            button = rows[row_index][btn_index]
        except (IndexError, TypeError):
            return bot.answer_callback_query(call.id, "⚠️ Кнопка не найдена.", show_alert=True)

        if button.get("popup") is not None:
            return bot.answer_callback_query(call.id, str(button.get("popup") or ""), show_alert=False)
        if button.get("alert") is not None:
            return bot.answer_callback_query(call.id, str(button.get("alert") or ""), show_alert=True)
        if button.get("rules") is not None:
            text = button.get("rules")
            if not isinstance(text, str) or not text.strip():
                text = "Правила группы не заданы."
            return bot.answer_callback_query(call.id, text[:195], show_alert=True)
        if button.get("copy") is not None:
            text = str(button.get("copy") or "")
            return bot.answer_callback_query(
                call.id, f"📋 Текст для копирования:\n{text}"[:195], show_alert=True
            )
        if button.get("delete_message"):
            try:
                bot.delete_message(chat_id, message_id)
            except Exception:
                pass
            return bot.answer_callback_query(call.id, "🗑 Сообщение удалено.")
        if button.get("user_command") is not None:
            command = str(button.get("user_command") or "")
            try:
                bot.send_message(
                    call.from_user.id,
                    f"Команда для выполнения: <code>{command}</code>\n"
                    "Автоматически выполнить её от имени пользователя бот не может.",
                    parse_mode="HTML",
                )
                return bot.answer_callback_query(call.id, "📩 Команда отправлена вам в личку.")
            except Exception:
                return bot.answer_callback_query(
                    call.id, "📩 Откройте личные сообщения с Лизой для команды.", show_alert=True
                )

        return bot.answer_callback_query(call.id)

    if action == "settings_liza":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "settings_liza", gid)

    if action == "settings_chat":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "settings_chat", gid)

    if action == "liza":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "liza", gid)

    if action == "chance":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "chance", gid)

    if action == "chance_set":
        try: value = max(0, min(35, int(rest[0])))
        except Exception: return bot.answer_callback_query(call.id, "⚠️ Некорректное значение.", show_alert=True)
        store.set_liza_value(gid, "chatter_chance", value / 100.0)
        try:
            from utils import set_setting
            set_setting(gid, "chatter_chance", value / 100.0)
        except Exception: pass
        bot.answer_callback_query(call.id, f"✅ Активность: {value}%")
        return _show(chat_id, message_id, "chance", gid)

    if action == "sleep":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "sleep", gid)

    if action == "sleep_set":
        try: seconds = max(0, int(rest[0]))
        except Exception: seconds = 0
        try:
            from utils import set_setting
            import time as _time
            set_setting(gid, "sleep_until", _time.time() + seconds if seconds else 0)
        except Exception: pass
        bot.answer_callback_query(call.id, "☀️ Лиза проснулась." if not seconds else "😴 Режим сна включён.")
        return _show(chat_id, message_id, "sleep", gid)

    if action == "liza_toggle":
        key = rest[0] if rest else ""
        if key not in {"autoactivity", "stories", "memory", "minigames", "polite", "angry"}:
            return bot.answer_callback_query(call.id, "⚠️ Неизвестная настройка.", show_alert=True)
        l = store.get_liza(gid)
        new = not bool(l.get(key, False))
        store.set_liza_value(gid, key, new)
        try:
            from utils import set_setting
            if key == "autoactivity": set_setting(gid, "autoactivity", new)
            elif key == "stories": set_setting(gid, "stories_enabled", new)
            elif key == "polite": set_setting(gid, "polite_filter", new)
            elif key == "angry": set_setting(gid, "angry_mode", new)
            elif key == "memory": set_setting(gid, "memory_enabled", new)
        except Exception: pass
        bot.answer_callback_query(call.id, "✅ Настройка обновлена.")
        target = "liza" if key == "autoactivity" else ("fun" if key in {"stories","minigames"} else "chat")
        return _show(chat_id, message_id, target, gid)

    if action == "reply":
        mode = rest[0] if rest else "everyone"
        if mode not in {"everyone", "mention", "silent"}:
            return bot.answer_callback_query(call.id, "⚠️ Неизвестный режим.", show_alert=True)
        store.set_liza_value(gid, "reply_mode", mode)
        bot.answer_callback_query(call.id, "✅ Режим ответов изменён.")
        return _show(chat_id, message_id, "liza", gid)

    if action == "mem":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "mem", gid)

    if action == "mem_toggle":
        from utils import get_setting, set_setting
        enabled = not bool(get_setting(gid, "memory_enabled", True))
        set_setting(gid, "memory_enabled", enabled); store.set_liza_value(gid, "memory", enabled)
        bot.answer_callback_query(call.id, "🧠 Память " + ("включена." if enabled else "выключена."))
        return _show(chat_id, message_id, "mem", gid)

    if action == "mem_clear_confirm":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "mem_clear_confirm", gid)

    if action == "mem_clear":
        from database import db_delete_scoped_prefix
        db_delete_scoped_prefix("user_memory", str(gid) + ":")
        bot.answer_callback_query(call.id, "🧹 Память этого чата очищена.")
        return _show(chat_id, message_id, "mem", gid)

    if action == "fun":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "fun", gid)

    if action == "chat":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "chat", gid)

    if action == "mod":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "mod", gid)

    if action == "modtoggle":
        key = rest[0] if rest else ""
        if key in {"auto_delete", "protect_admins"}:
            from moderation import _store
            bucket = _store().get(str(gid), {})
            current = bool(bucket.get("config", {}).get(key, False))
            from moderation import update_chat_config
            update_chat_config(gid, **{key: not current})
        else:
            return bot.answer_callback_query(call.id, "⚠️ Неизвестная настройка.", show_alert=True)
        bot.answer_callback_query(call.id, "✅ Модерация обновлена.")
        return _show(chat_id, message_id, "mod", gid)

    if action == "warnlimit":
        from moderation import _store, update_chat_config
        bucket = _store().get(str(gid), {})
        current=int(bucket.get("config", {}).get("warn_limit",3))
        values=[1,2,3,5,10,20]
        try: idx=values.index(current)
        except ValueError: idx=2
        new=values[(idx+1)%len(values)]
        update_chat_config(gid, warn_limit=new)
        bot.answer_callback_query(call.id, f"✅ Лимит варнов: {new}")
        return _show(chat_id, message_id, "mod", gid)

    if action == "warnaction":
        action_name = rest[0] if rest else "mute"
        if action_name not in {"mute","ban","kick"}:
            return bot.answer_callback_query(call.id, "⚠️ Некорректное действие.", show_alert=True)
        from moderation import update_chat_config
        update_chat_config(gid, warn_action=action_name)
        bot.answer_callback_query(call.id, "✅ Автодействие изменено.")
        return _show(chat_id, message_id, "mod", gid)

    if action == "modlegacy":
        try:
            from moderation import cmd_moderation_settings
            cmd_moderation_settings(call.message)
        except Exception: pass
        return bot.answer_callback_query(call.id)

    if action == "pers":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "pers", gid)

    if action == "pers_adj":
        key = rest[0] if rest else ""
        try: delta = int(rest[1])
        except Exception: delta = 0
        from chat_personality import get, set_value
        current = get(gid).get(key)
        if current is None:
            return bot.answer_callback_query(call.id, "⚠️ Неизвестный параметр.", show_alert=True)
        set_value(gid, key, max(0, min(100, current + delta)))
        bot.answer_callback_query(call.id, f"✅ {max(0, min(100, current + delta))}/100")
        return _show(chat_id, message_id, "pers", gid)

    if action == "perscmd":
        try:
            from handlers import _cmd_personality
            _cmd_personality(call.message, "")
        except Exception: pass
        return bot.answer_callback_query(call.id)

    if action == "status":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "status", gid)

    if action == "reset":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "reset", gid)

    if action == "reset_confirm":
        bot.answer_callback_query(call.id); return _show(chat_id, message_id, "reset_confirm", gid)

    if action == "reset_do":
        store.update_liza(gid, reply_mode="everyone", autoactivity=False, chatter_chance=0.05, stories=True, memory=True, minigames=True, polite=False, angry=False)
        try:
            from utils import set_setting
            defaults={"autoactivity":False,"chatter_chance":0.05,"stories_enabled":True,"memory_enabled":True,"polite_filter":False,"angry_mode":False,"sleep_until":0}
            for k,v in defaults.items(): set_setting(gid,k,v)
        except Exception: pass
        bot.answer_callback_query(call.id, "✅ Настройки Лизы сброшены.")
        return _show(chat_id, message_id, "root", gid)

    if action == "back":
        target = rest[0]
        pid = rest[1] if len(rest) > 1 else None
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, target, gid, pid)

    if action == "close":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
        return

    if action == "pprevback":
        del_id = rest[0] if rest else None
        bot.answer_callback_query(call.id)
        try:
            if del_id and str(del_id) not in ("0", "None"):
                bot.delete_message(chat_id, int(del_id))
        except Exception:
            pass
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
        return

    if action == "security":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "security", gid)

    if action == "cap":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "cap", gid)

    if action == "capon":
        store.set_captcha_enabled(gid, True)
        bot.answer_callback_query(call.id, "✅ Капча включена.")
        return _show(chat_id, message_id, "cap", gid)

    if action == "capoff":
        store.set_captcha_enabled(gid, False)
        bot.answer_callback_query(call.id, "❌ Капча выключена.")
        return _show(chat_id, message_id, "cap", gid)

    if action == "cap_type":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "cap_type", gid)

    if action == "cap_type_set":
        ctype = rest[0] if rest else "button"
        if ctype != "button":
            return bot.answer_callback_query(call.id, "⚠️ Некорректный тип.", show_alert=True)
        store.set_captcha_type(gid, "button")
        bot.answer_callback_query(call.id, "✅ Тип капчи обновлён.")
        return _show(chat_id, message_id, "cap_type", gid)

    if action == "cap_channels":
        bot.answer_callback_query(call.id)
        channels = _eligible_channels(call.from_user.id)
        text = ui.channels_text(bool(channels))
        kb = ui.channels_kb(gid, channels)
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                   reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            if "message is not modified" not in str(e):
                log.warning(f"[cap_channels] {e}")
        return

    if action == "cap_channel_set":
        try:
            channel_id = int(rest[0])
        except Exception:
            return bot.answer_callback_query(call.id, "⚠️ Некорректный канал.", show_alert=True)
        if call.from_user.id not in _channel_admin_ids(channel_id):
            return bot.answer_callback_query(call.id, "⛔ Вы не администратор этого канала.", show_alert=True)
        title = store.get_known_channels().get(str(channel_id), str(channel_id))
        store.set_captcha_type(gid, "subscribe", channel_id=channel_id, channel_title=title)
        bot.answer_callback_query(call.id, "✅ Канал выбран.")
        return _show(chat_id, message_id, "cap_type", gid)

    if action == "selectgroup":
        bot.answer_callback_query(call.id)
        store.set_active_group(call.from_user.id, gid)
        return _show(chat_id, message_id, "root", gid)

    if action == "pst":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "pst", gid)

    if action == "paddp":
        pid = store.add_post(gid)
        if pid is None:
            return bot.answer_callback_query(call.id, "⚠️ Достигнут лимит публикаций.", show_alert=True)
        bot.answer_callback_query(call.id, "➕ Публикация добавлена.")
        return _show(chat_id, message_id, "pst", gid)

    if action == "popen":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "popen", gid, rest[0])

    if action == "ptoggle":
        pid = rest[0]
        post = store.get_post(gid, pid)
        if post and not post.get("enabled") and not post.get("time"):
            bot.answer_callback_query(call.id, "⚠️ Сначала задайте время публикации.", show_alert=True)
            return _show(chat_id, message_id, "popen", gid, pid)
        store.toggle_post_field(gid, pid, "enabled")
        if store.get_post(gid, pid).get("enabled"):
            store.update_post(gid, pid, next_run=None)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "pst", gid)

    if action == "pdel":
        pid = rest[0]
        store.delete_post(gid, pid)
        bot.answer_callback_query(call.id, "🗑️ Публикация удалена.")
        return _show(chat_id, message_id, "pst", gid)

    if action == "pmsg":
        pid = rest[0]
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "pmsg", gid, pid)

    if action == "ptxt":
        pid = rest[0]
        post = store.get_post(gid, pid)
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            chat_id, "👉🏻 Отправьте сейчас сообщение, которое вы хотите установить.",
            reply_markup=ui.text_prompt_kb(gid, pid, bool(post.get("text"))),
        )
        track_message(chat_id, msg.message_id)
        store.set_pending(chat_id, call.from_user.id, {"kind": "text", "gid": gid, "pid": pid})
        return

    if action == "pmedia":
        pid = rest[0]
        post = store.get_post(gid, pid)
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            chat_id,
            "👉🏻 Отправьте сейчас медиа (фотографии, видео, наклейки ...), который вы хотите установить.\n"
            "Вы также можете ввести подпись.",
            reply_markup=ui.media_prompt_kb(gid, pid, bool(post.get("media"))),
        )
        track_message(chat_id, msg.message_id)
        store.set_pending(chat_id, call.from_user.id, {"kind": "media", "gid": gid, "pid": pid})
        return

    if action == "pbtn":
        pid = rest[0]
        post = store.get_post(gid, pid)
        bot.answer_callback_query(call.id)

        has_value = bool(post.get("buttons"))
        if MINIAPP_PUBLIC_URL:
            # Прямая ссылка на свой домен: chat_id/post_id идут обычными
            # query-параметрами и не зависят от передачи startapp Телеграмом —
            # тот механизм кэшируется и не всегда долетает (особенно на iOS),
            # из-за чего конструктор открывался пустым при повторном заходе.
            # "v=" — просто чтобы Telegram не показал закэшированную старую
            # страницу при открытии той же публикации второй раз подряд.
            # "state=" — текущие кнопки зашиты прямо в ссылку (см. GroupHelpBot),
            # чтобы конструктор рисовал их сразу, без похода на /api/context.
            miniapp_url = (
                f"{MINIAPP_PUBLIC_URL}/?chat_id={gid}&post_id={pid}&v={int(time.time())}"
                f"{_miniapp_state_param(post)}"
            )
        else:
            start_param = f"c{gid}_p{pid}"
            miniapp_url = f"https://t.me/{BOT_USERNAME}/app?startapp={start_param}"

        hint = (
            "👉🏻 Здесь можно настроить кнопки для этой публикации.\n\n"
            "Нажмите «⚡Простое создание кнопок», чтобы открыть конструктор.\n"
            "После сохранения кнопки автоматически будут использоваться при каждой "
            "повторяющейся публикации."
        )
        msg = bot.send_message(
            chat_id,
            hint,
            parse_mode="HTML",
            reply_markup=ui.buttons_prompt_kb(gid, pid, has_value, miniapp_url=miniapp_url),
        )
        track_message(chat_id, msg.message_id)
        return

    if action == "ptxtdel":
        pid = rest[0]
        store.update_post(gid, pid, text=None, text_entities=None)
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id, "🚫 Сообщение удалено.")
        return _show(chat_id, message_id, "pmsg", gid, pid)

    if action == "ptxtcancel":
        pid = rest[0]
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "pmsg", gid, pid)

    if action == "pmediadel":
        pid = rest[0]
        store.update_post(gid, pid, media=None)
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id, "🚫 Медиа удалено.")
        return _show(chat_id, message_id, "pmsg", gid, pid)

    if action == "pmediacancel":
        pid = rest[0]
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "pmsg", gid, pid)

    if action == "pbtndel":
        pid = rest[0]
        store.update_post(gid, pid, buttons=None)
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id, "🚫 Кнопки удалены.")
        return _show(chat_id, message_id, "pmsg", gid, pid)

    if action == "pbtncancel":
        pid = rest[0]
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "pmsg", gid, pid)

    if action == "ptxtprev":
        pid = rest[0]
        post = store.get_post(gid, pid)
        if not post.get("text"):
            return bot.answer_callback_query(call.id, "🤔 Текст ещё не установлен.", show_alert=True)
        bot.answer_callback_query(call.id, "👀 Отправляю текст.")
        kb = ui._kb([[ui._btn("⬅️ Назад", "close", gid)]])
        msg = _send_body(chat_id, None, post["text"], post.get("text_entities"), kb)
        track_message(chat_id, msg.message_id)
        return

    if action == "pmediaprev":
        pid = rest[0]
        post = store.get_post(gid, pid)
        media = post.get("media")
        if not media:
            return bot.answer_callback_query(call.id, "🤔 Медиа ещё не установлено.", show_alert=True)
        bot.answer_callback_query(call.id, "👀 Отправляю медиа.")
        kb = ui._kb([[ui._btn("⬅️ Назад", "close", gid)]])
        sender = {
            "photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation,
            "document": bot.send_document, "voice": bot.send_voice, "audio": bot.send_audio,
            "sticker": bot.send_sticker,
        }.get(media["type"])
        if sender is None:
            return
        if media["type"] in ("sticker", "voice"):
            msg = sender(chat_id, media["file_id"], reply_markup=kb)
        else:
            msg = _send_body(chat_id, media, media.get("caption"),
                             media.get("caption_entities"), kb)
        track_message(chat_id, msg.message_id)
        return

    if action == "pbtnprev":
        pid = rest[0]
        post = store.get_post(gid, pid)
        raw_rows = post.get("buttons") or []
        if not raw_rows:
            return bot.answer_callback_query(call.id, "🤔 URL-кнопки ещё не установлены.", show_alert=True)
        bot.answer_callback_query(call.id, "👀 Отправляю кнопки.")
        caption = "🔠 Так выглядят установленные URL-кнопки:"
        if pe.has_emoji_buttons(raw_rows):
            pe_rows = _rows_to_pe_format(raw_rows, gid=gid, pid=pid)
            pe_rows.append([{"text": "⬅️ Назад", "callback_data": f"cf|close|{gid}"}])
            resp = pe.send_message_with_emoji_buttons(chat_id, caption, pe_rows)
            if resp is not None and resp.get("ok"):
                track_message(chat_id, resp["result"]["message_id"])
                return
            # если raw-запрос не удался — падаем до обычного пути ниже
        markup = build_markup_from_buttons(raw_rows, gid=gid, pid=pid) or types.InlineKeyboardMarkup()
        markup.row(ui._btn("⬅️ Назад", "close", gid))
        msg = bot.send_message(chat_id, caption, reply_markup=markup)
        track_message(chat_id, msg.message_id)
        return

    if action == "pprev":
        pid = rest[0]
        bot.answer_callback_query(call.id, "👀 Отправляю превью.")
        return _preview_post(chat_id, gid, pid)

    if action == "ptopic":
        pid = rest[0]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            chat_id,
            "🗂 <b>Выбрать тему</b>\n"
            "Если вы используете «Темы» в своей группе, вы должны решить, в какой теме "
            "бот должен отправлять сообщения этого типа.\n\n"
            "Для этого перейдите в выбранную тему в группе и отправьте эту команду:\n"
            f"<code>/topic_rec{pid}</code>\n\n"
            "Если вы не используете «Темы», игнорируйте этот параметр.",
            parse_mode="HTML",
        )
        track_message(chat_id, msg.message_id)
        store.set_pending(gid, call.from_user.id, {"kind": "topic", "gid": gid, "pid": pid})
        return

    if action == "ptime":
        pid = rest[0]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, "👉🏻 Отправьте время публикации в формате ЧЧ:ММ, например 14:30.",
                                reply_markup=ui.back_kb(gid, "popen", pid))
        track_message(chat_id, msg.message_id)
        store.set_pending(chat_id, call.from_user.id, {"kind": "time", "gid": gid, "pid": pid})
        return

    if action == "prep":
        pid = rest[0]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, "👉🏻 Отправьте интервал повторения, например 30м, 2ч или 1д.",
                                reply_markup=ui.back_kb(gid, "popen", pid))
        track_message(chat_id, msg.message_id)
        store.set_pending(chat_id, call.from_user.id, {"kind": "interval", "gid": gid, "pid": pid})
        return

    if action == "pwd":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "pwd", gid, rest[0])

    if action == "pwdt":
        pid, day = rest[0], rest[1]
        store.toggle_post_day(gid, pid, "weekdays", day)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "pwd", gid, pid)

    if action == "pmd":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "pmd", gid, rest[0])

    if action == "pmdt":
        pid, day = rest[0], rest[1]
        store.toggle_post_day(gid, pid, "monthdays", day)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "pmd", gid, pid)

    if action == "pauto":
        pid = rest[0]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            chat_id,
            "👉🏻 Через сколько времени публикация должна сама выключиться? Например 2ч или 1д.",
            reply_markup=ui.back_kb(gid, "popen", pid),
        )
        track_message(chat_id, msg.message_id)
        store.set_pending(chat_id, call.from_user.id, {"kind": "autooff", "gid": gid, "pid": pid})
        return

    if action == "psdate":
        pid = rest[0]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, "👉🏻 Отправьте дату начала в формате ДД.ММ.ГГГГ.",
                                reply_markup=ui.back_kb(gid, "popen", pid))
        track_message(chat_id, msg.message_id)
        store.set_pending(chat_id, call.from_user.id, {"kind": "startdate", "gid": gid, "pid": pid})
        return

    if action == "pedate":
        pid = rest[0]
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, "👉🏻 Отправьте дату окончания в формате ДД.ММ.ГГГГ.",
                                reply_markup=ui.back_kb(gid, "popen", pid))
        track_message(chat_id, msg.message_id)
        store.set_pending(chat_id, call.from_user.id, {"kind": "enddate", "gid": gid, "pid": pid})
        return

    if action == "ppin":
        pid = rest[0]
        store.toggle_post_field(gid, pid, "pin")
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "popen", gid, pid)

    if action == "pdellast":
        pid = rest[0]
        store.toggle_post_field(gid, pid, "delete_last")
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "popen", gid, pid)

    if action == "pdeltimer":
        pid = rest[0]
        post = store.get_post(gid, pid)
        if post.get("delete_timer_seconds"):
            store.update_post(gid, pid, delete_timer_seconds=None)
            bot.answer_callback_query(call.id, "❌ Удаление по таймеру выключено.")
            return _show(chat_id, message_id, "popen", gid, pid)
        bot.answer_callback_query(call.id)
        msg = bot.send_message(chat_id, "👉🏻 Через сколько после публикации удалять сообщение? Например 10м.",
                                reply_markup=ui.back_kb(gid, "popen", pid))
        track_message(chat_id, msg.message_id)
        store.set_pending(chat_id, call.from_user.id, {"kind": "deltimer", "gid": gid, "pid": pid})
        return

    if action == "del":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "del", gid)

    if action == "delsys":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "delsys", gid)

    if action == "delsystg":
        key = rest[0]
        store.toggle_system_message(gid, key)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "delsys", gid)

    if action == "delmass":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "delmass", gid)

    if action == "delmasscf":
        bot.answer_callback_query(call.id, "🤯 Удаляю...")
        deleted = cmd_mass_delete(gid)
        try:
            bot.edit_message_text(f"✅ Удалено сообщений: {deleted}.", chat_id=chat_id, message_id=message_id,
                                   reply_markup=ui.back_kb(gid, "del"))
        except Exception:
            pass
        return

    bot.answer_callback_query(call.id)

# updated 2026-09-18
