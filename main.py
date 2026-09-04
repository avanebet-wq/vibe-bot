import os
import re
import time
import random
import requests
import html
import threading
import logging
from datetime import datetime
import telebot
from telebot import types
from telebot.types import ChatPermissions
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import TOKEN, OPENROUTER_KEY, BOSSES, AI_MODEL, ALLOWED_GROUPS, ALLOWED_GROUPS_RAW, DENIED_MSG, KYIV_TZ, SYS_PROMPT, SUSP, MUTES, CONFL
from database import db_get, db_set

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- ЗАГЛУШКА ДЛЯ RAILWAY ---
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
executor = ThreadPoolExecutor(max_workers=10)

# --- ПОТОКОБЕЗОПАСНЫЕ СОСТОЯНИЯ (State Lock) ---
state_lock = threading.RLock()
active_safes = {}
lucky_limits = {}
active_lucky_players = set()
untrusted_warned = set()
last_command_messages = {}
messages_to_delete = []

# Унифицированное FSM: uid -> {"action": str, "pid": str}
active_fsm = {}

# --- СОСТОЯНИЯ ИГРЫ «ГОРОДА» ---
active_cities_games = {}  # cid -> {"last_city","next_letter","used","last_move_ts","moves"}
CITIES_TIMEOUT = 180

CYRILLIC_CITY_RE = re.compile(r'^[а-яёА-ЯЁ][а-яёА-ЯЁ\- ]{1,38}[а-яёА-ЯЁ]$')

_nominatim_lock = threading.Lock()
_last_nominatim_call = [0.0]

BASE_CITIES = {
    "москва","санкт-петербург","новосибирск","екатеринбург","казань","нижний новгород",
    "челябинск","самара","омск","ростов-на-дону","уфа","красноярск","воронеж","пермь",
    "волгоград","краснодар","саратов","тюмень","тольятти","ижевск","барнаул","ульяновск",
    "иркутск","хабаровск","ярославль","владивосток","махачкала","томск","оренбург",
    "кемерово","новокузнецк","рязань","астрахань","пенза","липецк","киров","чебоксары",
    "тула","калининград","курск","ставрополь","сочи","белгород","владимир","симферополь",
    "севастополь","мурманск","архангельск","якутск","грозный","орел","смоленск","тверь",
    "магнитогорск","сургут","подольск","псков","брянск","череповец","чита","вологда",
    "калуга","саранск","абакан","южно-сахалинск","петропавловск-камчатский",
    "киев","харьков","одесса","днепр","донецк","запорожье","львов","кривой рог",
    "николаев","мариуполь","луганск","винница","херсон","полтава","чернигов","черкассы",
    "житомир","сумы","хмельницкий","ровно","ужгород","луцк","тернополь","ивано-франковск",
    "минск","гомель","могилев","витебск","гродно","брест","бобруйск","барановичи",
    "алматы","астана","шымкент","караганда","актобе","тараз","павлодар","семей",
    "ташкент","бишкек","душанбе","ашхабад","ереван","тбилиси","баку","кишинев",
    "вильнюс","рига","таллин","хельсинки","стокгольм","осло","копенгаген",
    "берлин","мюнхен","гамбург","кельн","франкфурт","дрезден","лейпциг",
    "париж","марсель","лион","ницца","лондон","манчестер","ливерпуль","дублин",
    "мадрид","барселона","валенсия","севилья","лиссабон","порту","рим","милан",
    "неаполь","венеция","турин","вена","зальцбург","прага","брно","братислава",
    "варшава","краков","гданьск","будапешт","бухарест","софия","белград","загреб",
    "афины","стамбул","анкара","каир","тунис","алжир","касабланка","найроби",
    "дели","мумбаи","пекин","шанхай","токио","осака","сеул","бангкок","ханой",
    "джакарта","манила","сингапур","нью-йорк","вашингтон","чикаго","бостон",
    "лос-анджелес","сан-франциско","майами","торонто","монреаль","оттава",
    "мехико","буэнос-айрес","сантьяго","лима","богота","каракас","гавана",
    "сидней","мельбурн","окленд",
}

# --- УТИЛИТЫ И БЕЗОПАСНОСТЬ ---
def get_v(cid, k, d=40):
    with state_lock:
        return db_get("settings", {}).get(str(cid), {}).get(k, d)

def set_v(cid, k, val):
    with state_lock:
        s = db_get("settings", {})
        s.setdefault(str(cid), {"freq": 40, "anger": 40, "intervene": True})[k] = val
        db_set("settings", s)

def get_user_mention(user_obj=None, user_id=None, first_name=None):
    if user_obj: user_id, first_name = user_obj.id, user_obj.first_name
    safe_name = html.escape(str(first_name or "User"))
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>' if user_id else safe_name

def format_safe_leaderboard():
    with state_lock:
        ldrs = db_get("safe_leaders", {})
        if not ldrs: return "🏆 Рейтинг взломщиков сейфа пока пуст."
        txt = "🏆 Рейтинг взломщиков сейфа VIBE\n\n"
        sorted_ldrs = sorted(ldrs.items(), key=lambda x: x[1].get("wins", 0), reverse=True)[:10]
        for i, (uid_str, uinfo) in enumerate(sorted_ldrs, 1):
            m_icon = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else "▫️"
            txt += f"{m_icon} {i}. {get_user_mention(user_id=int(uid_str), first_name=uinfo.get('name'))} — <b>{uinfo.get('wins', 0)}</b> побед\n"
        return txt.strip()

def format_cities_leaderboard():
    with state_lock:
        ldrs = db_get("cities_leaders", {})
        if not ldrs: return "🏆 Рейтинг игры «Города» пока пуст."
        txt = "🏙 Рейтинг игры «Города»\n\n"
        for i, (uid_str, uinfo) in enumerate(sorted(ldrs.items(), key=lambda x: x[1].get("wins", 0), reverse=True)[:10], 1):
            m_icon = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else "▫️"
            txt += f"{m_icon} {i}. {get_user_mention(user_id=int(uid_str), first_name=uinfo.get('name'))} — <b>{uinfo.get('wins', 0)}</b> городов\n"
        return txt.strip()

def parse_interval_input(text):
    match = re.match(r'^(\d+)([дчм])$', text.lower().strip())
    if not match: return None
    val, unit = int(match.group(1)), match.group(2)
    return val * 60 if unit == 'м' else (val * 3600 if unit == 'ч' else val * 86400)

def track_and_replace_specific_cmd(chat_id, user_id, cmd_name, new_msg):
    if not new_msg: return
    with state_lock:
        key = (chat_id, user_id, cmd_name)
        if key in last_command_messages:
            try: bot.delete_message(chat_id, last_command_messages[key])
            except Exception as e: logging.error(f"[DEL OLD CMD] {e}")
        last_command_messages[key] = new_msg.message_id

def register_chat(chat):
    if chat.type in ['group', 'supergroup', 'channel']:
        with state_lock:
            cache = db_get("chats_cache", {"-1004374303475": "Основная VIBE", "-1003514059820": "Вторая группа"})
            cid_str = str(chat.id)
            cname = chat.title or f"Чат {cid_str}"
            if cache.get(cid_str) != cname:
                cache[cid_str] = cname
                db_set("chats_cache", cache)

def check_access(m):
    uid = m.from_user.id if m.from_user else 0
    if m.chat.type == 'private':
        if uid not in BOSSES:
            try: bot.reply_to(m, DENIED_MSG, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔗 Разработчик", url="https://t.me/VER_CIDE")), parse_mode='HTML')
            except Exception as e: logging.error(f"[ACCESS ERROR] {e}")
            return False
        return True
    register_chat(m.chat)
    if str(m.chat.id).replace("-100", "").replace("-", "") not in ALLOWED_GROUPS_RAW and m.chat.id not in ALLOWED_GROUPS:
        try: bot.reply_to(m, DENIED_MSG, parse_mode='HTML')
        except Exception as e: logging.error(f"[ACCESS ERROR] {e}")
        return False
    return True

def auto_del(msg, ttl=180):
    if msg:
        with state_lock:
            messages_to_delete.append({"cid": msg.chat.id, "mid": msg.message_id, "time": time.time() + ttl})

def cleanup_worker():
    while True:
        try:
            time.sleep(5)
            now = time.time()
            with state_lock:
                remaining = []
                for item in messages_to_delete:
                    if now >= item["time"]:
                        try: bot.delete_message(item["cid"], item["mid"])
                        except Exception as e: logging.error(f"[CLEANUP DEL] {e}")
                    else:
                        remaining.append(item)
                messages_to_delete[:] = remaining
        except Exception as e:
            logging.error(f"[CLEANUP WORKER] {e}", exc_info=True)
threading.Thread(target=cleanup_worker, daemon=True).start()

def cities_timeout_worker():
    while True:
        try:
            time.sleep(20)
            now, to_close = time.time(), []
            with state_lock:
                for cid, game in list(active_cities_games.items()):
                    if now - game["last_move_ts"] > CITIES_TIMEOUT:
                        to_close.append((cid, game))
                        del active_cities_games[cid]
            for cid, game in to_close:
                try:
                    bot.send_message(cid, f"⌛ Игра «Города» окончена по тайм-ауту.\nНазвано: {game['moves']}. /start_cities_game — начать заново")
                except Exception as e: logging.error(f"[CITIES TIMEOUT] {e}")
        except Exception as e: logging.error(f"[CITIES WORKER] {e}", exc_info=True)
threading.Thread(target=cities_timeout_worker, daemon=True).start()

# --- ХЕЛПЕРЫ ДЛЯ ГОРОДОВ ---
def normalize_city(name):
    n = name.strip().lower().replace("ё", "е")
    n = re.sub(r'\s*-\s*', '-', n)
    return re.sub(r'\s+', ' ', n)

def first_letter(name):
    n = normalize_city(name)
    return n[0] if n else None

def effective_last_letter(name):
    n = normalize_city(name)
    idx = len(n) - 1
    while idx >= 0 and n[idx] in "ьъ":
        idx -= 1
    return n[idx] if idx >= 0 else None

def is_known_city(norm_name):
    if norm_name in BASE_CITIES:
        return True
    with state_lock:
        return norm_name in db_get("known_cities_extra", [])

def learn_city(norm_name):
    with state_lock:
        extra = db_get("known_cities_extra", [])
        if norm_name not in extra:
            extra.append(norm_name)
            db_set("known_cities_extra", extra)

def validate_city_via_nominatim(raw_name):
    try:
        with _nominatim_lock:
            wait = 1.1 - (time.time() - _last_nominatim_call[0])
            if wait > 0: time.sleep(wait)
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": raw_name, "format": "json", "limit": 3, "accept-language": "ru"},
                headers={"User-Agent": "VibeCitiesGameBot/1.0"},
                timeout=8,
            )
            _last_nominatim_call[0] = time.time()
        for item in r.json():
            if item.get("class") in ("place", "boundary") and item.get("type") in \
               ("city", "town", "village", "administrative", "hamlet"):
                return True
        return False
    except Exception as e:
        logging.error(f"[NOMINATIM] {e}")
        return None

def ai_check_is_city(raw_name):
    ans = call_ai([{"role": "user", "content": f"Слово: '{raw_name}'. Это реально существующий населённый пункт в любой стране? Отвечай строго: ДА или НЕТ."}], 10, 0.0)
    return "ДА" in ans.upper()

def resolve_is_city(raw_name, norm_name):
    if is_known_city(norm_name):
        return True
    verdict = validate_city_via_nominatim(raw_name)
    is_city = verdict if verdict is not None else ai_check_is_city(raw_name)
    if is_city:
        learn_city(norm_name)
    return is_city

# --- AI ЛОГИКА ---
def clean_ai_response(content):
    if not content: return "Та ну... мысль потерялась, спроси по-другому..."
    content = re.sub(r"(?si)^.*?thinking process.*?(?:output|option \d+:|final response:|answer:|draft generation:?)\s*", "", content)
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    clean_str = re.sub(r"(?i)^option \d+:\s*", "", content.strip())
    if re.fullmatch(r"[\d\.\-\sE]+", clean_str) and len(clean_str) > 3: return "Что-то цифры одни... давай о чём-то другом."
    if re.search(r"(i cannot|i can't|as an ai|sorry|unable to fulfill|error|я искусственный интеллект)", clean_str, re.IGNORECASE): 
        return "Не, об этом говорить не буду..."
    clean_str = re.sub(r"^(Лиза|Lisa|Ліза):\s*", "", clean_str, flags=re.IGNORECASE).strip()
    res = clean_str + "." if clean_str and clean_str[-1].isalnum() else clean_str or "Мда..."
    return html.escape(res)

def get_current_key():
    with key_lock:
        return api_keys[current_key_idx]

def switch_key():
    global current_key_idx
    with key_lock:
        current_key_idx = (current_key_idx + 1) % len(api_keys)
        return api_keys[current_key_idx]

def call_ai(messages, max_tokens=300, temp=0.5):
    attempts = 0
    max_attempts = len(api_keys)
    
    while attempts < max_attempts:
        current_key = get_current_key()
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {current_key}"}, json={"model": AI_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temp}, timeout=20)
            
            try:
                data = r.json()
            except Exception:
                data = {}

            key_is_bad = (
                r.status_code in (401, 402, 429)
                or (isinstance(data.get("error"), dict) and data["error"].get("code") in (401, 402, 429))
            )

            if key_is_bad:
                logging.warning(f"Лимит ключа исчерпан или невалиден (status={r.status_code}). Переключаю на следующий...")
                switch_key()
                attempts += 1
                continue
                
            if "choices" in data and data["choices"]: 
                return clean_ai_response(data["choices"][0].get("message", {}).get("content", ""))
            else:
                logging.error(f"[AI API ERROR RESPONSE]: {data} | Status: {r.status_code}")
                break
        except Exception as e: 
            logging.error(f"[AI Exception]: {e}", exc_info=True)
            switch_key()
            attempts += 1
            continue
            
    return "Сервис временно занят."

def is_threat(txt):
    try:
        ans = call_ai([{"role": "user", "content": f"Текст: '{txt}'. Это угроза докса/сватинга? Отвечай THREAT или SAFE."}], 10, 0.0)
        return "THREAT" in ans.upper()
    except Exception as e:
        logging.error(f"[THREAT CHECK] {e}")
        return False

# --- КЛАВИАТУРЫ ---
def main_kb(cid, is_pv=False):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(f"⚡ Вмешательство: {'Вкл' if get_v(cid, 'intervene', True) else 'Выкл'}", callback_data="m:toggle_intervene"),
        types.InlineKeyboardButton(f"📊 Частота: {get_v(cid, 'freq')}%", callback_data="m:freq"),
        types.InlineKeyboardButton(f"🔥 Строгость: {get_v(cid, 'anger')}%", callback_data="m:anger")
    )
    kb.add(types.InlineKeyboardButton("📢 Автопостинг", callback_data="m:autopost_list") if is_pv else types.InlineKeyboardButton("⚙️ Настройки группы", callback_data="to_group_settings"))
    if is_pv: kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_start"))
    return kb

def chats_selection_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for cid, cname in db_get("chats_cache", {}).items(): kb.add(types.InlineKeyboardButton(f"📢 {cname}", callback_data=f"ap:chat:{cid}"))
    return kb.add(types.InlineKeyboardButton("« Назад", callback_data="open_main_settings"))

def autopost_list_kb(cid):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for p in [p for p in db_get("autopost", {"posts": []}).get("posts", []) if str(p.get("chat_id")) == str(cid)]:
        kb.add(types.InlineKeyboardButton(f"{'✅' if p.get('enabled') else '❌'} {html.escape(p.get('name', 'Пост'))}", callback_data=f"ap:select:{p['id']}"))
    kb.add(types.InlineKeyboardButton("➕ Создать", callback_data=f"ap:create:{cid}"), types.InlineKeyboardButton("🗑 Удалить", callback_data=f"ap:delmenu:{cid}"))
    return kb.add(types.InlineKeyboardButton("« Назад", callback_data="m:autopost_list"))

def build_post_user_kb(post):
    rows = post.get("buttons", [])
    if not rows: return None
    kb = types.InlineKeyboardMarkup()
    for row in rows:
        btns = []
        for b in row:
            if b.get("url"): btns.append(types.InlineKeyboardButton(b["text"], url=b["url"]))
            elif b.get("command"): btns.append(types.InlineKeyboardButton(b["text"], callback_data=f"cmd_exec_{post['id']}_{b['command']}"))
            elif b.get("callback_data"): btns.append(types.InlineKeyboardButton(b["text"], callback_data=b["callback_data"]))
        if btns: kb.row(*btns)
    return kb

def post_settings_kb(pid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    post = next((p for p in db_get("autopost", {"posts": []}).get("posts", []) if p["id"] == pid), None)
    if not post: return kb
    sec = post.get("interval", 3600)
    istr = "Выкл" if sec == 0 else (f"{sec//60}м" if sec<3600 else f"{sec//3600}ч")
    kb.add(types.InlineKeyboardButton(f"💡 Статус: {'Вкл' if post.get('enabled') else 'Выкл'}", callback_data=f"ap:toggle:{pid}"), types.InlineKeyboardButton(f"⏱ Интервал: {istr}", callback_data=f"ap:int_menu:{pid}"))
    kb.add(types.InlineKeyboardButton(f"🕑 Время: {post.get('daily_time') or 'Выкл'}", callback_data=f"ap:time_menu:{pid}"), types.InlineKeyboardButton(f"📅 Дата: {post.get('start_date') or 'Сегодня'}", callback_data=f"ap:date_menu:{pid}"))
    kb.add(types.InlineKeyboardButton(f"♻️ Удаление: {'Вкл' if post.get('auto_delete_prev') else 'Выкл'}", callback_data=f"ap:autodel:{pid}"), types.InlineKeyboardButton(f"🖼 Фото: {'Есть' if post.get('photo') else 'Нет'}", callback_data=f"ap:photo:{pid}"))
    kb.add(types.InlineKeyboardButton("📝 Текст", callback_data=f"ap:text:{pid}"), types.InlineKeyboardButton(f"🔘 Кнопки ({sum(len(r) for r in post.get('buttons',[]))})", callback_data=f"ap:btns:{pid}"))
    kb.add(types.InlineKeyboardButton("👁 Предпросмотр", callback_data=f"ap:preview:{pid}"), types.InlineKeyboardButton("🚀 Отправить", callback_data=f"ap:send:{pid}"))
    return kb.add(types.InlineKeyboardButton("« К постам", callback_data=f"ap:chat:{post.get('chat_id', '')}"))

def post_text_view(pid):
    post = next((p for p in db_get("autopost", {"posts": []}).get("posts", []) if p["id"] == pid), None)
    if not post: return "⚠️ Пост не найден."
    cname = db_get("chats_cache", {}).get(str(post.get("chat_id")), str(post.get("chat_id")))
    dt = post.get("daily_time")
    time_str = f"Ежедневно в {dt}" if dt else "По интервалу"
    rep_str = "Ежедневно" if dt else ("Выключено" if post.get("interval",3600)==0 else f"Каждые {post['interval']//60}м")
    return f"🕑 Пост\n💡 Статус: {'Вкл' if post.get('enabled') else 'Выкл'}\n📢 Чат: {html.escape(str(cname))}\n🕑 Время: {time_str}\n🔁 Повтор: {rep_str}"

def send_specific_post(chat_id, post):
    try:
        mk = build_post_user_kb(post)
        txt = post.get("text", "")
        if post.get("photo"):
            msg = bot.send_photo(chat_id, post["photo"], caption=txt, reply_markup=mk, parse_mode='HTML')
        else:
            msg = bot.send_message(chat_id, txt, reply_markup=mk, parse_mode='HTML')
        
        old_msg_id = None
        with state_lock:
            if post.get("auto_delete_prev") and post.get("last_msg_id"):
                old_msg_id = post["last_msg_id"]
            post["last_msg_id"] = msg.message_id
            
        if old_msg_id:
            try: bot.delete_message(chat_id, old_msg_id)
            except Exception as e: logging.error(f"[AUTOPOST DEL PREV] {e}")
    except Exception as e: logging.error(f"[AUTOPOST SEND] {e}", exc_info=True)

def autopost_worker():
    while True:
        try:
            time.sleep(15)
            now_ts = datetime.now(KYIV_TZ).timestamp()
            td_str, curr_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d"), datetime.now(KYIV_TZ).strftime("%H:%M")
            to_send = []
            with state_lock:
                data = db_get("autopost", {"posts": []})
                updated = False
                for p in data.get("posts", []):
                    if not p.get("enabled") or (p.get("start_date") and td_str < p["start_date"]): continue
                    dt = p.get("daily_time")
                    if dt:
                        if curr_str >= dt and p.get("last_sent_date") != td_str:
                            p["last_post"], p["last_sent_date"], updated = now_ts, td_str, True
                            to_send.append((p.get("chat_id"), p))
                    elif p.get("interval", 0) > 0 and now_ts - p.get("last_post", 0) >= p["interval"]:
                        p["last_post"], updated = now_ts, True
                        to_send.append((p.get("chat_id"), p))
                if updated: db_set("autopost", data)
            
            for chat_id, p in to_send:
                send_specific_post(chat_id, p)
            
            if to_send:
                with state_lock:
                    fresh = db_get("autopost", {"posts": []})
                    by_id = {item["id"]: item for item in fresh.get("posts", [])}
                    for _, sent_post in to_send:
                        target = by_id.get(sent_post["id"])
                        if target:
                            target["last_msg_id"] = sent_post.get("last_msg_id")
                            target["last_post"] = sent_post.get("last_post")
                            if "last_sent_date" in sent_post:
                                target["last_sent_date"] = sent_post["last_sent_date"]
                    db_set("autopost", fresh)
        except Exception as e: logging.error(f"[WORKER] {e}", exc_info=True)
threading.Thread(target=autopost_worker, daemon=True).start()

def process_city_guess(cid, uid, fname, raw_text):
    try:
        raw = raw_text.strip()
        norm = normalize_city(raw)
        with state_lock:
            game = active_cities_games.get(cid)
        if not game: return

        f_letter, req_letter = first_letter(raw), game["next_letter"]
        known_locally = is_known_city(norm)

        if req_letter and f_letter != req_letter:
            if not known_locally: return
            return bot.send_message(cid, f"❌ Нужна буква «{req_letter.upper()}», а не «{f_letter.upper()}».")

        with state_lock:
            if norm in game["used"]:
                return bot.send_message(cid, f"♻️ «{html.escape(raw)}» уже называли.")

        if not resolve_is_city(raw, norm):
            if req_letter:
                return bot.send_message(cid, f"🤔 Не нахожу такой город. Ещё раз на букву «{req_letter.upper()}».")
            return

        eff_letter = effective_last_letter(raw)
        with state_lock:
            game = active_cities_games.get(cid)
            if not game or (game["next_letter"] and f_letter != game["next_letter"]) or norm in game["used"]:
                return 
            game["used"].add(norm)
            game["last_city"], game["next_letter"] = raw, eff_letter
            game["last_move_ts"], game["moves"] = time.time(), game["moves"] + 1
            ldrs = db_get("cities_leaders", {})
            ldrs.setdefault(str(uid), {"name": fname, "wins": 0})["wins"] += 1
            db_set("cities_leaders", ldrs)

        mention = get_user_mention(user_id=uid, first_name=fname)
        bot.send_message(cid, f"✅ {mention}: <b>{html.escape(raw)}</b>\nСледующий город на букву «{eff_letter.upper()}»", parse_mode='HTML')
    except Exception as e:
        logging.error(f"[CITIES MOVE] {e}", exc_info=True)

def lucky_game_result(cid, uid, fname, msg_id, win, left):
    try:
        time.sleep(7)
        try: bot.delete_message(cid, msg_id)
        except Exception as e: logging.error(f"[LUCKY DEL] {e}")
        mention = get_user_mention(user_id=uid, first_name=fname)
        if win:
            with state_lock:
                ldrs = db_get("lucky_leaders", {})
                u = ldrs.setdefault(str(uid), {"name": fname, "wins": 0})
                u["wins"] += 1
                db_set("lucky_leaders", ldrs)
                rank = sum(1 for v in ldrs.values() if v.get("wins",0) > u["wins"]) + 1
            txt = f"🎉 {mention}, невероятно! Ты выиграл +1 балл!\nТвое место: #{rank}\n"
        else:
            txt = f"😔 {mention}, не повезло.\n"
        
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🎲 Снова", callback_data=f"lucky:again:{uid}")) if left > 0 else None
        if left == 0:
            with state_lock:
                rem = int(lucky_limits[uid]["reset_at"] - time.time())
            txt += f"\nПопыток больше нет😔\nНовые через: {max(0, rem//60)}м {max(0, rem%60)}с.\n\nЗато в боте играй без ограничений😉\n🔥 @vibe_247top_bot"
        else:
            txt += f"Осталось попыток: {left}\nСыграем еще?"
        track_and_replace_specific_cmd(cid, uid, "lucky_game", bot.send_message(cid, txt, reply_markup=kb, parse_mode='HTML'))
    except Exception as e:
        logging.error(f"[LUCKY RESULT] {e}", exc_info=True)
    finally:
        with state_lock:
            if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))

def play_lucky_game(cid, uid, fname):
    try:
        with state_lock:
            lim = lucky_limits.setdefault(uid, {"left": 5, "reset_at": 0})
            now = time.time()
            if lim["left"] <= 0:
                if now < lim["reset_at"]:
                    rem = int(lim["reset_at"] - now)
                    txt = f"{get_user_mention(user_id=uid, first_name=fname)},\nПопыток нет😔\nНовые через: {rem//60}м {rem%60}с.\n\nЗато в боте без ограничений😉\n🔥 @vibe_247top_bot"
                    track_and_replace_specific_cmd(cid, uid, "lucky_game", bot.send_message(cid, txt, parse_mode='HTML', disable_web_page_preview=True))
                    if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))
                    return
                else: lim["left"] = 5
            lim["left"] -= 1
            if lim["left"] == 0: lim["reset_at"] = now + 1800
        
        emoji = random.choice(["🎯", "🎳", "🏀"])
        try: dice = bot.send_dice(cid, emoji=emoji)
        except Exception as e:
            bot.send_message(cid, "⚠️ Нет прав на кубики.")
            with state_lock:
                if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))
            return
        win = (emoji in ["🎯", "🎳"] and dice.dice.value == 6) or (emoji == "🏀" and dice.dice.value in [4, 5])
        executor.submit(lucky_game_result, cid, uid, fname, dice.message_id, win, lim["left"])
    except Exception as e:
        logging.error(f"[PLAY LUCKY] {e}", exc_info=True)
        with state_lock:
            if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))

@bot.message_handler(commands=['start'])
def cmd_start(m):
    if not check_access(m): return
    if m.chat.type == 'private' and m.from_user.id in BOSSES and m.text and "settings" in m.text:
        return bot.reply_to(m, "📢 Выберите группу:", reply_markup=chats_selection_kb())
    kb = types.InlineKeyboardMarkup(row_width=1).add(types.InlineKeyboardButton("Что ты умеешь?", callback_data="what_can_i_do"))
    if m.chat.type == 'private' and m.from_user.id in BOSSES: kb.add(types.InlineKeyboardButton("⚙️ Настройки", callback_data="open_main_settings"))
    
    txt = f"Привет, {get_user_mention(m.from_user)}... Я Лиза...\n💭 Чат: https://t.me/+8WZ4kwpAaZ0yZTRi\n🛒 Наш бот: @vibe_247top_bot\n🎁 События: /events"
    msg = bot.send_message(m.chat.id, txt, reply_markup=kb, parse_mode='HTML', disable_web_page_preview=True)
    if m.chat.type != 'private': auto_del(msg, 180)

@bot.message_handler(commands=['setting', 'settings'])
def cmd_settings(m):
    if not check_access(m): return
    if m.from_user.id not in BOSSES: return auto_del(bot.send_message(m.chat.id, "⛔ Только для руководства.", parse_mode='HTML'))
    track_and_replace_specific_cmd(m.chat.id, m.from_user.id, "settings", bot.send_message(m.chat.id, "🎛 Панель управления:", reply_markup=main_kb(m.chat.id, m.chat.type == 'private'), parse_mode='HTML'))

@bot.message_handler(commands=['lucky_game'])
def cmd_lg(m):
    if not check_access(m): return
    uid, cid = m.from_user.id, m.chat.id
    try: bot.delete_message(cid, m.message_id)
    except Exception as e: logging.error(f"[LG DEL] {e}")
    with state_lock:
        if (cid, uid) in active_lucky_players:
            return track_and_replace_specific_cmd(cid, uid, "lucky_game", bot.send_message(cid, "Дождись конца игры!"))
        active_lucky_players.add((cid, uid))
    play_lucky_game(cid, uid, m.from_user.first_name)

@bot.message_handler(commands=['leaders_lucky_game'])
def cmd_llg(m):
    if not check_access(m): return
    try: bot.delete_message(m.chat.id, m.message_id)
    except Exception as e: logging.error(f"[LLG DEL] {e}")
    with state_lock:
        ldrs = db_get("lucky_leaders", {})
    if not ldrs: txt = "🏆 Рейтинг пуст."
    else: txt = "🏆 Рейтинг везунчиков\n\n" + "\n".join(f"{i}. {get_user_mention(user_id=int(u), first_name=info.get('name'))} — <b>{info.get('wins',0)}</b>" for i, (u, info) in enumerate(sorted(ldrs.items(), key=lambda x: x[1].get("wins",0), reverse=True)[:10], 1))
    track_and_replace_specific_cmd(m.chat.id, m.from_user.id, "leaders_lucky_game", bot.send_message(m.chat.id, txt, parse_mode='HTML'))

@bot.message_handler(commands=['events'])
def cmd_events(m):
    if not check_access(m): return
    evs = db_get("events", [])
    evs = evs["events"] if isinstance(evs, dict) and "events" in evs else evs
    auto_del(bot.send_message(m.chat.id, "Пока пусто!" if not evs else "Планируется:\n" + "\n".join(f"🔸 {html.escape(str(e.get('date')))} — {html.escape(str(e.get('info')))}" for e in evs), parse_mode='HTML'), 180)

@bot.message_handler(commands=['start_game_safe'])
def cmd_sgs(m):
    if not check_access(m) or m.from_user.id not in BOSSES: return
    with state_lock:
        if m.chat.id in active_safes: return bot.send_message(m.chat.id, "⚠️ Сейф уже активирован.")
        active_safes[m.chat.id] = {"code": f"{random.randint(0,999):03d}", "hint_given": False, "gid": time.time()}
    track_and_replace_specific_cmd(m.chat.id, m.from_user.id, "start_game_safe", bot.send_message(m.chat.id, "🔐 ИГРА НАЧАЛАСЬ\nБронированный сейф заблокирован на ХХХ.\nПишите код в чат.\nТоп: /leaders_safe_game"))

@bot.message_handler(commands=['leaders_safe_game'])
def cmd_lsg(m):
    if not check_access(m): return
    track_and_replace_specific_cmd(m.chat.id, m.from_user.id, "leaders_safe_game", bot.send_message(m.chat.id, format_safe_leaderboard(), parse_mode='HTML'))

@bot.message_handler(commands=['start_cities_game'])
def cmd_start_cities(m):
    if not check_access(m) or m.from_user.id not in BOSSES: return
    cid = m.chat.id
    with state_lock:
        if cid in active_cities_games:
            return bot.reply_to(m, "⚠️ Игра «Города» уже идёт.")
    parts = m.text.split(maxsplit=1)
    seed = parts[1].strip() if len(parts) > 1 else None
    if seed:
        if not CYRILLIC_CITY_RE.match(seed):
            return bot.reply_to(m, "⚠️ Пример: /start_cities_game Москва")
        if not resolve_is_city(seed, normalize_city(seed)):
            return bot.reply_to(m, f"⚠️ «{html.escape(seed)}» не похож на существующий город.")
    else:
        seed = random.choice(list(BASE_CITIES)).title()
    eff = effective_last_letter(seed)
    with state_lock:
        active_cities_games[cid] = {"last_city": seed, "next_letter": eff, "used": {normalize_city(seed)}, "last_move_ts": time.time(), "moves": 0}
    bot.send_message(cid, f"🏙 ИГРА «ГОРОДА»!\nПервый город: <b>{html.escape(seed)}</b>\nСледующий на букву «{eff.upper()}»\n⏱ Тайм-аут хода: 3 мин\n/stop_cities_game · /leaders_cities_game", parse_mode='HTML')

@bot.message_handler(commands=['stop_cities_game'])
def cmd_stop_cities(m):
    if not check_access(m) or m.from_user.id not in BOSSES: return
    with state_lock:
        game = active_cities_games.pop(m.chat.id, None)
    if not game: return bot.reply_to(m, "Игра сейчас не идёт.")
    bot.send_message(m.chat.id, f"🛑 Остановлено. Названо городов: {game['moves']}. Последний: {html.escape(game['last_city'] or '—')}")

@bot.message_handler(commands=['leaders_cities_game'])
def cmd_leaders_cities(m):
    if not check_access(m): return
    track_and_replace_specific_cmd(m.chat.id, m.from_user.id, "leaders_cities_game", bot.send_message(m.chat.id, format_cities_leaderboard(), parse_mode='HTML'))

@bot.message_handler(commands=['cities_status'])
def cmd_cities_status(m):
    if not check_access(m): return
    with state_lock:
        game = active_cities_games.get(m.chat.id)
    if not game: return bot.reply_to(m, "Игра не идёт. /start_cities_game")
    bot.reply_to(m, f"🏙 Названо: {game['moves']}. Нужна буква «{game['next_letter'].upper()}».")

@bot.callback_query_handler(func=lambda c: True)
def cb_handler(c):
    try:
        cid, uid, d = c.message.chat.id, c.from_user.id, c.data
        is_pv = c.message.chat.type == 'private'
        parts = d.split(":")
        action = parts[0]

        if d.startswith("cmd_exec_"):
            cmd = d.split("_", 2)[2]
            if cmd.startswith("/"):
                fake = c.message
                fake.text, fake.from_user, fake.is_callback = cmd, c.from_user, True
                if cmd == "/lucky_game": cmd_lg(fake)
                elif cmd == "/leaders_lucky_game": cmd_llg(fake)
            return bot.answer_callback_query(c.id)

        if not is_pv and str(cid).replace("-100", "") not in ALLOWED_GROUPS_RAW and cid not in ALLOWED_GROUPS:
            return bot.answer_callback_query(c.id, "⛔ Неразрешенный чат.", show_alert=True)

        if action == "lucky" and parts[1] == "again":
            target_uid = int(parts[2])
            if uid != target_uid: return bot.answer_callback_query(c.id, "⛔ Не твоя игра!", show_alert=True)
            try: bot.delete_message(cid, c.message.message_id)
            except Exception as e: logging.error(f"[LUCKY AGAIN DEL] {e}")
            bot.answer_callback_query(c.id)
            return play_lucky_game(cid, uid, c.from_user.first_name)

        if d == "what_can_i_do":
            bot.answer_callback_query(c.id)
            return bot.edit_message_text("🔹 Общаюсь\n🔹 Слежу за матом\n🔹 Автопостинг\n🔹 Игры (/lucky_game, сейф, /start_cities_game)", cid, c.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data="back_to_start")))
            
        if d == "back_to_start":
            bot.answer_callback_query(c.id)
            kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Что ты умеешь?", callback_data="what_can_i_do"))
            if uid in BOSSES: kb.add(types.InlineKeyboardButton("⚙️ Настройки", callback_data="open_main_settings"))
            return bot.edit_message_text(f"Привет, {get_user_mention(c.from_user)}... Я Лиза.\n💭Чат: https://t.me/+8WZ4kwpAaZ0yZTRi\n🛒Бот: @vibe_247top_bot", cid, c.message.message_id, reply_markup=kb, parse_mode='HTML', disable_web_page_preview=True)

        if d == "open_main_settings":
            if uid not in BOSSES: return bot.answer_callback_query(c.id, "⛔", show_alert=True)
            bot.answer_callback_query(c.id)
            return bot.edit_message_text("🎛 Панель:", cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))

        if d == "m:toggle_intervene":
            set_v(cid, "intervene", not get_v(cid, "intervene", True))
            bot.answer_callback_query(c.id)
            return bot.edit_message_reply_markup(cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))

        if d in ["m:freq", "m:anger"]:
            bot.answer_callback_query(c.id)
            t = "freq" if d == "m:freq" else "anger"
            kb = types.InlineKeyboardMarkup(row_width=4)
            kb.add(*[types.InlineKeyboardButton(f"{v}%", callback_data=f"s:{t}:{v}") for v in [10, 20, 30, 40, 50, 70, 100]])
            kb.add(types.InlineKeyboardButton("« Назад", callback_data="open_main_settings"))
            return bot.edit_message_text("📊 Выберите значение:", cid, c.message.message_id, reply_markup=kb)

        if action == "s":
            t, v = parts[1], parts[2]
            bot.answer_callback_query(c.id)
            set_v(cid, t, int(v))
            return bot.edit_message_text("✅ Параметр зафиксирован.", cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))

        if d == "to_group_settings" and uid in BOSSES:
            try: bot.send_message(uid, "📢 Выберите группу:", reply_markup=chats_selection_kb())
            except Exception as e:
                logging.error(f"[GROUP SETTINGS] {e}")
                return bot.answer_callback_query(c.id, "⚠️ Нажми Start в ЛС с ботом.", show_alert=True)
            return bot.answer_callback_query(c.id, "Отправлено в ЛС")

        if d == "m:autopost_list":
            bot.answer_callback_query(c.id)
            return bot.edit_message_text("📢 Группа для автопостинга:", cid, c.message.message_id, reply_markup=chats_selection_kb())

        if action == "ap":
            sub = parts[1]
            if sub == "chat":
                target_cid = parts[2]
                bot.answer_callback_query(c.id)
                return bot.edit_message_text("📋 Посты:", cid, c.message.message_id, reply_markup=autopost_list_kb(target_cid))
            elif sub == "select":
                pid = parts[2]
                with state_lock:
                    active_fsm[uid] = {"action": "editing", "pid": pid}
                bot.answer_callback_query(c.id)
                return bot.edit_message_text(post_text_view(pid), cid, c.message.message_id, reply_markup=post_settings_kb(pid))
            elif sub == "create":
                c_str = parts[2]
                with state_lock:
                    data = db_get("autopost", {"posts": []})
                    new_id = str(int(time.time()))
                    data["posts"].append({
                        "id": new_id,
                        "name": f"Пост #{len([p for p in data['posts'] if str(p.get('chat_id'))==c_str])+1}",
                        "enabled": False, "interval": 3600, "daily_time": None, "start_date": None,
                        "auto_delete_prev": False, "last_msg_id": None, "text": "Текст...",
                        "photo": None, "buttons": [], "last_post": 0,
                        "chat_id": int(c_str) if c_str.lstrip('-').isdigit() else c_str
                    })
                    db_set("autopost", data)
                bot.answer_callback_query(c.id, "✅ Создан")
                return bot.edit_message_text("📋 Посты", cid, c.message.message_id, reply_markup=autopost_list_kb(c_str))
            elif sub == "delmenu":
                bot.answer_callback_query(c.id)
                kb = types.InlineKeyboardMarkup(row_width=1)
                target_chat = parts[2]
                for p in [p for p in db_get("autopost", {"posts": []}).get("posts", []) if str(p.get("chat_id")) == target_chat]:
                    kb.add(types.InlineKeyboardButton(f"🗑 Удалить: {html.escape(p.get('name', 'Пост'))}", callback_data=f"ap:delfinal:{p['id']}"))
                return bot.edit_message_text("🗑 Выберите пост:", cid, c.message.message_id, reply_markup=kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:chat:{target_chat}")))
            elif sub == "delfinal":
                pid = parts[2]
                with state_lock:
                    data = db_get("autopost", {"posts": []})
                    c_str = next((str(p.get("chat_id")) for p in data["posts"] if p["id"] == pid), "-1004374303475")
                    data["posts"] = [p for p in data["posts"] if p["id"] != pid]
                    db_set("autopost", data)
                bot.answer_callback_query(c.id, "🗑 Удален")
                return bot.edit_message_text("📋 Посты", cid, c.message.message_id, reply_markup=autopost_list_kb(c_str))
            elif sub in ["toggle", "autodel"]:
                pid = parts[2]
                with state_lock:
                    data = db_get("autopost", {"posts": []})
                    for p in data["posts"]:
                        if p["id"] == pid:
                            if sub == "toggle": p["enabled"] = not p.get("enabled", False)
                            else: p["auto_delete_prev"] = not p.get("auto_delete_prev", False)
                    db_set("autopost", data)
                bot.answer_callback_query(c.id, "✅")
                return bot.edit_message_text(post_text_view(pid), cid, c.message.message_id, reply_markup=post_settings_kb(pid))
            elif sub == "int_menu":
                pid = parts[2]
                bot.answer_callback_query(c.id)
                kb = types.InlineKeyboardMarkup(row_width=3)
                kb.add(types.InlineKeyboardButton("15м", callback_data=f"ap:setint:{pid}:900"), types.InlineKeyboardButton("1ч", callback_data=f"ap:setint:{pid}:3600"), types.InlineKeyboardButton("6ч", callback_data=f"ap:setint:{pid}:21600"))
                kb.add(types.InlineKeyboardButton("Выкл", callback_data=f"ap:setint:{pid}:0"), types.InlineKeyboardButton("✏ Свое", callback_data=f"ap:custom_int:{pid}"))
                return bot.edit_message_text("⏱ Выберите интервал:", cid, c.message.message_id, reply_markup=kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
            elif sub in ["setint", "settime", "setdate", "clrbtns", "delph"]:
                pid = parts[2]
                with state_lock:
                    data = db_get("autopost", {"posts": []})
                    for p in data["posts"]:
                        if p["id"] == pid:
                            if sub == "setint": p["interval"], p["daily_time"] = int(parts[3]), None
                            elif sub == "settime": p["daily_time"] = None if parts[3] == "OFF" else parts[3]
                            elif sub == "setdate": p["start_date"] = None if parts[3] == "OFF" else parts[3]
                            elif sub == "clrbtns": p["buttons"] = []
                            elif sub == "delph": p["photo"] = None
                    db_set("autopost", data)
                bot.answer_callback_query(c.id, "✅ Сохранено")
                return bot.edit_message_text(post_text_view(pid), cid, c.message.message_id, reply_markup=post_settings_kb(pid))
            elif sub.startswith("custom_") or sub in ["text", "btns", "photo"]:
                pid = parts[2]
                with state_lock:
                    if "custom_int" in d: act = "interval"
                    elif "custom_time" in d: act = "time"
                    elif "custom_date" in d: act = "date"
                    elif sub == "text": act = "text"
                    elif sub == "btns": act = "buttons"
                    elif sub == "photo": act = "photo"
                    else: act = None
                    active_fsm[uid] = {"action": act, "pid": pid}
                bot.answer_callback_query(c.id)
                msgs = {
                    "interval": "Отправьте интервал (например: 45м, 3ч):",
                    "time": "Отправьте время (например: 14:30):",
                    "date": "Отправьте дату (например: 2026-10-01):",
                    "text": "📝 Отправьте новый текст поста.",
                    "buttons": "Отправь кнопки:\nТекст - https://ссылка.com\nИли: Текст - cmd:/lucky_game",
                    "photo": "🖼 Отправьте фото в этот чат."
                }
                return bot.send_message(cid, msgs.get(act, "Отправьте значение:"))
            elif sub in ["photo_menu", "btns_menu", "time_menu", "date_menu"]:
                pid = parts[2]
                bot.answer_callback_query(c.id)
                kb = types.InlineKeyboardMarkup(row_width=1)
                if sub == "photo_menu": kb.add(types.InlineKeyboardButton("🖼 Загрузить", callback_data=f"ap:photo:{pid}"), types.InlineKeyboardButton("🗑 Удалить", callback_data=f"ap:delph:{pid}"))
                elif sub == "btns_menu": kb.add(types.InlineKeyboardButton("➕ Настроить", callback_data=f"ap:btns:{pid}"), types.InlineKeyboardButton("🗑 Удалить все", callback_data=f"ap:clrbtns:{pid}"))
                elif sub == "time_menu": kb.add(types.InlineKeyboardButton("12:00", callback_data=f"ap:settime:{pid}:12:00"), types.InlineKeyboardButton("Выкл", callback_data=f"ap:settime:{pid}:OFF"), types.InlineKeyboardButton("Свое", callback_data=f"ap:custom_time:{pid}"))
                elif sub == "date_menu": kb.add(types.InlineKeyboardButton("Сразу", callback_data=f"ap:setdate:{pid}:OFF"), types.InlineKeyboardButton("Ввести", callback_data=f"ap:custom_date:{pid}"))
                return bot.edit_message_text("Настройка:", cid, c.message.message_id, reply_markup=kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
            elif sub == "send":
                pid = parts[2]
                with state_lock:
                    data = db_get("autopost", {"posts": []})
                    post = next((p for p in data["posts"] if p["id"] == pid), None)
                if post:
                    try:
                        send_specific_post(int(post.get("chat_id", -1004374303475)), post)
                        with state_lock:
                            fresh = db_get("autopost", {"posts": []})
                            for p in fresh.get("posts", []):
                                if p["id"] == pid:
                                    p["last_post"] = time.time()
                                    p["last_msg_id"] = post.get("last_msg_id")
                            db_set("autopost", fresh)
                        return bot.answer_callback_query(c.id, "🚀 Отправлено!", show_alert=True)
                    except Exception as e:
                        logging.error(f"[MANUAL SEND] {e}")
                        return bot.answer_callback_query(c.id, f"⚠️ Ошибка", show_alert=True)
            elif sub == "preview":
                pid = parts[2]
                bot.answer_callback_query(c.id)
                with state_lock:
                    post = next((p for p in db_get("autopost", {"posts": []}).get("posts", []) if p["id"] == pid), None)
                if post:
                    mk = build_post_user_kb(post)
                    bot.send_message(cid, "👁 <b>Предпросмотр:</b>", parse_mode='HTML')
                    if post.get("photo"): bot.send_photo(cid, post.get("photo"), caption=post.get("text", ""), reply_markup=mk, parse_mode='HTML')
                    else: bot.send_message(cid, post.get("text", ""), reply_markup=mk, parse_mode='HTML')
                    return bot.send_message(cid, "Вот так выглядит пост.", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))

        bot.answer_callback_query(c.id)
    except Exception as e:
        logging.error(f"[CALLBACK ERROR] {e}", exc_info=True)

@bot.message_handler(content_types=['photo'])
def on_photo(m):
    try:
        if not check_access(m): return
        uid = m.from_user.id
        if m.chat.type == 'private' and uid in BOSSES:
            with state_lock:
                fsm = active_fsm.get(uid)
                if fsm and fsm.get("action") == "photo":
                    pid = fsm["pid"]
                    active_fsm.pop(uid, None)
                    data = db_get("autopost", {"posts": []})
                    for p in data["posts"]:
                        if p["id"] == pid: p["photo"] = m.photo[-1].file_id
                    db_set("autopost", data)
                    bot.reply_to(m, "🖼 Фото сохранено", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
    except Exception as e:
        logging.error(f"[ON PHOTO] {e}", exc_info=True)

@bot.message_handler(func=lambda m: True)
def text_handler(m):
    try:
        if not check_access(m): return
        uid, cid, t = m.from_user.id, m.chat.id, m.text.strip()
        boss = uid in BOSSES

        if m.chat.type == 'private' and boss:
            with state_lock:
                fsm = active_fsm.get(uid)
            if fsm:
                action, pid = fsm.get("action"), fsm.get("pid")
                with state_lock:
                    active_fsm.pop(uid, None)
                
                if action == "buttons":
                    new_btns = []
                    for line in t.split('\n'):
                        row = []
                        for part in line.split('|'):
                            if '-' in part:
                                try:
                                    t_btn, val = part.rsplit('-', 1)
                                    t_btn, val = t_btn.strip(), val.strip()
                                    if val.startswith("cmd:"): row.append({"text": t_btn, "command": val[4:].strip()})
                                    elif val.startswith("cb:"): row.append({"text": t_btn, "callback_data": val[3:].strip()})
                                    else: row.append({"text": t_btn, "url": val if val.startswith("http") else "https://"+val})
                                except Exception as e: logging.error(f"[BTN PARSE] {e}")
                        if row: new_btns.append(row)
                    with state_lock:
                        data = db_get("autopost", {"posts": []})
                        for p in data["posts"]:
                            if p["id"] == pid: p["buttons"] = new_btns
                        db_set("autopost", data)
                    return bot.reply_to(m, "✅ Кнопки сохранены", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))

                elif action == "text":
                    with state_lock:
                        data = db_get("autopost", {"posts": []})
                        for p in data["posts"]:
                            if p["id"] == pid: p["text"] = t
                        db_set("autopost", data)
                    return bot.reply_to(m, "📝 Текст сохранен", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))

                elif action == "interval":
                    sec = parse_interval_input(t)
                    if sec is not None:
                        with state_lock:
                            data = db_get("autopost", {"posts": []})
                            for p in data["posts"]:
                                if p["id"] == pid: p["interval"], p["daily_time"] = sec, None
                            db_set("autopost", data)
                        return bot.reply_to(m, "⏱ Интервал сохранен", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
                    return bot.reply_to(m, "⚠️ Ошибка формата. Пример: 30м, 2ч")

                elif action == "time":
                    if re.match(r'^\d{2}:\d{2}$', t):
                        with state_lock:
                            data = db_get("autopost", {"posts": []})
                            for p in data["posts"]:
                                if p["id"] == pid: p["daily_time"] = t
                            db_set("autopost", data)
                        return bot.reply_to(m, "🕑 Время сохранено", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
                    return bot.reply_to(m, "⚠️ Формат: ЧЧ:ММ (12:00)")

                elif action == "date":
                    if re.match(r'^\d{4}-\d{2}-\d{2}$', t):
                        with state_lock:
                            data = db_get("autopost", {"posts": []})
                            for p in data["posts"]:
                                if p["id"] == pid: p["start_date"] = t
                            db_set("autopost", data)
                        return bot.reply_to(m, "📅 Дата сохранена", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
                    return bot.reply_to(m, "⚠️ Формат: ГГГГ-ММ-ДД (2026-10-01)")

        t_lower = t.lower()
        with state_lock:
            is_safe_active = cid in active_safes
        if is_safe_active and re.fullmatch(r'\d{3}', t):
            with state_lock:
                if cid in active_safes and t == active_safes[cid]["code"]:
                    del active_safes[cid]
                    ldrs = db_get("safe_leaders", {})
                    ldrs.setdefault(str(uid), {"name": m.from_user.first_name, "wins": 0})["wins"] += 1
                    db_set("safe_leaders", ldrs)
                    auto_del(bot.send_message(cid, f"🎉 СЕЙФ ВЗЛОМАН\nМастер {get_user_mention(m.from_user)} подобрал код: {t}", parse_mode='HTML'), 180)
            return

        with state_lock:
            is_cities_active = cid in active_cities_games
        if is_cities_active and CYRILLIC_CITY_RE.match(t):
            executor.submit(process_city_guess, cid, uid, m.from_user.first_name, t)

        if m.chat.type in ['group', 'supergroup'] and not boss and any(s in t_lower for s in SUSP):
            def threat_check():
                try:
                    if is_threat(m.text):
                        until = int(time.time()) + 300
                        bot.restrict_chat_member(cid, uid, until_date=until, permissions=ChatPermissions(can_send_messages=False))
                        mention = get_user_mention(m.from_user)
                        bosses_ping = " ".join([f"<a href='tg://user?id={b}'>⚠️</a>" for b in BOSSES])
                        alert_msg = f"{bosses_ping} 🛡 Обнаружена угроза от {mention}. Пользователь автоматически заглушен на 5 минут для проверки."
                        bot.send_message(cid, alert_msg, parse_mode='HTML')
                except Exception as e:
                    logging.error(f"[THREAT ACTION] {e}", exc_info=True)
            executor.submit(threat_check)

        direct = m.chat.type == 'private' or (m.reply_to_message and m.reply_to_message.from_user.id == BOT_ID) or "лиза" in t_lower or f"@{BOT_USER}" in t_lower
        if direct or any(w in t_lower for w in CONFL):
            if not get_v(cid, "intervene", True) or random.randint(1, 100) > get_v(cid, "freq", 40): return
            prompt = f"В чате ссора: {m.reply_to_message.text} -> {m.text}. Резюме:" if (any(w in t_lower for w in CONFL) and m.reply_to_message) else m.text
            def ai_task():
                try:
                    bot.send_chat_action(cid, 'typing')
                    ans = call_ai([{"role": "system", "content": SYS_PROMPT}, {"role": "user", "content": prompt}])
                    if m.chat.type == 'private': bot.send_message(cid, ans, parse_mode='HTML')
                    else: bot.send_message(cid, f"{get_user_mention(m.from_user)}, {ans}", parse_mode='HTML')
                except Exception as e:
                    logging.error(f"[AI TASK] {e}", exc_info=True)
            executor.submit(ai_task)
    except Exception as e:
        logging.error(f"[TEXT HANDLER] {e}", exc_info=True)

if __name__ == "__main__":
    logging.info("Бот запущен и готов к работе!")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"Сбой связи: {e}", exc_info=True)
            time.sleep(5)
