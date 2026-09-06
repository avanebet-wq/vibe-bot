import os

import re

import time

import random

import requests

import html

import threading

import logging

from datetime import datetime, timedelta

import telebot

from telebot import types

from telebot.types import ChatPermissions

from concurrent.futures import ThreadPoolExecutor

from http.server import BaseHTTPRequestHandler, HTTPServer

from config import TOKEN, OPENROUTER_KEY, AI_MODEL, ALLOWED_GROUPS, ALLOWED_GROUPS_RAW, DENIED_MSG, KYIV_TZ, SYS_PROMPT, SUSP, MUTES, CONFL, LOG_CHAT_ID

from database import db_get, db_set

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def run_dummy_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Bot is alive!")
            except Exception as e:
                logging.error(f"[DUMMY SERVER GET] {e}", exc_info=True)
        def log_message(self, format, *args): pass
    port = int(os.environ.get("PORT", 8080))
    try:
        HTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except Exception as e:
        logging.error(f"[DUMMY SERVER CRASH] {e}", exc_info=True)

threading.Thread(target=run_dummy_server, daemon=True).start()

api_keys = [k.strip() for k in OPENROUTER_KEY.split(",") if k.strip()]

current_key_idx = 0

key_lock = threading.Lock()

if not TOKEN or not api_keys:
    logging.critical("СЕКРЕТЫ НЕ НАЙДЕНЫ!")
    exit(1)

bot = telebot.TeleBot(TOKEN)

ME = bot.get_me()

BOT_ID, BOT_USER = ME.id, (ME.username or "").lower()

BOT_USERNAME = ME.username or ""

# Быстрые фоновые задачи не должны ждать медленные AI-запросы.
executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="vibe-fast")
ai_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="vibe-ai")

state_lock = threading.RLock()

active_safes = {}

lucky_limits = {}

active_lucky_players = set()

untrusted_warned = set()

last_command_messages = {}

messages_to_delete = []

active_fsm = {}


pending_word_lobbies = {}

active_word_games = {}

WORDS_TURN_TIMEOUT = 600

WORD_HINT_DELAY_SECONDS = 180

REGISTRATION_SECONDS = 1200

DICE_ANIMATION_SECONDS = {"🎯": 4.0, "🎳": 4.0, "🏀": 4.0}

DELETE_ANIM_DELAY = 2.0

CYRILLIC_WORD_RE = re.compile(r'^[а-яёА-ЯЁ][а-яёА-ЯЁ\- ]{1,38}[а-яёА-ЯЁ]$')

BASE_WORDS = {
    "арбуз", "база", "весна", "гроза", "дорога", "ель", "жара", "зима", "игра", "йога",
    "кот", "лес", "мама", "небо", "окно", "парк", "река", "снег", "топор", "ужин",
    "фильм", "хлеб", "цветок", "чашка", "шар", "щенок", "эхо", "юмор", "яблоко",
}

try:
    if os.path.exists("russian.txt"):
        with open("russian.txt", "r", encoding="utf-8") as f:
            for line in f:
                w = line.strip().lower()
                if w: BASE_WORDS.add(w)
except Exception as e:
    logging.error(f"[LOAD WORDS] {e}")

RANKS = [
    (0, "🌱 Новичок"), (100, "🌿 Участник"), (300, "⭐ Активист"), (600, "🔥 Завсегдатай"),
    (1000, "💎 Авторитет"), (1500, "🏅 Ветеран"), (2200, "⚡ Старожил"),
    (3200, "🧠 Знаток"), (4500, "👑 Уважаемый"), (6000, "🏆 Мастер"),
    (8000, "💫 Легенда"), (10500, "👑 Элита"), (14000, "🏆 Чемпион"),
    (18000, "💎 Ветеран чата"), (25000, "👑 Легенда чата")
]

ADMIN_RANKS = {
    1: "1 РАНГ — МЛАДШИЙ МОДЕРАТОР",
    2: "2 РАНГ — СТАРШИЙ МОДЕРАТОР",
    3: "3 РАНГ — МЛАДШИЙ АДМИНИСТРАТОР",
    4: "4 РАНГ — СТАРШИЙ АДМИНИСТРАТОР",
    5: "5 РАНГ — СОЗДАТЕЛЬ"
}

DEFAULT_RANK_PERMS = {
    1: {"can_warn": True,  "can_mute": False, "can_ban": False, "can_kick": False, "can_promote": False, "can_pin": False, "can_change_settings": False},
    2: {"can_warn": True,  "can_mute": True,  "can_ban": False, "can_kick": False, "can_promote": False, "can_pin": False, "can_change_settings": False},
    3: {"can_warn": True,  "can_mute": True,  "can_ban": True,  "can_kick": True,  "can_promote": False, "can_pin": False, "can_change_settings": False},
    4: {"can_warn": True,  "can_mute": True,  "can_ban": True,  "can_kick": True,  "can_promote": False, "can_pin": False, "can_change_settings": False},
    5: {"can_warn": True,  "can_mute": True,  "can_ban": True,  "can_kick": True,  "can_promote": True,  "can_pin": True,  "can_change_settings": True}
}

PERM_NAMES = {
    "can_warn": "варн", "can_mute": "мут", "can_ban": "бан",
    "can_kick": "кик", "can_promote": "ранги",
    "can_pin": "закреп", "can_change_settings": "настройки"
}

CMD_PERM_MAP = {
    "мут": "can_mute", "/mute": "can_mute", "снять мут": "can_mute", "/unmute": "can_mute",
    "бан": "can_ban", "/ban": "can_ban", "снятьбан": "can_ban", "/unban": "can_ban",
    "кик": "can_kick", "/kick": "can_kick",
    "пред": "can_warn", "/warn": "can_warn", "снять пред": "can_warn", "/unwarn": "can_warn",
    "повысить": "can_promote", "понизить": "can_promote", "разжаловать": "can_promote"
}

TRIGGER_DK_DEFAULT=4

TRIGGER_EVENTS={"маты":"Маты","ссылки":"Ссылки","стикеры":"Стикеры","капс":"Капс","варнлимит":"Варнлимит","дуэль":"Дуэль","кубы":"Кубы","русская рулетка":"Русская рулетка"}

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "\uFE0F"
    "]+", flags=re.UNICODE)

RANDOM_REACTION_EMOJI = "❤️‍🔥"

RANDOM_REACTION_CHANCE = 0.12
RANDOM_REACTION_COOLDOWN_SECONDS = 4.0
random_reaction_last = {}

DK_COMMANDS = {
    # Модерация
    "повысить": "повысить", "понизить": "повысить", "разжаловать": "повысить",
    "+модер": "+модер", "!модер": "+модер", "+админ": "+модер",
    "кто админ": "кто админ", "админы": "кто админ", "кто назначил": "кто назначил",
    "модер лог": "модер лог", "мой модер лог": "мой модер лог", "твой модер лог": "твой модер лог", "модер лог от": "модер лог от",
    "созвать модеров": "вызов админов", "позвать модеров": "вызов админов",
    "пред": "выдача варнов", "варны": "предупреждения пользователя", "мои варны": "мои варны", "варнлист": "предупреждения чата",
    "снять пред": "снятие варнов", "снять варн": "снятие варнов", "снять варны": "снятие варнов", "снять все варны": "снятие варнов",
    "мут": "выдача мута", "/mute": "выдача мута", "снять мут": "выдача мута", "/unmute": "выдача мута", "муты": "список мутов", "проверить мут": "проверить мут",
    "бан": "бан", "/ban": "бан", "снятьбан": "разбан", "/unban": "разбан", "банлист": "банлист", "причина": "причина бана", "амнистия": "амнистия", "кик": "кик", "/kick": "кик", "кик тихо": "кик",
    # Управление доступом
    "доступ команд": "доступ команд", "дк": "доступ команд", "/дк": "доступ команд", "!дк": "доступ команд", ".дк": "доступ команд",
    "мой доступ команд": "мой доступ команд", "мой дк": "мой доступ команд", "мдк": "мой доступ команд",
    "дк лог": "лог дк", "лог дк": "лог дк", "+дк": "доступ команд", "-дк": "доступ команд",
    "+лдк": "личный дк", "-лдк": "личный дк", "+команды": "команды", "-команды": "команды",
    "+модер теги": "модер теги", "-модер теги": "модер теги",
}

DK_DEFAULTS = {
    "повысить": 5, "+модер": 5, "кто админ": 0, "кто назначил": 1, "модер лог": 1,
    "вызов админов": 1, "выдача варнов": 1, "предупреждения пользователя": 1,
    "мои варны": 0, "предупреждения чата": 1, "снятие варнов": 2,
    "выдача мута": 2, "список мутов": 2, "проверить мут": 2,
    "бан": 3, "разбан": 3, "банлист": 3, "причина бана": 3, "амнистия": 5, "кик": 3,
    "доступ команд": 5, "мой доступ команд": 0, "лог дк": 1, "личный дк": 5,
    "команды": 0, "модер теги": 1,
}

DK_ALIASES = {
    "+модер": "+модер", "!модер": "+модер", "+админ": "+модер", "повысить": "повысить", "понизить": "повысить", "разжаловать": "повысить",
    "варн": "пред", "предупреждение": "пред", "/warn": "пред", "warn": "пред",
    "снять варн": "снятие варнов", "снять варны": "снятие варнов", "снять все варны": "снятие варнов", "снять пред": "снятие варнов", "/unwarn": "снятие варнов",
    "мут": "выдача мута", "/mute": "выдача мута", "снять мут": "выдача мута", "/unmute": "выдача мута",
    "бан": "бан", "/ban": "бан", "снятьбан": "разбан", "разбан": "разбан", "вернуть": "разбан", "/unban": "разбан",
    "банлист": "банлист", "причина": "причина бана", "кик": "кик", "/kick": "кик", "амнистия": "амнистия",
    "мои варны": "мои варны", "варны": "предупреждения пользователя", "варнлист": "предупреждения чата",
    "кто админ": "кто админ", "админы": "кто админ", "кто назначил": "кто назначил",
    "созвать модеров": "вызов админов", "позвать модеров": "вызов админов",
    "модер лог": "модер лог", "мой модер лог": "мой модер лог", "твой модер лог": "твой модер лог", "модер лог от": "модер лог от",
    "доступ команд": "доступ команд", "дк": "доступ команд", "мой доступ команд": "мой доступ команд", "мой дк": "мой доступ команд", "мдк": "мой доступ команд",
    "лог дк": "лог дк", "+дк": "доступ команд", "-дк": "доступ команд", "+лдк": "личный дк", "-лдк": "личный дк",
}

MIN_WORD_PLAYERS = 3

CLEANUP_DK_DEFAULTS = {
    "-смс": 2, "пург": 2, "кик неактив": 4, "кик актив": 4,
    "кик новичков": 4, "кик удалённых": 4, "кик молчунов": 4, "кик по смс": 4,
    "закреп": 4, "открепить": 4, "название": 5, "описание": 5,
    "правила": 4, "приветствие": 4, "чат": 5,
    "автокик": 5, "входы": 4, "выходы": 4, "входы-выходы": 4,
    "минрег": 5, "автозаявки": 5, "каналы": 5, "инлайны": 5,
    "чат ссылка": 5,
}

for _k, _v in CLEANUP_DK_DEFAULTS.items():
    DK_DEFAULTS.setdefault(_k, _v)
