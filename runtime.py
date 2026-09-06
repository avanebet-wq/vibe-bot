# -*- coding: utf-8 -*-
import os
import re
import time
import random
import logging
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
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


def run_dummy_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Liza is alive!")
            except Exception as e:
                logging.error(f"[DUMMY SERVER] {e}")

        def log_message(self, fmt, *args):
            pass

    port = int(os.environ.get("PORT", 8080))
    try:
        HTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except Exception as e:
        logging.error(f"[DUMMY SERVER CRASH] {e}")


threading.Thread(target=run_dummy_server, daemon=True).start()

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
ME = bot.get_me()
BOT_ID = ME.id
BOT_USERNAME = (ME.username or "").lower()

executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="liza-fast")
ai_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="liza-ai")

state_lock = threading.RLock()

# Обращение к боту: "Лиза, ..." (регистр не важен), плюс упоминание @username
WAKE_RE = re.compile(r'^\s*лиза[,\s!.:]*\s*(.*)$', re.IGNORECASE | re.DOTALL)
MENTION_PREFIXES = ("!", ".", "/")
