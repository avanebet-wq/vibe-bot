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

from config import TOKEN, OPENROUTER_KEY, LOG_CHAT_ID, TZ
from database import db_get, db_set

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if not TOKEN:
    logging.critical("BOT_TOKEN не задан!")
    raise SystemExit(1)


bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
ME = bot.get_me()
BOT_ID = ME.id
BOT_USERNAME = (ME.username or "").lower()

# Railway health port is now used by the Telegram Mini App/API server.
from miniapp import start_miniapp_server
start_miniapp_server()

executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="liza-fast")
ai_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="liza-ai")

state_lock = threading.RLock()

# Обращение к боту: "Лиза, ..." (регистр не важен), плюс упоминание @username
WAKE_RE = re.compile(r'^\s*лиза[,\s!.:]*\s*(.*)$', re.IGNORECASE | re.DOTALL)
MENTION_PREFIXES = ("!", ".", "/")
