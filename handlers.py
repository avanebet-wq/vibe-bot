# -*- coding: utf-8 -*-
"""Разбор сообщений и маршрутизация команд Лизы."""
import random
import logging

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
)
from stats import cmd_stats, record_message
from stories import cmd_tell_story, cmd_stories_on, cmd_stories_off, maybe_autotell
from help import cmd_help
from ai import ask_liza

# Многословные команды проверяются первыми (от самых длинных, чтобы не путать с однословными)
_COMPOUND_COMMANDS = [
    ("снять варн", cmd_unwarn),
    ("мои варны", lambda m, a: cmd_mywarns(m)),
    ("снять мут", cmd_unmute),
    ("снять бан", cmd_unban),
    ("что с чатом", lambda m, a: cmd_stats(m)),
    ("расскажи историю", lambda m, a: cmd_tell_story(m)),
    ("включи истории", lambda m, a: cmd_stories_on(m)),
    ("отключи истории", lambda m, a: cmd_stories_off(m)),
    ("не спамь", lambda m, a: cmd_less_spam(m)),
    ("включи автоактивность", lambda m, a: cmd_autoactivity_on(m)),
    ("отключи автоактивность", lambda m, a: cmd_autoactivity_off(m)),
    ("будь вежлива", lambda m, a: cmd_polite_on(m)),
    ("будь вежливой", lambda m, a: cmd_polite_on(m)),
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
    "статистика": lambda m, a: cmd_stats(m),
    "активнее": lambda m, a: cmd_more_active(m),
    "отключись": lambda m, a: cmd_sleep(m),
    "включись": lambda m, a: cmd_wakeup(m),
    "матерись": lambda m, a: cmd_polite_off(m),
    "разозлись": lambda m, a: cmd_angry_on(m),
    "успокойся": lambda m, a: cmd_calm_down(m),
    "помощь": lambda m, a: cmd_help(m),
    "команды": lambda m, a: cmd_help(m),
}


def _dispatch(message, cmd_text):
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
            return True

    first, _, rest = text.partition(" ")
    key = first.lower()
    if key in _SINGLE_COMMANDS:
        try:
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


@bot.message_handler(commands=["start"])
def on_start(message):
    if message.chat.type == "private":
        bot.send_message(
            message.chat.id,
            "💗 Привет, я Лиза! Добавь меня в группу и дай права администратора, "
            "чтобы я могла модерировать чат. Команда «Лиза, помощь» покажет всё, что я умею.",
        )
    else:
        cmd_help(message)


@bot.message_handler(commands=["help"])
def on_help_cmd(message):
    cmd_help(message)


@bot.message_handler(content_types=["text"])
def text_handler(message):
    try:
        cid = message.chat.id
        text = message.text or ""
        is_group = message.chat.type in ("group", "supergroup")

        if message.from_user:
            remember_user(message.from_user)

        if is_group:
            record_message(message)

        asleep = is_group and is_asleep(cid)

        # Обращение "Лиза, ..."
        wake_match = WAKE_RE.match(text)
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
            reply = ask_liza(cmd_text, angry=is_angry(cid))
            reply = _apply_polite_filter(cid, reply)
            bot.reply_to(message, reply)
            return

        if not is_group:
            # Личка — общаемся без обращения по имени.
            reply = ask_liza(text, angry=is_angry(cid))
            reply = _apply_polite_filter(cid, reply)
            bot.reply_to(message, reply)
            return

        # Групповой чат, сообщение не адресовано напрямую.
        if addressed:
            reply = ask_liza(text, angry=is_angry(cid))
            reply = _apply_polite_filter(cid, reply)
            bot.reply_to(message, reply)
            return

        # Если включена автоактивность — не встреваем во время бурного обсуждения.
        if is_autoactivity(cid):
            return

        if maybe_autotell(message, STORY_AUTOTELL_CHANCE):
            return

        chance = get_chatter_chance(cid)
        if random.random() < chance:
            reply = ask_liza(text, angry=is_angry(cid), max_tokens=80)
            reply = _apply_polite_filter(cid, reply)
            bot.send_message(cid, reply)

    except Exception as e:
        logging.error(f"[text_handler] {e}", exc_info=True)
