from group_context import record_group_message
from autoactivity import remember_message as aa_remember_message, should_auto_reply, mark_liza_response
from dialogue_context import record as record_dialogue, mark_liza as mark_dialogue_liza, format_for_ai
from events import emit as emit_event
from mood_state import on_message as update_mood, on_event as update_mood_event
from user_memory import infer_safe_fact, add_fact, get_facts, clear as clear_user_memory
from social_context import observe as observe_social
from contest import (is_active as contest_is_active, cmd_start as contest_start, cmd_stop as contest_stop, cmd_add_participant as contest_add_participant)
from minigames import cmd_smoke, cmd_coffee, cmd_drink, cmd_stats as cmd_minigame_stats
from profile import cmd_profile, touch_user
from karma import observe_message, get_user_context, get_karma, change_karma, give_karma, give_negative_karma, auto_delta
from contest_settings import open_settings as contest_settings_open, handle_pending as contest_settings_pending
from reliability import mark_ok, mark_error
from goals import add as goal_add, list_open as goal_list, complete as goal_complete, remove as goal_remove
from chat_personality import get as get_chat_personality, set_value as set_chat_personality
from mood_state import decay as decay_mood
# -*- coding: utf-8 -*-
"""Разбор сообщений и маршрутизация команд Лизы."""
import random
import logging
import time
import threading
import html
import re
from concurrent.futures import ThreadPoolExecutor

from runtime import bot, WAKE_RE, BOT_ID
from config import STORY_AUTOTELL_CHANCE, BAD_WORDS
from utils import get_setting, set_setting, remember_user
from mood import (
    get_chatter_chance, is_autoactivity, is_polite, is_angry, is_asleep,
    cmd_less_spam, cmd_more_active, cmd_autoactivity_on, cmd_autoactivity_off,
    cmd_sleep, cmd_wakeup, cmd_polite_on, cmd_polite_off, cmd_angry_on, cmd_calm_down,
)
from moderation import (
    cmd_ban, cmd_unban, cmd_banlist, cmd_mute, cmd_unmute, cmd_mutelist,
    cmd_warn, cmd_unwarn, cmd_mywarns, cmd_warns_of,
    cmd_moderation_settings, cmd_set_warn_limit, cmd_set_warn_action,
    cmd_set_warn_mute_duration, cmd_set_auto_delete, cmd_set_protect_admins, cmd_modlog,
)
from stats import cmd_stats, record_message, record_command, record_liza_request, record_liza_response
from stories import cmd_tell_story, cmd_stories_on, cmd_stories_off, maybe_autotell
from help import cmd_help
from ai import ask_liza
from queue import Queue, Full

_AI_QUEUE = Queue(maxsize=24)
_AI_ACTIVE_BY_CHAT = {}
_AI_STATE_LOCK = threading.RLock()
_AI_MAX_PER_CHAT = 2
_COMMAND_ANALYTICS_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="liza-cmd-analytics")
_CALL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="liza-call")
_CALL_LOCK = threading.RLock()
_CALL_LAST_AT = {}
_CALL_COOLDOWN = 60.0
_CALL_EMOJIS = ("📣", "🔥", "🚀", "⚡", "🎯", "🗣️", "💥", "🔔", "👀", "🏃")

def _ai_job_finished(chat_id):
    if chat_id is None:
        return
    with _AI_STATE_LOCK:
        current = _AI_ACTIVE_BY_CHAT.get(chat_id, 0)
        if current <= 1:
            _AI_ACTIVE_BY_CHAT.pop(chat_id, None)
        else:
            _AI_ACTIVE_BY_CHAT[chat_id] = current - 1

def _typing_loop(chat_id, stop_event):
    """Keep Telegram's `typing…` indicator visible while AI is working.

    Telegram keeps chat actions only for a few seconds, so refresh the action
    periodically until the AI request finishes. Failures are intentionally
    ignored: the answer itself must not be affected by the indicator.
    """
    if chat_id is None:
        return
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        stop_event.wait(4.0)


def _ai_worker():
    while True:
        job = _AI_QUEUE.get()
        message = None
        chat_id = None
        typing_stop = None
        typing_thread = None
        try:
            if job is None:
                return
            message, args, kwargs, reply_mode = job
            chat_id = getattr(getattr(message, "chat", None), "id", None)

            # Show `typing…` immediately and refresh it while Qwen is thinking
            # or generating the response. This works independently of the AI
            # provider and does not add artificial delay to the request.
            typing_stop = threading.Event()
            typing_thread = threading.Thread(
                target=_typing_loop,
                args=(chat_id, typing_stop),
                daemon=True,
                name="liza-typing",
            )
            typing_thread.start()

            reply = ask_liza(*args, **kwargs)
            reply = _apply_polite_filter(chat_id, reply) if reply is not None else reply
            if reply_mode == "send":
                sent = _safe_send(chat_id, reply)
            else:
                sent = _safe_reply(message, reply)
            if sent:
                record_liza_response(chat_id, getattr(message.from_user, "id", None))
                try:
                    _record_liza_sent(message, sent)
                except Exception:
                    pass
        except Exception:
            logging.exception("[ai-worker] failed")
            if message is not None:
                try:
                    _safe_reply(message, "⚠️ Не удалось получить ответ. Попробуй ещё раз через несколько секунд.")
                except Exception:
                    pass
        finally:
            if typing_stop is not None:
                typing_stop.set()
            _ai_job_finished(chat_id)
            _AI_QUEUE.task_done()

for _ in range(3):
    threading.Thread(target=_ai_worker, daemon=True, name="liza-ai-queue").start()

def _enqueue_ai_reply(message, *args, reply_mode="reply", processing_notice=False, **kwargs):
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    with _AI_STATE_LOCK:
        active = _AI_ACTIVE_BY_CHAT.get(chat_id, 0)
        if active >= _AI_MAX_PER_CHAT:
            if processing_notice:
                try:
                    _safe_reply(message, "⏳ У меня уже есть запросы в обработке. Подожди пару секунд.")
                except Exception:
                    pass
            return False
        # Start Telegram's typing indicator immediately, before the job enters
        # the AI queue. This removes the visible 2-3s gap caused by waiting for
        # an AI worker to pick up the request. The worker continues refreshing
        # the indicator while Qwen is thinking/generating.
        try:
            if chat_id is not None:
                bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        try:
            _AI_QUEUE.put_nowait((message, args, kwargs, reply_mode))
        except Full:
            if processing_notice:
                try:
                    _safe_reply(message, "⏳ Сейчас слишком много запросов. Повтори чуть позже.")
                except Exception:
                    pass
            logging.warning("[ai] queue full for chat %s", chat_id)
            return False
        _AI_ACTIVE_BY_CHAT[chat_id] = active + 1
    if processing_notice:
        try:
            _safe_reply(message, "⏳ Обрабатываю…")
        except Exception:
            logging.exception("[ai] failed to send processing notice")
    return True

from settings import (
    try_handle_pending_input, enforce_silence, enforce_captcha,
    track_message, cmd_settings_command, open_settings_in_dm,
    send_dm_start_intro, send_dm_start_group_picker, send_group_start,
)

# Многословные команды проверяются первыми (от самых длинных, чтобы не путать с однословными)
_COMPOUND_COMMANDS = [
    ("снять варн", cmd_unwarn),
    ("мои варны", lambda m, a: cmd_mywarns(m)),
    ("снять мут", cmd_unmute),
    ("снять бан", cmd_unban),
    ("что с чатом", lambda m, a: cmd_stats(m, a)),
    ("расскажи историю", lambda m, a: cmd_tell_story(m)),
    ("включи истории", lambda m, a: cmd_stories_on(m)),
    ("отключи истории", lambda m, a: cmd_stories_off(m)),
    ("не спамь", lambda m, a: cmd_less_spam(m)),
    ("включи автоактивность", lambda m, a: cmd_autoactivity_on(m)),
    ("отключи автоактивность", lambda m, a: cmd_autoactivity_off(m)),
    ("будь вежлива", lambda m, a: cmd_polite_on(m)),
    ("будь вежливой", lambda m, a: cmd_polite_on(m)),
    ("лимит варнов", cmd_set_warn_limit),
    ("автодействие варнов", cmd_set_warn_action),
    ("мут за варны", cmd_set_warn_mute_duration),
    ("моя статистика", lambda m, a: cmd_stats(m, "моя")),
    ("статистика пользователя", lambda m, a: cmd_stats(m, "пользователь " + a)),
    ("настройки розыгрыша", lambda m, a: contest_settings_open(m)),
    ("стоп запись", lambda m, a: contest_stop(m)),
    ("добавить", lambda m, a: contest_add_participant(m, a)),
    ("записать", lambda m, a: contest_add_participant(m, a)),
    ("очистить память", lambda m, a: _cmd_clear_memory(m)),
    ("закрыть цель", lambda m, a: _cmd_goal_done(m, a)),
    ("удалить цель", lambda m, a: _cmd_goal_delete(m, a)),
    ("удаляй нарушения", lambda m, a: cmd_set_auto_delete(m, True)),
    ("не удаляй нарушения", lambda m, a: cmd_set_auto_delete(m, False)),
    ("защищай админов", lambda m, a: cmd_set_protect_admins(m, True)),
    ("не защищай админов", lambda m, a: cmd_set_protect_admins(m, False)),
]
_COMPOUND_COMMANDS.sort(key=lambda x: -len(x[0]))

_SINGLE_COMMANDS = {
    "бан": cmd_ban,
    "разбан": cmd_unban,
    "банлист": lambda m, a: cmd_banlist(m),
    "мут": cmd_mute,
    "размут": cmd_unmute,
    "мутлист": lambda m, a: cmd_mutelist(m),
    "варн": cmd_warn,
    "варны": cmd_warns_of,
    "статистика": lambda m, a: cmd_stats(m, a),
    "активность": lambda m, a: cmd_stats(m, a),
    "активнее": lambda m, a: cmd_more_active(m),
    "отключись": lambda m, a: cmd_sleep(m),
    "включись": lambda m, a: cmd_wakeup(m),
    "матерись": lambda m, a: cmd_polite_off(m),
    "разозлись": lambda m, a: cmd_angry_on(m),
    "успокойся": lambda m, a: cmd_calm_down(m),
    "помощь": lambda m, a: cmd_help(m),
    "память": lambda m, a: _cmd_memory(m),
    "команды": lambda m, a: cmd_help(m),
    "настройки": lambda m, a: cmd_settings_command(m),
    "модерация": lambda m, a: cmd_moderation_settings(m),
    "модлог": lambda m, a: cmd_modlog(m),
    "цель": lambda m, a: _cmd_goal(m, a),
    "цели": lambda m, a: _cmd_goals(m),
    "характер": lambda m, a: _cmd_personality(m, a),
    "запись": lambda m, a: contest_start(m, a),
    "пыхнуть": lambda m, a: cmd_smoke(m),
    "заварить": lambda m, a: cmd_coffee(m),
    "выпить": lambda m, a: cmd_drink(m),
    "профиль": lambda m, a: cmd_profile(m),
    "стата": lambda m, a: cmd_minigame_stats(m, a),
    "топ": lambda m, a: cmd_minigame_stats(m, a),
    "калл": lambda m, a: _cmd_call(m, a),
}



def _ai_user_context(message):
    try:
        u = getattr(message, "from_user", None)
        if not u:
            return None
        return get_user_context(message.chat.id, u.id)
    except Exception:
        return None


def _handle_manual_karma(message):
    """Handle + / - replies as explicit karma votes for the replied user."""
    if getattr(message.chat, "type", "") not in ("group", "supergroup"):
        return False
    text = (message.text or "").strip()
    if not re.fullmatch(r"\+{1,3}|-", text):
        return False
    reply = getattr(message, "reply_to_message", None)
    target = getattr(reply, "from_user", None)
    actor = getattr(message, "from_user", None)
    if not target or getattr(target, "is_bot", False) or not actor:
        bot.reply_to(message, "⚠️ Поставить карму можно ответом + или - на сообщение участника.")
        return True
    if target.id == actor.id:
        bot.reply_to(message, "😏 Сам себе карму накручивать не дам.")
        return True
    if text.startswith("+"):
        amount = len(text)
        ok, old, new, used_today, reason = give_karma(
            message.chat.id, actor.id, target.id, amount
        )
        if not ok:
            remaining = max(0, 3 - used_today)
            if reason == "достигнут максимум кармы":
                bot.reply_to(message, "⚠️ У этого участника уже максимальная карма.")
            elif reason == "дневной лимит":
                bot.reply_to(
                    message,
                    f"⏳ Сегодня ты уже потратил(а) {used_today}/3 кармы. Осталось: {remaining}."
                )
            return True
        delta = new - old
        sign = f"+{delta}"
        emoji = "📈"
        verb = "повысила"
    else:
        ok, old, new, used_today, reason = give_negative_karma(
            message.chat.id, actor.id, target.id, 1
        )
        if not ok:
            remaining = max(0, 2 - used_today)
            if reason == "достигнут минимум кармы":
                bot.reply_to(message, "⚠️ У этого участника уже минимальная карма.")
            elif reason == "дневной лимит":
                bot.reply_to(
                    message,
                    f"⏳ Сегодня ты уже поставил(а) {used_today}/2 минуса. Осталось: {remaining}."
                )
            return True
        sign = "-1"
        emoji = "📉"
        verb = "понизила"
    name = html.escape((target.first_name or "Пользователь").split(None, 1)[0])
    bot.send_message(
        message.chat.id,
        f'{emoji} <a href="tg://user?id={target.id}">{name}</a>: Лиза {verb} карму на {sign}. Карма: <b>{new:+d}</b>',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return True


def _apply_auto_karma(message):
    """Conservatively adjust karma from clearly positive/negative behavior."""
    u = getattr(message, "from_user", None)
    if not u or getattr(u, "is_bot", False):
        return
    delta, reason = auto_delta(message.text or "")
    if not delta:
        return
    old, new = change_karma(message.chat.id, u.id, delta, reason, u.id)
    if old == new:
        return
    name = html.escape((u.first_name or "Пользователь").split(None, 1)[0])
    emoji = "📈" if delta > 0 else "📉"
    verb = "повысила" if delta > 0 else "понизила"
    bot.send_message(
        message.chat.id,
        f'{emoji} <a href="tg://user?id={u.id}">{name}</a>: Лиза {verb} тебе карму {delta:+d} — {html.escape(reason)}. Карма: <b>{new:+d}</b>',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _cmd_memory(message):
    facts=get_facts(message.chat.id, message.from_user.id)
    if not facts: return bot.reply_to(message, "🧠 О тебе пока ничего не запомнено.")
    lines=["🧠 <b>Что я помню:</b>"]+[f"{i}. {x['text']}" for i,x in enumerate(facts,1)]
    bot.reply_to(message, "\n".join(lines))

def _cmd_clear_memory(message):
    clear_user_memory(message.chat.id, message.from_user.id)
    bot.reply_to(message, "🧠 Память о тебе в этом чате очищена.")


def _cmd_goals(message):
    items = goal_list(message.chat.id)
    if not items: return bot.reply_to(message, "🎯 Открытых целей нет.")
    lines=["🎯 <b>Цели:</b>"]
    for i,x in enumerate(items[:15],1): lines.append(f"{i}. <b>{x['id']}</b> — {x['title']}")
    lines.append("\nЗакрыть: <code>закрыть цель ID</code>")
    bot.reply_to(message,"\n".join(lines))

def _cmd_goal(message,args):
    item=goal_add(message.chat.id,args.strip(),getattr(message.from_user,'id',None)) if args.strip() else None
    bot.reply_to(message, (f"🎯 Добавила цель: <b>{item['title']}</b>\nID: <code>{item['id']}</code>" if item else "⚠️ Укажи текст цели."))

def _cmd_goal_done(message,args):
    ok=goal_complete(message.chat.id,args.strip().split()[0] if args.strip() else "")
    bot.reply_to(message,"✅ Цель закрыта." if ok else "⚠️ Не нашла такую цель.")

def _cmd_goal_delete(message,args):
    ok=goal_remove(message.chat.id,args.strip().split()[0] if args.strip() else "")
    bot.reply_to(message,"🗑️ Цель удалена." if ok else "⚠️ Не нашла такую цель.")

def _cmd_personality(message,args):
    cid=message.chat.id; raw=(args or '').strip()
    if not raw:
        p=get_chat_personality(cid); bot.reply_to(message,"🎭 <b>Характер:</b>\n"+"\n".join(f"{k}: {v}/100" for k,v in p.items())); return
    parts=raw.split()
    aliases={'юмор':'humor','сарказм':'sarcasm','доброта':'friendliness','грубость':'rudeness','серьёзность':'seriousness','серьезность':'seriousness','разговорчивость':'verbosity'}
    key=aliases.get(parts[0].lower(),parts[0].lower())
    try: value=int(parts[1])
    except Exception: return bot.reply_to(message,"⚠️ Формат: <code>характер юмор 80</code>")
    if set_chat_personality(cid,key,value): bot.reply_to(message,f"✅ {key}: {max(0,min(100,value))}/100")
    else: bot.reply_to(message,"⚠️ Неизвестный параметр характера.")


def _cmd_call(message, args):
    """Созыв участников чата: имя-ссылка + разные эмодзи, затем финал."""
    if getattr(message.chat, "type", "") not in ("group", "supergroup"):
        bot.reply_to(message, "📣 Команда «калл» работает только в группе.")
        return

    cid = str(message.chat.id)
    now = time.time()
    with _CALL_LOCK:
        last = _CALL_LAST_AT.get(cid, 0.0)
        if now - last < _CALL_COOLDOWN:
            left = max(1, int(_CALL_COOLDOWN - (now - last)))
            bot.reply_to(message, f"⏳ Призыв уже был недавно. Подожди ещё {left} сек.")
            return
        _CALL_LAST_AT[cid] = now

    reason = (args or "").strip()
    _CALL_EXECUTOR.submit(_run_call, message, reason)


def _run_call(message, reason):
    cid = message.chat.id
    try:
        # Telegram Bot API не даёт ботам общего getChatMembers, поэтому берём
        # всех участников, которых Лиза уже видела в переписке за период
        # хранения статистики. Их ID сохраняются вместе с именами.
        from database import db_get, conn, db_lock

        store = db_get("stats", {}) or {}
        chat = store.get(str(cid), {}) or {}
        names = dict(chat.get("names", {}) or {})
        user_ids = set(str(uid) for uid in names.keys() if str(uid).lstrip("-").isdigit())

        # Дополняем список игроками мини-игр, которых могло не быть в
        # недавней статистике сообщений. Их имя уже хранится в событиях.
        try:
            with db_lock:
                rows = conn.execute(
                    "SELECT DISTINCT user_id, display_name FROM minigame_events WHERE chat_id=?",
                    (cid,),
                ).fetchall()
            for uid, display_name in rows:
                uid = str(uid)
                if uid.lstrip("-").isdigit():
                    user_ids.add(uid)
                    if display_name:
                        names[uid] = str(display_name)
        except Exception:
            logging.exception("[call] failed to read minigame users")

        # Берём также ранее замеченных участников. Для них имя при необходимости
        # подтянем через get_chat_member ниже.
        try:
            with db_lock:
                rows = conn.execute(
                    "SELECT user_id FROM chat_user_presence WHERE chat_id=?",
                    (cid,),
                ).fetchall()
            for (uid,) in rows:
                uid = str(uid)
                if uid.lstrip("-").isdigit():
                    user_ids.add(uid)
        except Exception:
            logging.exception("[call] failed to read chat presence")

        # Автор команды обязательно участвует, даже если сообщение ещё не
        # успело попасть в статистику.
        author = getattr(message, "from_user", None)
        if author and getattr(author, "id", None) is not None:
            user_ids.add(str(author.id))
            names.setdefault(str(author.id), getattr(author, "first_name", None) or "Пользователь")

        # Если статистика пуста, попробуем хотя бы администраторов.
        if not user_ids:
            try:
                for member in bot.get_chat_administrators(cid) or []:
                    u = getattr(member, "user", None)
                    if u and not getattr(u, "is_bot", False):
                        user_ids.add(str(u.id))
                        names[str(u.id)] = getattr(u, "first_name", None) or "Пользователь"
            except Exception:
                logging.exception("[call] failed to get administrators")

        # Для участников, у которых в статистике нет имени, пытаемся один раз
        # получить актуальное имя из Telegram. Это не перебирает всех участников
        # чата — Bot API такого метода не предоставляет — а только уже известных
        # Лизе пользователей.
        for uid in list(user_ids):
            if names.get(uid):
                continue
            try:
                member = bot.get_chat_member(cid, int(uid))
                u = getattr(member, "user", None)
                if not u or getattr(u, "is_bot", False):
                    user_ids.discard(uid)
                    continue
                names[uid] = getattr(u, "first_name", None) or "Пользователь"
            except Exception:
                # Если Telegram не дал карточку пользователя, лучше пропустить
                # его, чем отправлять бессмысленный тег без имени.
                user_ids.discard(uid)

        # Ботов не зовём; имя берём только как имя, без фамилии.
        ordered = sorted(user_ids, key=lambda uid: (str(names.get(uid, "")).lower(), uid))
        if not ordered:
            bot.send_message(cid, "📣 Никого не нашла для призыва.")
            return

        header = "📣 <b>СОЗЫВ!</b>"
        if reason:
            header += f"\n💬 {html.escape(reason)}"
        bot.send_message(cid, header, parse_mode="HTML")

        for idx, uid in enumerate(ordered):
            name = str(names.get(uid) or "Пользователь").strip()
            name = name.split(None, 1)[0] or "Пользователь"
            name = html.escape(name)
            emoji = _CALL_EMOJIS[idx % len(_CALL_EMOJIS)]
            try:
                bot.send_message(
                    cid,
                    f'{emoji} <a href="tg://user?id={uid}">{name}</a>',
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                logging.exception("[call] failed to mention user %s", uid)
            # Не долбим Telegram запросами слишком быстро.
            time.sleep(0.18)

        bot.send_message(cid, "📣 <b>Призыв окончен!</b> Все, кого Лиза знает в этом чате, позваны 💨", parse_mode="HTML")
    except Exception:
        logging.exception("[call] failed")
        try:
            bot.send_message(cid, "⚠️ Не смогла завершить призыв. Попробуй ещё раз.")
        except Exception:
            pass


def _game_enabled(message):
    # Единственный источник истины для переключателя мини-игр.
    # Старый chat_settings.minigames_enabled нигде больше не используется
    # и заставлял каждый запуск игры делать лишний запрос PostgreSQL.
    try:
        from settings_store import get_liza
        return bool(get_liza(message.chat.id).get("minigames", True))
    except Exception:
        return True


def _record_command_async(chat_id, command):
    """Записывает служебную статистику команды вне критического пути ответа."""
    try:
        _COMMAND_ANALYTICS_EXECUTOR.submit(record_command, chat_id, command)
    except Exception:
        logging.exception("[command-analytics] submit failed")


def _dispatch(message, cmd_text):
    """Максимально короткий путь для команд: обработчик -> ответ, аналитика после."""
    started = time.perf_counter()
    text = cmd_text.strip().rstrip("?!. ")
    low = text.lower()
    if not low:
        return False

    for phrase, handler in _COMPOUND_COMMANDS:
        if low == phrase or low.startswith(phrase + " "):
            rest = text[len(phrase):].strip()
            try:
                handler(message, rest)
            except Exception as e:
                logging.error(f"[dispatch:{phrase}] {e}", exc_info=True)
            finally:
                _record_command_async(message.chat.id, phrase)
                elapsed = time.perf_counter() - started
                if elapsed >= 0.5:
                    logging.info("[command-latency] %s %.3fs", phrase, elapsed)
            return True

    first, _, rest = text.partition(" ")
    key = first.lower()
    if key in _SINGLE_COMMANDS:
        try:
            if key in {"пыхнуть", "заварить", "выпить", "стата", "топ"} and not _game_enabled(message):
                bot.reply_to(message, "🎮 Мини-игры сейчас отключены администратором.")
                return True
            _SINGLE_COMMANDS[key](message, rest.strip())
        except Exception as e:
            logging.error(f"[dispatch:{key}] {e}", exc_info=True)
        finally:
            _record_command_async(message.chat.id, key)
            elapsed = time.perf_counter() - started
            if elapsed >= 0.5:
                logging.info("[command-latency] %s %.3fs", key, elapsed)
        return True

    return False


def _apply_polite_filter(cid, text):
    if not is_polite(cid):
        return text
    lowered = text.lower()
    for bad in BAD_WORDS:
        if bad in lowered:
            # грубая, но надёжная маскировка
            idx = lowered.find(bad)
            text = text[:idx] + "***" + text[idx + len(bad):]
            lowered = text.lower()
    return text


def _ask_liza_with_typing(message, *args, **kwargs):
    """Compatibility wrapper: enqueue AI work so Telegram handlers never wait."""
    return _enqueue_ai_reply(message, *args, **kwargs)


def _safe_reply(message, text):
    """Безопасный ответ: не отправляет пустой Telegram message."""
    if text is None:
        return bot.reply_to(message, "⏳ Слишком много обращений подряд. Повтори через несколько секунд.")
    text = str(text).strip()
    if not text:
        return bot.reply_to(message, "⏳ Не удалось получить ответ от сервиса. Повтори через несколько секунд.")
    return bot.reply_to(message, text)


def _safe_send(chat_id, text):
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    return bot.send_message(chat_id, text)



def _liza_ai_context(message):
    """Return recent group context for an AI request."""
    try:
        if getattr(message.chat, "type", "") in ("group", "supergroup"):
            from group_context import get_group_context
            return get_group_context(message.chat.id, limit=12)
    except Exception:
        pass
    return None


def _mark_liza_autoactivity(message):
    try:
        if getattr(message.chat, "type", "") in ("group", "supergroup"):
            mark_liza_response(message.chat.id)
    except Exception:
        pass


def _record_liza_sent(message, sent_message):
    try:
        if getattr(message.chat, "type", "") in ("group", "supergroup"):
            text = getattr(sent_message, "text", "") or ""
            if text:
                mark_dialogue_liza(
                    message.chat.id,
                    text,
                    message_id=getattr(sent_message, "message_id", None),
                )
    except Exception:
        pass


def _emit_liza_event(message, event_type, **data):
    try:
        if getattr(message.chat, "type", "") in ("group", "supergroup"):
            emit_event(message.chat.id, event_type, **data)
    except Exception:
        pass

@bot.message_handler(commands=["start"])
def on_start(message):
    if message.chat.type == "private":
        parts = (message.text or "").split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        if payload.startswith("cfg-"):
            gid_str = payload[len("cfg-"):]
            try:
                gid = int(gid_str)
            except ValueError:
                gid = None
            if gid is not None:
                return open_settings_in_dm(message.from_user.id, gid)

        if send_dm_start_group_picker(message.chat.id, message.from_user.id):
            return
        send_dm_start_intro(message.chat.id)
    else:
        send_group_start(message)


@bot.message_handler(commands=["help"])
def on_help_cmd(message):
    cmd_help(message)


@bot.message_handler(content_types=["text"])
def text_handler(message):
    try:
        cid = message.chat.id
        text = message.text or ""
        is_group = message.chat.type in ("group", "supergroup")

        # IMPORTANT: Telegram typing must start before ANY database/context work.
        # The previous implementation started it only inside _enqueue_ai_reply,
        # but this handler performs captcha/silence/settings/profile/memory/context
        # work before reaching the enqueue call. Those synchronous operations could
        # create the visible 2-3s gap even though the AI itself was already async.
        # Detect a direct "Лиза ..." message immediately and fire the action now.
        early_wake = WAKE_RE.match(text)
        if early_wake:
            try:
                bot.send_chat_action(cid, "typing")
            except Exception:
                pass

        # CAPTCHA должна оставаться самым ранним guard:
        # пользователь без проверки не может запускать команды.
        if is_group and enforce_captcha(message):
            return

        # Явная оценка кармы: ответ + / - на сообщение участника.
        if _handle_manual_karma(message):
            return

        # УЛЬТРА-FAST PATH: известную команду маршрутизируем сразу.
        # Она не должна ждать проверки конкурса, полной тишины, ожидания
        # настроек, учёта сообщения, памяти и прочей аналитики.
        # Каждая административная команда сама проверяет права там, где это нужно.
        direct_command_text = text.strip()
        if _dispatch(message, direct_command_text):
            return

        # Во время активной записи обычный разговор блокируется, но
        # прямое обращение «Лиза ...» остаётся рабочим AI-диалогом.
        # Важно: проверка конкурса теперь выполняется ТОЛЬКО для текста,
        # который не оказался известной командой.
        if is_group and contest_is_active(cid):
            active_wake = WAKE_RE.match(text)
            if active_wake:
                active_text = active_wake.group(1).strip().rstrip("?!. ")
                record_liza_request(cid, getattr(message.from_user, "id", None))
                _enqueue_ai_reply(
                    message, active_text, angry=is_angry(cid), chat_id=cid,
                    user_id=getattr(message.from_user, "id", None),
                    group_context=_liza_ai_context(message), user_context=_ai_user_context(message)
                )
            return

        # Обычные сообщения могут быть удалены «Полной тишиной».
        # Команды уже вышли выше и потому не задерживаются этим guard.
        if is_group and enforce_silence(message):
            return

        # Ожидаемый ввод настроек обрабатываем только если это НЕ команда.
        if contest_settings_pending(message):
            return
        if try_handle_pending_input(message):
            return

        # Обычный текст идёт по полному pipeline.
        touch_user(message)
        if is_group:
            track_message(cid, message.message_id, getattr(message.chat, "title", None))
            try:
                display_name = getattr(message.from_user, "first_name", None) or getattr(message.from_user, "username", None) or "Пользователь"
                record_group_message(cid, display_name, text)
            except Exception:
                pass

        if message.from_user:
            try:
                observe_message(cid, message.from_user.id, text, getattr(message.from_user, "first_name", None))
            except Exception:
                pass
            remember_user(message.from_user)
            fact = infer_safe_fact(text) if get_setting(cid, "memory_enabled", True) else None
            if fact and is_group:
                try: add_fact(cid, message.from_user.id, fact)
                except Exception: pass

        if is_group:
            record_message(message)
            try:
                reply_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
                observe_social(cid, message.from_user.id if message.from_user else 0, getattr(reply_user, "id", None))
                update_mood(cid, text, is_direct=False, is_question=("?" in text or "？" in text))
                _apply_auto_karma(message)
                mark_ok()
            except Exception: pass

        asleep = is_group and is_asleep(cid)

        # Обращение "Лиза, ..."
        wake_match = WAKE_RE.match(text)
        try:
            from settings_store import get_liza
            reply_mode = get_liza(cid).get("reply_mode", "everyone")
        except Exception:
            reply_mode = "everyone"

        # Тишина влияет только на обычные ответы; команды выше уже обработаны.
        if is_group and reply_mode == "silent":
            return

        addressed = bool(wake_match) or (not is_group) or (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == BOT_ID
        )

        if asleep:
            # Пока Лиза спит, реагируем только на команду пробуждения.
            if wake_match and wake_match.group(1).strip().rstrip("?!. ").lower() == "включись":
                cmd_wakeup(message)
            return

        if wake_match:
            cmd_text = wake_match.group(1)
            if _dispatch(message, cmd_text):
                return
            # Обратились по имени, но это не команда — считаем, что это вопрос к AI.
            record_liza_request(cid, getattr(message.from_user, "id", None))
            _enqueue_ai_reply(message, cmd_text, angry=is_angry(cid), chat_id=cid, user_id=getattr(message.from_user, "id", None), group_context=_liza_ai_context(message), user_context=_ai_user_context(message))
            return

        if is_group and reply_mode == "mention" and not addressed:
            return

        if not is_group:
            # Личка — общаемся без обращения по имени.
            record_liza_request(cid, getattr(message.from_user, "id", None))
            _enqueue_ai_reply(message, text, angry=is_angry(cid), chat_id=cid, user_id=getattr(message.from_user, "id", None), group_context=_liza_ai_context(message), user_context=_ai_user_context(message))
            return

        # Групповой чат, сообщение не адресовано напрямую.
        if addressed:
            record_liza_request(cid, getattr(message.from_user, "id", None))
            _enqueue_ai_reply(message, text, angry=is_angry(cid), chat_id=cid, user_id=getattr(message.from_user, "id", None), group_context=_liza_ai_context(message), user_context=_ai_user_context(message))
            return

        # Если включена автоактивность — не встреваем во время бурного обсуждения.
        if is_autoactivity(cid):
            if not should_auto_reply(cid, text, get_chatter_chance(cid)):
                aa_remember_message(cid, text, is_liza=False)
                return
        if maybe_autotell(message, STORY_AUTOTELL_CHANCE):
            return

        chance = get_chatter_chance(cid)
        if random.random() < chance:
            record_liza_request(cid, getattr(message.from_user, "id", None))
            _enqueue_ai_reply(message, text, angry=is_angry(cid), max_tokens=80, chat_id=cid, user_id=getattr(message.from_user, "id", None), group_context=_liza_ai_context(message), reply_mode="send", processing_notice=False)

    except Exception as e:
        logging.error(f"[text_handler] {e}", exc_info=True)
