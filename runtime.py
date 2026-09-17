# -*- coding: utf-8 -*-
import os
import re
import time
import random
import logging
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import telebot
from telebot import types
from telebot.types import ChatPermissions

from config import TOKEN, LOG_CHAT_ID, TZ, HF_TOKEN, GROQ_KEY
from database import db_get, db_set
from reliability import mark_ok, mark_error

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if not TOKEN:
    logging.critical("BOT_TOKEN не задан!")
    raise SystemExit(1)

if not HF_TOKEN and not GROQ_KEY:
    logging.critical("Не задан ни HF_TOKEN, ни GROQ_API_KEY")
    raise SystemExit(1)

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")


def _load_bot_identity():
    delay = 1.0
    last_error = None
    for attempt in range(6):
        try:
            me = bot.get_me()
            return me
        except Exception as exc:
            last_error = exc
            logging.error("Не удалось получить данные бота при старте (попытка %s/6): %s", attempt + 1, exc)
            if attempt < 5:
                time.sleep(delay)
                delay = min(delay * 2.0, 15.0)
    raise RuntimeError(f"Telegram API недоступен при старте: {last_error}")


ME = _load_bot_identity()
BOT_ID = ME.id
BOT_USERNAME = (ME.username or "").lower()

executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="liza-fast")
ai_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="liza-ai")

state_lock = threading.RLock()

# Обращение к боту по имени — с любым из принятых обращений, по-русски и
# по-украински (регистр не важен). \b перед началом хвоста не даёт словам
# вроде "Ветеран" или "Лизать" ложно сработать как обращение к Лизе.
_WAKE_NAMES = (
    "лизонька", "лізонька",
    "лизочка", "лізочка",
    "лизушка",
    "лизуня", "лізуня",
    "лизуся", "лізуся",
    "лизуха",
    "лизка", "лізка",
    "элиза",
    "лиза", "ліза",
    "вета",
)
WAKE_RE = re.compile(
    r'^\s*(?:' + "|".join(_WAKE_NAMES) + r')\b[,\s!.:]*\s*(.*)$',
    re.IGNORECASE | re.DOTALL,
)
MENTION_PREFIXES = ("!", ".", "/")

# updated 2026-09-18
