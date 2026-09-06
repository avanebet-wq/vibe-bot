# -*- coding: utf-8 -*-
"""Настроение и активность Лизы — аналог команд Шмалалы (не спамь / активнее / отключись и т.д.)."""
import time

from runtime import bot
from config import (
    DEFAULT_CHATTER_CHANCE, MIN_CHATTER_CHANCE, MAX_CHATTER_CHANCE,
    CHATTER_STEP, SLEEP_HOURS,
)
from utils import get_setting, set_setting


def get_chatter_chance(cid):
    return get_setting(cid, "chatter_chance", DEFAULT_CHATTER_CHANCE)


def is_autoactivity(cid):
    return bool(get_setting(cid, "autoactivity", False))


def is_polite(cid):
    return bool(get_setting(cid, "polite_filter", False))


def is_angry(cid):
    return bool(get_setting(cid, "angry_mode", False))


def sleep_until(cid):
    return get_setting(cid, "sleep_until", 0) or 0


def is_asleep(cid):
    return time.time() < sleep_until(cid)


# ------------------------------------------------------------ команды ----

def cmd_less_spam(message):
    cid = message.chat.id
    val = max(MIN_CHATTER_CHANCE, get_chatter_chance(cid) - CHATTER_STEP)
    set_setting(cid, "chatter_chance", val)
    bot.reply_to(message, "🤫 Хорошо, буду вставлять свои пять копеек реже.")


def cmd_more_active(message):
    cid = message.chat.id
    val = min(MAX_CHATTER_CHANCE, get_chatter_chance(cid) + CHATTER_STEP)
    set_setting(cid, "chatter_chance", val)
    bot.reply_to(message, "😼 Ладно, буду чаще встревать в разговор.")


def cmd_autoactivity_on(message):
    set_setting(message.chat.id, "autoactivity", True)
    bot.reply_to(
        message,
        "🧠 Включила автоактивность — если в чате бурное обсуждение, "
        "я не буду лезть с комментариями.",
    )


def cmd_autoactivity_off(message):
    set_setting(message.chat.id, "autoactivity", False)
    bot.reply_to(message, "🙃 Автоактивность выключена.")


def cmd_sleep(message):
    cid = message.chat.id
    until = time.time() + SLEEP_HOURS * 3600
    set_setting(cid, "sleep_until", until)
    bot.reply_to(message, f"😴 Ухожу в отключку на {SLEEP_HOURS} часов. Разбудите командой «Лиза, включись».")


def cmd_wakeup(message):
    set_setting(message.chat.id, "sleep_until", 0)
    bot.reply_to(message, "🙋 Я снова на связи!")


def cmd_polite_on(message):
    set_setting(message.chat.id, "polite_filter", True)
    bot.reply_to(message, "🎩 Хорошо, буду вести себя культурно.")


def cmd_polite_off(message):
    set_setting(message.chat.id, "polite_filter", False)
    bot.reply_to(message, "😏 Ладно, фильтр выключен, буду говорить как обычно.")


def cmd_angry_on(message):
    set_setting(message.chat.id, "angry_mode", True)
    bot.reply_to(message, "😠 Всё, я в бешенстве. Осторожнее со мной.")


def cmd_calm_down(message):
    set_setting(message.chat.id, "angry_mode", False)
    bot.reply_to(message, "😌 Ладно, выдохнула. Успокоилась.")
