# -*- coding: utf-8 -*-
"""Меню настроек Лизы: капча, повторяющиеся публикации, удаление сообщений."""
import re
import time
import logging
import threading
from collections import deque
from datetime import datetime, timedelta

from telebot import types
from telebot.apihelper import ApiTelegramException
from telebot.types import ChatPermissions

from runtime import bot, BOT_ID, BOT_USERNAME
from config import TZ
from utils import is_chat_admin, parse_duration, format_seconds, get_mention

import settings_store as store
import settings_ui as ui

log = logging.getLogger("settings")

# Кольцевой буфер последних message_id по каждому чату — для массового удаления.
# Только в памяти: переживает работу процесса, но не перезапуск (это ок для этой функции).
_MAX_TRACKED = 2000
_recent_messages = {}
_recent_lock = threading.RLock()


def track_message(chat_id, message_id):
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
        return chat.title or str(gid)
    except Exception:
        return str(gid)


def _render(target, gid, pid=None):
    if target == "root":
        return ui.root_text(_chat_title(gid)), ui.root_kb(gid)
    if target == "cap":
        return ui.captcha_text(gid), ui.captcha_kb(gid)
    if target == "pst":
        return ui.posts_list_text(gid), ui.posts_list_kb(gid)
    if target == "popen":
        return ui.post_edit_text(gid, pid), ui.post_edit_kb(gid, pid)
    if target == "pwd":
        return ui.post_edit_text(gid, pid) + "\n\n🗓️ Выберите дни недели:", ui.weekdays_kb(gid, pid)
    if target == "pmd":
        return ui.post_edit_text(gid, pid) + "\n\n📆 Выберите дни месяца:", ui.monthdays_kb(gid, pid)
    if target == "del":
        return ui.deletion_text(), ui.deletion_kb(gid)
    if target == "delsil":
        return ui.silence_text(gid), ui.silence_kb(gid)
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
# Добавление бота в группу — приглашение открыть настройки
# =============================================================================

def _offer_settings(chat_id, gid, thread_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("⚙️ Настройки чата", callback_data=f"cf|askwhere|{gid}"))
    msg = bot.send_message(
        chat_id,
        "🎉 Спасибо, что добавили меня в группу! Дайте мне права администратора, "
        "и можно настраивать чат прямо отсюда.",
        reply_markup=kb, message_thread_id=thread_id,
    )
    track_message(chat_id, msg.message_id)


def open_settings_in_dm(user_id, gid):
    """Используется для deep-link `/start cfg-<gid>` — открыть меню настроек в личке."""
    if not _authorized(gid, user_id):
        bot.send_message(user_id, "⛔ Вы не администратор этого чата.")
        return
    store.set_active_group(user_id, gid)
    text, kb = _render("root", gid)
    msg = bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML")
    track_message(msg.chat.id, msg.message_id)


def cmd_settings_command(message):
    """«Лиза, настройки» — сразу спросить, где открыть меню."""
    gid = message.chat.id
    if not _authorized(gid, message.from_user.id):
        return bot.reply_to(message, "⛔ Эта команда только для админов чата.")
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("⚙️ Настройки чата", callback_data=f"cf|askwhere|{gid}"))
    msg = bot.reply_to(message, "⚙️ Открыть настройки этого чата?", reply_markup=kb)
    track_message(gid, msg.message_id)


# =============================================================================
# Капча
# =============================================================================

def handle_new_members(message):
    gid = message.chat.id
    for user in message.new_chat_members:
        if user.id == BOT_ID:
            _offer_settings(gid, gid, thread_id=getattr(message, "message_thread_id", None))
            continue

        if store.get_captcha(gid).get("enabled", False):
            _start_captcha(message, user)


def _start_captcha(message, user):
    gid = message.chat.id
    try:
        bot.restrict_chat_member(gid, user.id, permissions=ChatPermissions(can_send_messages=False))
    except Exception as e:
        log.warning(f"[captcha restrict] {e}")

    mention = get_mention(user.id, user.first_name or user.username or str(user.id))
    text = (
        f"🧠 {mention}, подтвердите, что вы не робот, нажав на кнопку ниже. "
        "Пока вы этого не сделаете, писать в чат нельзя."
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("✅ Я не робот", callback_data=f"cf|capver|{gid}|{user.id}"))

    sent_privately = False
    try:
        bot.send_message(user.id, text, reply_markup=kb)
        sent_privately = True
    except Exception:
        pass

    if sent_privately:
        group_msg = bot.send_message(
            gid,
            f"🧠 {mention}, я отправила вам подтверждение капчи в личные сообщения.",
            message_thread_id=getattr(message, "message_thread_id", None),
        )
    else:
        group_msg = bot.send_message(gid, text, reply_markup=kb,
                                      message_thread_id=getattr(message, "message_thread_id", None))
    track_message(gid, group_msg.message_id)

    store.set_captcha_pending(gid, user.id, {
        "msg_chat": group_msg.chat.id, "msg_id": group_msg.message_id,
        "joined_at": time.time(), "private": sent_privately,
    })


def enforce_captcha(message):
    """Удаляет сообщения пользователя, который ещё не прошёл капчу. True, если удалили."""
    gid = message.chat.id
    uid = message.from_user.id if message.from_user else None
    if uid is None or not store.is_captcha_pending(gid, uid):
        return False
    try:
        bot.delete_message(gid, message.message_id)
    except Exception:
        pass
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
            if pending.get("private"):
                bot.edit_message_text("✅ Капча пройдена, теперь можно писать в чат.",
                                       chat_id=pending["msg_chat"], message_id=pending["msg_id"])
            else:
                bot.delete_message(pending["msg_chat"], pending["msg_id"])
        except Exception:
            pass


# =============================================================================
# Полная тишина / системные сообщения / массовое удаление
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


def enforce_silence(message):
    """True, если сообщение удалено режимом «Полная тишина»."""
    gid = message.chat.id
    if message.chat.type not in ("group", "supergroup"):
        return False
    if not store.get_deletion(gid).get("silence", False):
        return False
    uid = message.from_user.id if message.from_user else None
    if uid and is_chat_admin(gid, uid):
        return False
    try:
        bot.delete_message(gid, message.message_id)
    except Exception:
        pass
    return True


def enforce_system_message_deletion(message):
    """True, если служебное сообщение удалено по настройке."""
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


def parse_url_buttons(raw_text):
    """Возвращает (rows, error). rows — список рядов [{"text":.., ...}]."""
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
                btn["url"] = value
            row.append(btn)
        rows.append(row)
    if not rows:
        return None, "Не нашла ни одной кнопки."
    return rows, None


def build_markup_from_buttons(rows):
    if not rows:
        return None
    kb = types.InlineKeyboardMarkup()
    for row in rows:
        line = []
        for b in row:
            if b.get("url"):
                line.append(types.InlineKeyboardButton(b["text"], url=b["url"]))
            elif b.get("rules"):
                line.append(types.InlineKeyboardButton(b["text"], callback_data="cf|rules|0"))
            elif b.get("popup") is not None:
                line.append(types.InlineKeyboardButton(b["text"], callback_data=f"cf|txtpop|0"))
            elif b.get("alert") is not None:
                line.append(types.InlineKeyboardButton(b["text"], callback_data=f"cf|txtal|0"))
            elif b.get("share") is not None:
                share_url = f"https://t.me/share/url?text={b['share']}"
                line.append(types.InlineKeyboardButton(b["text"], url=share_url))
            elif b.get("copy") is not None:
                line.append(types.InlineKeyboardButton(b["text"], callback_data="cf|txtcp|0"))
            else:
                line.append(types.InlineKeyboardButton(b["text"], callback_data="cf|noop|0"))
        kb.row(*line)
    return kb


# =============================================================================
# Публикации: превью и рассылка
# =============================================================================

def _deliver_post(chat_id, post, thread_id=None):
    markup = build_markup_from_buttons(post.get("buttons"))
    media = post.get("media")
    if media:
        caption = media.get("caption") or post.get("text")
        sender = {
            "photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation,
            "document": bot.send_document, "voice": bot.send_voice, "audio": bot.send_audio,
            "sticker": bot.send_sticker,
        }.get(media["type"])
        if sender is None:
            return None
        if media["type"] == "sticker":
            msg = sender(chat_id, media["file_id"], message_thread_id=thread_id)
        else:
            msg = sender(chat_id, media["file_id"], caption=caption, reply_markup=markup,
                         message_thread_id=thread_id)
    else:
        text = post.get("text") or "​"
        msg = bot.send_message(chat_id, text, reply_markup=markup, message_thread_id=thread_id)
    track_message(chat_id, msg.message_id)
    return msg


def _preview_post(chat_id, gid, pid):
    post = store.get_post(gid, pid)
    if not post or (not post.get("text") and not post.get("media")):
        bot.send_message(chat_id, "🤔 Сначала задайте текст или медиа публикации.")
        return
    _deliver_post(chat_id, post, thread_id=post.get("topic_id"))


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

            # пора публиковать
            try:
                if post.get("delete_last") and post.get("last_message_id") and post.get("last_chat_id"):
                    try:
                        bot.delete_message(post["last_chat_id"], post["last_message_id"])
                    except Exception:
                        pass

                msg = _deliver_post(gid, post, thread_id=post.get("topic_id"))
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
    while True:
        try:
            _scheduler_tick()
        except Exception as e:
            log.error(f"[scheduler] {e}", exc_info=True)
        time.sleep(20)


def start_scheduler():
    threading.Thread(target=_scheduler_loop, daemon=True, name="liza-posts-scheduler").start()


# =============================================================================
# Обработка ожидаемого пользовательского ввода (текст/медиа/кнопки/время/даты)
# =============================================================================

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_TOPIC_CMD_RE = re.compile(r"^/topic_rec(\d+)$")


def _finish_pending(chat_id, uid, gid, pid, note):
    store.clear_pending(chat_id, uid)
    msg = bot.send_message(chat_id, note)
    track_message(chat_id, msg.message_id)
    _send_post_screen_followup(chat_id, gid, pid)


def _send_post_screen_followup(chat_id, gid, pid):
    text, kb = _render("popen", gid, pid)
    msg = bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    track_message(chat_id, msg.message_id)


def try_handle_pending_input(message):
    """Возвращает True, если сообщение было перехвачено как ответ на запрос настроек."""
    if not message.from_user:
        return False
    key_chat = message.chat.id
    uid = message.from_user.id

    # Команда выбора темы может прийти прямо в группе, привязана к gid, а не к чату с меню.
    m = _TOPIC_CMD_RE.match((message.text or "").strip())
    if m:
        pending = store.get_pending(message.chat.id, uid)
        if pending and pending.get("kind") == "topic" and pending.get("gid") == message.chat.id:
            thread_id = message.message_thread_id
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
        store.update_post(gid, pid, text=message.text)
        _finish_pending(key_chat, uid, gid, pid, "✅ Сообщение сохранено.")
        return True

    if kind == "media":
        media = _extract_media(message)
        if not media:
            bot.reply_to(message, "Нужно фото, видео, гиф или стикер. Или нажмите «Отмена».")
            return True
        store.update_post(gid, pid, media=media)
        _finish_pending(key_chat, uid, gid, pid, "✅ Медиа сохранено.")
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
        _finish_pending(key_chat, uid, gid, pid, "✅ Кнопки сохранены.")
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
    if message.photo:
        return {"type": "photo", "file_id": message.photo[-1].file_id, "caption": message.caption}
    if message.video:
        return {"type": "video", "file_id": message.video.file_id, "caption": message.caption}
    if message.animation:
        return {"type": "animation", "file_id": message.animation.file_id, "caption": message.caption}
    if message.document:
        return {"type": "document", "file_id": message.document.file_id, "caption": message.caption}
    if message.voice:
        return {"type": "voice", "file_id": message.voice.file_id, "caption": None}
    if message.audio:
        return {"type": "audio", "file_id": message.audio.file_id, "caption": message.caption}
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


@bot.message_handler(content_types=[
    "photo", "video", "animation", "document", "voice", "audio", "sticker",
])
def _on_media_message(message):
    try:
        if message.chat.type in ("group", "supergroup"):
            if enforce_captcha(message):
                return
            if enforce_silence(message):
                return
            track_message(message.chat.id, message.message_id)
        if try_handle_pending_input(message):
            return
    except Exception as e:
        log.error(f"[media_message] {e}", exc_info=True)


@bot.message_handler(content_types=[
    "left_chat_member", "new_chat_title", "new_chat_photo", "delete_chat_photo",
    "pinned_message", "video_chat_started", "video_chat_ended", "video_chat_scheduled",
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

    # Всё, что ниже, требует прав администратора целевой группы.
    if not _authorized(gid, call.from_user.id):
        return bot.answer_callback_query(call.id, "⛔ Только для админов чата.", show_alert=True)

    chat_id, message_id = call.message.chat.id, call.message.message_id

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

    if action == "capver":
        return _captcha_callback(call, gid, rest[0])

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
        hint = (
            "👉🏻 Установите кнопки, которые будут вставлены под сообщением\n"
            "Отправьте сообщение, структурированное следующим образом:\n\n"
            "<blockquote>• Вставьте одну кнопку:\n"
            "Название кнопки - t.me/LinkExample\n\n"
            "• Вставьте несколько кнопок в один ряд:\n"
            "Название кнопки - t.me/LinkExample && Текст кнопки - t.me/LinkExample\n\n"
            "• Вставьте несколько рядов кнопок:\n"
            "Название кнопки - t.me/LinkExample\n"
            "Название кнопки - t.me/LinkExample</blockquote>\n\n"
            "<b>Специальные кнопки</b>\n"
            "<blockquote>• Кнопка со всплывающим окном:\n"
            "Название кнопки - popup: Текст всплывающего окна\n"
            "или\n"
            "Название кнопки - alert: Текст всплывающего окна\n\n"
            "• Кнопка правил:\n"
            "Название кнопки - rules\n\n"
            "• Кнопка «Поделиться»:\n"
            "Название кнопки - share: Текст для обмена\n\n"
            "• Кнопка с копируемым текстом:\n"
            "Название кнопки - copy: Текст копируется при нажатии</blockquote>"
        )
        msg = bot.send_message(chat_id, hint, parse_mode="HTML",
                                reply_markup=ui.buttons_prompt_kb(gid, pid, bool(post.get("buttons"))))
        track_message(chat_id, msg.message_id)
        store.set_pending(chat_id, call.from_user.id, {"kind": "buttons", "gid": gid, "pid": pid})
        return

    if action == "ptxtdel":
        pid = rest[0]
        store.update_post(gid, pid, text=None)
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id, "🚫 Сообщение удалено.")
        return _show(chat_id, message_id, "popen", gid, pid)

    if action == "ptxtcancel":
        pid = rest[0]
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "popen", gid, pid)

    if action == "pmediadel":
        pid = rest[0]
        store.update_post(gid, pid, media=None)
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id, "🚫 Медиа удалено.")
        return _show(chat_id, message_id, "popen", gid, pid)

    if action == "pmediacancel":
        pid = rest[0]
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "popen", gid, pid)

    if action == "pbtndel":
        pid = rest[0]
        store.update_post(gid, pid, buttons=None)
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id, "🚫 Кнопки удалены.")
        return _show(chat_id, message_id, "popen", gid, pid)

    if action == "pbtncancel":
        pid = rest[0]
        store.clear_pending(chat_id, call.from_user.id)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "popen", gid, pid)

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

    if action == "delsil":
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "delsil", gid)

    if action == "delsiltg":
        cur = store.get_deletion(gid).get("silence", False)
        store.set_silence(gid, not cur)
        bot.answer_callback_query(call.id)
        return _show(chat_id, message_id, "delsil", gid)

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

