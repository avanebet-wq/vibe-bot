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

from runtime import bot, WAKE_RE, BOT_ID, ai_executor
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
    "закрыть цель": lambda m, a: _cmd_goal_done(m, a),
    "удалить цель": lambda m, a: _cmd_goal_delete(m, a),
    "характер": lambda m, a: _cmd_personality(m, a),
    "запись": lambda m, a: contest_start(m, a),
    "пыхнуть": lambda m, a: cmd_smoke(m),
    "заварить": lambda m, a: cmd_coffee(m),
    "выпить": lambda m, a: cmd_drink(m),
    "профиль": lambda m, a: cmd_profile(m),
    "стата": lambda m, a: cmd_minigame_stats(m, a),
}



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


def _game_enabled(message):
    return bool(get_setting(message.chat.id, "minigames_enabled", True)) and bool(__import__('settings_store').get_liza(message.chat.id).get("minigames", True))


def _dispatch(message, cmd_text):
    text = cmd_text.strip().rstrip("?!. ")
    low = text.lower()
    if not low:
        return False

    for phrase, handler in _COMPOUND_COMMANDS:
        if low == phrase or low.startswith(phrase + " "):
            rest = text[len(phrase):].strip()
            try:
                record_command(message.chat.id, phrase)
                handler(message, rest)
            except Exception as e:
                logging.error(f"[dispatch:{phrase}] {e}", exc_info=True)
            return True

    first, _, rest = text.partition(" ")
    key = first.lower()
    if key in _SINGLE_COMMANDS:
        try:
            if key in {"пыхнуть", "заварить", "выпить", "стата"} and not _game_enabled(message):
                bot.reply_to(message, "🎮 Мини-игры сейчас отключены администратором.")
                return True
            record_command(message.chat.id, key)
            _SINGLE_COMMANDS[key](message, rest.strip())
        except Exception as e:
            logging.error(f"[dispatch:{key}] {e}", exc_info=True)
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
    """Ask the AI in the background while showing Telegram's typing indicator.

    The indicator stays visible for roughly 2–4 seconds depending on the
    message length, and is refreshed every few seconds because Telegram
    expires chat actions automatically.
    """
    text_for_delay = str(args[0] if args else kwargs.get("user_text", "") or "")
    target_duration = 2.0 + min(2.0, len(text_for_delay) / 300.0)
    started = time.monotonic()
    future = ai_executor.submit(ask_liza, *args, **kwargs)
    try:
        while not future.done():
            try:
                bot.send_chat_action(message.chat.id, "typing")
            except Exception:
                pass
            time.sleep(1.0)
        elapsed = time.monotonic() - started
        while elapsed < target_duration:
            try:
                bot.send_chat_action(message.chat.id, "typing")
            except Exception:
                pass
            time.sleep(min(1.0, target_duration - elapsed))
            elapsed = time.monotonic() - started
        return future.result()
    finally:
        pass


def _safe_reply(message, text):
    """reply_to, но никогда не отправляет пустое сообщение (Telegram это запрещает)."""
    if not text or not text.strip():
        text = "Не получилось сформировать ответ. Попробуй ещё раз через мгновение."
    bot.reply_to(message, text)


def _safe_send(chat_id, text):
    if not text or not text.strip():
        return
    bot.send_message(chat_id, text)



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

        # Во время активной записи Лиза молчит в обычном разговоре, но
        # прямые команды работают без приставки «Лиза», а обращение «Лиза ...»
        # по-прежнему отправляется в обычный AI-диалог.
        # Команды управления записью остаются отдельными исключениями.
        if is_group and contest_is_active(cid):
            # Во время конкурса сначала безусловно проверяем управляющие
            # команды. Это важно: они не должны попадать в AI даже если
            # пользователь написал их без обращения «Лиза».
            active_wake = WAKE_RE.match(text)
            active_text = active_wake.group(1).strip().rstrip("?!. ") if active_wake else text.strip().rstrip("?!. ")
            active_low = active_text.lower()
            is_contest_command = (
                active_low.startswith("стоп запись")
                or active_low.startswith("добавить @")
                or active_low.startswith("записать @")
                or active_low == "пыхнуть"
                or active_low.startswith("заварить")
                or active_low.startswith("стата")
            )
            if is_contest_command:
                logging.info(
                    "contest command received: chat=%s user=%s command=%r wake=%s",
                    cid, getattr(message.from_user, "id", None), active_text, bool(active_wake)
                )
                _dispatch(message, active_text)
                return

            if active_wake:
                # Любое другое обращение «Лиза ...» во время записи
                # отправляем в обычный AI-диалог.
                logging.info(
                    "contest addressed dialogue: chat=%s user=%s text=%r",
                    cid, getattr(message.from_user, "id", None), active_text
                )
                record_liza_request(cid, getattr(message.from_user, "id", None))
                reply = _ask_liza_with_typing(message,
                    active_text,
                    angry=is_angry(cid),
                    chat_id=cid,
                    user_id=getattr(message.from_user, "id", None),
                    group_context=_liza_ai_context(message),
                )
                record_liza_response(cid, getattr(message.from_user, "id", None))
                reply = _apply_polite_filter(cid, reply)
                _safe_reply(message, reply)
                return

            return

        if is_group:
            if enforce_captcha(message):
                return
            if enforce_silence(message):
                return
            track_message(cid, message.message_id)

        if contest_settings_pending(message):
            return
        if try_handle_pending_input(message):
            return

        # Все команды работают напрямую, без обязательной приставки «Лиза».
        # В группах обычный текст по-прежнему не считается командой и дальше
        # обрабатывается как обычный разговор/AI по старым правилам.
        touch_user(message)
        direct_command_text = text.strip()
        if _dispatch(message, direct_command_text):
            return

        if message.from_user:
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
            reply = _ask_liza_with_typing(message, cmd_text, angry=is_angry(cid), chat_id=cid, user_id=getattr(message.from_user, "id", None), group_context=_liza_ai_context(message))
            record_liza_response(cid, getattr(message.from_user, "id", None))
            reply = _apply_polite_filter(cid, reply)
            _safe_reply(message, reply)
            return

        if is_group and reply_mode == "mention" and not addressed:
            return

        if not is_group:
            # Личка — общаемся без обращения по имени.
            record_liza_request(cid, getattr(message.from_user, "id", None))
            reply = _ask_liza_with_typing(message, text, angry=is_angry(cid), chat_id=cid, user_id=getattr(message.from_user, "id", None), group_context=_liza_ai_context(message))
            record_liza_response(cid, getattr(message.from_user, "id", None))
            reply = _apply_polite_filter(cid, reply)
            _safe_reply(message, reply)
            return

        # Групповой чат, сообщение не адресовано напрямую.
        if addressed:
            record_liza_request(cid, getattr(message.from_user, "id", None))
            reply = _ask_liza_with_typing(message, text, angry=is_angry(cid), chat_id=cid, user_id=getattr(message.from_user, "id", None), group_context=_liza_ai_context(message))
            record_liza_response(cid, getattr(message.from_user, "id", None))
            reply = _apply_polite_filter(cid, reply)
            _safe_reply(message, reply)
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
            reply = _ask_liza_with_typing(message, text, angry=is_angry(cid), max_tokens=80, chat_id=cid, user_id=getattr(message.from_user, "id", None), group_context=_liza_ai_context(message))
            record_liza_response(cid, getattr(message.from_user, "id", None))
            reply = _apply_polite_filter(cid, reply)
            _safe_send(cid, reply)

    except Exception as e:
        logging.error(f"[text_handler] {e}", exc_info=True)
