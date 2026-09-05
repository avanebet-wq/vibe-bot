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

from config import TOKEN, OPENROUTER_KEY, BOSSES, AI_MODEL, ALLOWED_GROUPS, ALLOWED_GROUPS_RAW, DENIED_MSG, KYIV_TZ, SYS_PROMPT, SUSP, MUTES, CONFL, LOG_CHAT_ID
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
executor = ThreadPoolExecutor(max_workers=10)

state_lock = threading.RLock()
active_safes = {}
lucky_limits = {}
active_lucky_players = set()
untrusted_warned = set()
last_command_messages = {}
messages_to_delete = []
active_fsm = {}

# --- ИГРА «СЛОВА» ---
pending_word_lobbies = {}
active_word_games = {}
WORDS_TURN_TIMEOUT = 180
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

# --- СИСТЕМА РАНГОВ И ОПЫТА (ДЛЯ ИГРОКОВ) ---
RANKS = [
    (0, "🌱 Новичок"), (100, "🌿 Травяной"), (300, "💨 Пыхач"), (600, "🍃 Хапарь"),
    (1000, "😶‍🌫️ Дунувший"), (1500, "🌀 Обдутый"), (2200, "💨 Дутый"),
    (3200, "🧙 Зелёный Маг"), (4500, "🔥 Мастер Напаса"), (6000, "🌿 Шишечный Гуру"),
    (8000, "🧙‍♂️ Архимаг Дыма"), (10500, "👑 Повелитель Хапки"), (14000, "🏆 Легендарный Пыхарь"),
    (18000, "☁️ Верховный Стоунер"), (25000, "👑🔥 БОСС ШИШЕК")
]

# --- СИСТЕМА ИЕРАРХИИ АДМИНИСТРАТОРОВ (IRIS-STYLE) ---
ADMIN_RANKS = {
    1: "🌱 1 РАНГ — СЕМЕЧКО",
    2: "🍃 2 РАНГ — РОСТОК",
    3: "🌿 3 РАНГ — ХАПЕР",
    4: "🔥 4 РАНГ — СТАФФЕР",
    5: "👑 5 РАНГ — БОСС"
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
    "мут": "can_mute", "/mute": "can_mute", "снятьмут": "can_mute", "/unmute": "can_mute",
    "бан": "can_ban", "/ban": "can_ban", "снятьбан": "can_ban", "/unban": "can_ban",
    "кик": "can_kick", "/kick": "can_kick",
    "пред": "can_warn", "/warn": "can_warn", "снятьпред": "can_warn", "/unwarn": "can_warn",
    "повысить": "can_promote", "понизить": "can_promote", "разжаловать": "can_promote"
}

def get_rank_permissions(cid, rank):
    db = db_get("rank_permissions", {})
    chat_perms = db.get(str(cid), {})
    if str(rank) in chat_perms:
        return chat_perms[str(rank)]
    return dict(DEFAULT_RANK_PERMS.get(int(rank), {}))

def set_rank_permission(cid, rank, perm_name, value):
    db = db_get("rank_permissions", {})
    chat_perms = db.setdefault(str(cid), {})
    for r in range(1, 6):
        if str(r) not in chat_perms:
            chat_perms[str(r)] = dict(DEFAULT_RANK_PERMS[r])
    chat_perms[str(rank)][perm_name] = value
    db_set("rank_permissions", db)

def set_admin_rank(cid, uid, rank):
    with state_lock:
        admins = db_get("chat_admins", {})
        chat_adms = admins.setdefault(str(cid), {})
        if rank <= 0: chat_adms.pop(str(uid), None)
        else: chat_adms[str(uid)] = rank
        db_set("chat_admins", admins)

def get_admin_rank(cid, uid):
    if uid in BOSSES: return 5
    with state_lock:
        admins = db_get("chat_admins", {})
        chat_adms = admins.get(str(cid), {})
        if str(uid) in chat_adms: return chat_adms[str(uid)]
    try:
        member = bot.get_chat_member(cid, uid)
        if member.status == 'creator':
            set_admin_rank(cid, uid, 5)
            return 5
    except: pass
    return 0

def has_permission(cid, uid, perm_name):
    if uid in BOSSES: return True
    rank = get_admin_rank(cid, uid)
    if rank <= 0: return False
    perms = get_rank_permissions(cid, rank)
    return perms.get(perm_name, False)

# --- ИНСТРУМЕНТЫ МОДЕРАЦИИ ---
def log_moderation_action(chat_title_or_id, text):
    if LOG_CHAT_ID:
        try: bot.send_message(LOG_CHAT_ID, f"[{chat_title_or_id}] {text}", parse_mode="HTML")
        except Exception as e: logging.error(f"[LOG MODERATION] Failed to send log: {e}")

def parse_duration(time_str):
    if not time_str or time_str.lower() in ["навсегда", "forever", "0"]: return 0, True
    match = re.match(r'^(\d+)\s*([a-zA-Zа-яА-ЯёЁ]+)$', time_str.lower())
    if not match: return 0, False
    v = int(match.group(1))
    u = match.group(2)
    if u.startswith('м') and not u.startswith('мес') or u.startswith('m') and not u.startswith('mo'): return v * 60, True
    if u.startswith('ч') or u.startswith('h'): return v * 3600, True
    if u.startswith('д') or u.startswith('d'): return v * 86400, True
    if u.startswith('н') or u.startswith('w'): return v * 86400 * 7, True
    if u.startswith('мес') or u.startswith('mo'): return v * 86400 * 30, True
    return 0, False

def extract_target_and_args(m, text_parts):
    target_uid, target_name = None, None
    args = []
    if m.reply_to_message:
        target_uid = m.reply_to_message.from_user.id
        target_name = m.reply_to_message.from_user.first_name
        args = text_parts[1:]
    else:
        for i, part in enumerate(text_parts[1:], start=1):
            if part.startswith('@') and len(part) > 1:
                uname = part[1:].lower()
                with state_lock:
                    for u_id_str, data in db_get("users_data", {}).items():
                        if data.get("uname", "").lower() == uname:
                            target_uid = int(u_id_str)
                            target_name = data.get("name", uname)
                            break
                args = text_parts[1:i] + text_parts[i+1:]
                break
            elif part.isdigit():
                with state_lock:
                    u_data = db_get("users_data", {}).get(part)
                    if u_data:
                        target_uid = int(part)
                        target_name = u_data.get("name", f"ID:{part}")
                        args = text_parts[1:i] + text_parts[i+1:]
                        break
    return target_uid, target_name, args

def issue_warn(cid, chat_title, target_uid, target_name, admin_uid, admin_name, reason, m_to_reply, warn_count=1):
    max_warns = get_v(cid, "max_warns", 3)
    warn_action = get_v(cid, "warn_action", "mute")
    
    with state_lock:
        db = db_get("chat_warns", {})
        cw = db.setdefault(str(cid), {})
        uw = cw.setdefault(str(target_uid), {"count": 0, "history": []})
        
        uw["count"] += warn_count
        uw["history"].append({
            "reason": reason, "by_uid": admin_uid, "by_name": admin_name, "date": time.time()
        })
        count = uw["count"]
        db_set("chat_warns", db)
        
    mention_admin = get_user_mention(user_id=admin_uid, first_name=admin_name) if admin_uid != BOT_ID else "🤖 Автомодератор"
    mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
    txt = f"⚠️ {mention_admin} выдал предупреждение {mention_target} ({count}/{max_warns})\nПричина: {reason}"
    log_moderation_action(chat_title or str(cid), txt)
    
    if count >= max_warns:
        with state_lock:
            db = db_get("chat_warns", {})
            db[str(cid)][str(target_uid)]["count"] = 0
            db_set("chat_warns", db)
        action_txt = ""
        if warn_action == "ban":
            try:
                bot.ban_chat_member(cid, target_uid, until_date=0)
                action_txt = f"🔨 {mention_target} забанен навсегда (достигнут лимит предупреждений)."
            except: action_txt = f"⚠️ Не удалось забанить {mention_target} (нет прав)."
        elif warn_action == "kick":
            try:
                bot.ban_chat_member(cid, target_uid, until_date=0)
                bot.unban_chat_member(cid, target_uid, only_if_banned=True)
                action_txt = f"👢 {mention_target} кикнут (достигнут лимит предупреждений)."
            except: action_txt = f"⚠️ Не удалось кикнуть {mention_target} (нет прав)."
        else:
            try:
                bot.restrict_chat_member(cid, target_uid, until_date=0, permissions=ChatPermissions(can_send_messages=False))
                action_txt = f"🔇 {mention_target} получил мут навсегда (достигнут лимит предупреждений)."
            except: action_txt = f"⚠️ Не удалось замутить {mention_target} (нет прав)."
        txt += f"\n\n{action_txt}"
        log_moderation_action(chat_title or str(cid), action_txt)

    if m_to_reply: finish_command(m_to_reply, "warn", bot.send_message(cid, txt, parse_mode="HTML"))
    else: bot.send_message(cid, txt, parse_mode="HTML")

# --- СИСТЕМА ФАРМА ТРАВКИ ---
def pluralize_weed(n):
    if n % 10 == 1 and n % 100 != 11: return "травка"
    elif 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20): return "травки"
    else: return "травок"

def get_farm_reward():
    r = random.random()
    if r < 0.50: return 1, random.randint(1, 5)
    elif r < 0.75: return 2, random.randint(5, 15)
    elif r < 0.90: return 3, random.randint(15, 38)
    elif r < 0.97: return 4, random.randint(38, 55)
    else: return 5, random.randint(55, 100)

def get_user_rank_info(xp):
    curr_rank, next_xp, next_rank = RANKS[0][1], 100, RANKS[1][1]
    for i, (r_xp, r_name) in enumerate(RANKS):
        if xp >= r_xp:
            curr_rank = r_name
            if i + 1 < len(RANKS):
                next_xp, next_rank = RANKS[i+1][0], RANKS[i+1][1]
            else:
                next_xp, next_rank = 0, None
        else: break
    return curr_rank, xp, next_xp, next_rank

# --- ОБЩИЕ УТИЛИТЫ ---
def get_v(cid, k, d=False):
    with state_lock: return db_get("settings", {}).get(str(cid), {}).get(k, d)

def set_v(cid, k, val):
    with state_lock:
        s = db_get("settings", {})
        s.setdefault(str(cid), {"freq": 40, "anger": 40, "intervene": True, "del_sys": False, "max_warns": 3, "warn_action": "mute"})[k] = val
        db_set("settings", s)

def track_and_replace_specific_cmd(chat_id, user_id, cmd_name, new_msg):
    if not new_msg: return
    with state_lock:
        key = (chat_id, user_id, cmd_name)
        if key in last_command_messages:
            try: bot.delete_message(chat_id, last_command_messages[key])
            except Exception as e:
                if "message to delete not found" not in str(e): logging.error(f"[DEL OLD CMD] {e}")
        last_command_messages[key] = new_msg.message_id

def auto_del(msg, ttl=180):
    if msg:
        with state_lock: messages_to_delete.append({"cid": msg.chat.id, "mid": msg.message_id, "time": time.time() + ttl})

def finish_command(m, cmd_name, sent_msg=None, ttl=None, delete_user_msg=True):
    if delete_user_msg and not getattr(m, "is_callback", False):
        try: bot.delete_message(m.chat.id, m.message_id)
        except Exception as e:
            if "message to delete not found" not in str(e): logging.error(f"[DEL CMD:{cmd_name}] {e}")
    if sent_msg:
        track_and_replace_specific_cmd(m.chat.id, m.from_user.id, cmd_name, sent_msg)
        if ttl: auto_del(sent_msg, ttl)

def record_xp_and_stats(m):
    if m.chat.type not in ['group', 'supergroup']: return
    uid = m.from_user.id
    if m.from_user.is_bot: return
    
    fname, uname = m.from_user.first_name, m.from_user.username
    cid = m.chat.id
    
    with state_lock:
        users = db_get("users_data", {})
        u = users.setdefault(str(uid), {
            "xp": 0, "msgs": 0, "name": fname, "uname": uname, "first_seen": time.time(),
            "respects": 0, "given_respects": 0, "respect_reset": 0, "weed": 0, "last_farm": 0
        })
        if uname: u["uname"] = uname
        u["name"] = fname
        old_xp = u["xp"]
        u["xp"] += random.randint(1, 3)
        u["msgs"] += 1
        db_set("users_data", users)
        new_xp = u["xp"]
        
    old_rank, _, _, _ = get_user_rank_info(old_xp)
    new_rank, _, _, _ = get_user_rank_info(new_xp)
    if old_rank != new_rank:
        try:
            bot.send_message(cid, f"🎉 <b>ПОЗДРАВЛЯЕМ!</b>\n\n{get_user_mention(m.from_user)} достигает ранга: <b>{new_rank}</b> 🚀\nТак держать!", parse_mode='HTML')
            try: bot.set_chat_administrator_custom_title(chat_id=cid, user_id=uid, custom_title=new_rank)
            except: pass
        except: pass

def handle_profile_request(m, target_uid, target_user=None):
    with state_lock:
        users = db_get("users_data", {})
        u = users.get(str(target_uid))
    if not u:
        try: bot.reply_to(m, "🤷‍♀️ Информации об этом пользователе пока нет.")
        except: pass
        return
    
    xp, msgs = u.get("xp", 0), u.get("msgs", 0)
    respects, weed = u.get("respects", 0), u.get("weed", 0)
    first_seen = u.get("first_seen", time.time())
    
    days_in_group = max(1, int((time.time() - first_seen) / 86400))
    fs_date = datetime.fromtimestamp(first_seen, KYIV_TZ).strftime('%d.%m.%Y')
    
    activity = "Низкая 💤"
    if msgs > 1000: activity = "Высокая 🔥"
    elif msgs > 100: activity = "Средняя ⚡"
    
    rank, _, next_xp, next_rank = get_user_rank_info(xp)
    
    if next_xp:
        progress = min(1.0, xp / next_xp)
        filled = int(progress * 10)
        p_bar = "▓" * filled + "░" * (10 - filled)
        next_info = f"🌱 СЛЕДУЮЩИЙ РАНГ\n{next_rank} — {next_xp} XP\n\nДо повышения осталось: {next_xp - xp} XP\n━━━━━━━VIBE━━━━━━━"
    else:
        p_bar = "▓" * 10
        next_info = "👑 ВЫ ДОСТИГЛИ МАКСИМАЛЬНОГО РАНГА!\n━━━━━━━VIBE━━━━━━━"
    
    title = "ВАШ ПРОФИЛЬ" if target_uid == m.from_user.id else "ПРОФИЛЬ"
    text = (
        "━━━━━━━VIBE━━━━━━━\n"
        f"🌿 <b>{title}:</b>\n\n"
        f"👤 Это: {get_user_mention(user_id=target_uid, first_name=u.get('name'))}\n\n"
        f"🏆 Ранг: <b>{rank}</b>\n"
        f"⭐ XP: {xp} / {next_xp if next_xp else 'MAX'}\n"
        f"🌿 Баланс: <b>{weed}</b> {pluralize_weed(weed)}\n"
        f"🤝 Уважение: <b>{respects}</b>\n"
        f"📊 Прогресс: {p_bar}\n\n"
        f"💬 Сообщений: {msgs}\n"
        f"🔥 Активность: {activity}\n"
        f"📅 В группе уже: {days_in_group} дн.\n"
        f"🆕 Первое появление: {fs_date}\n"
        "━━━━━━━VIBE━━━━━━━\n\n"
        f"{next_info}"
    )
    try: bot.send_message(m.chat.id, text, parse_mode='HTML')
    except: pass

def process_farm_command(m):
    cid, uid, fname, uname = m.chat.id, m.from_user.id, m.from_user.first_name, m.from_user.username
    with state_lock:
        users = db_get("users_data", {})
        u = users.setdefault(str(uid), {
            "xp": 0, "msgs": 0, "name": fname, "uname": uname, "first_seen": time.time(),
            "respects": 0, "given_respects": 0, "respect_reset": 0, "weed": 0, "last_farm": 0
        })
        now = time.time()
        cooldown = 4 * 3600
        elapsed = now - u.get("last_farm", 0)
        
        if elapsed < cooldown:
            rem = int(cooldown - elapsed)
            h, rem = divmod(rem, 3600)
            m_min, s = divmod(rem, 60)
            time_str = f"{h}ч {m_min}м {s}с" if h > 0 else f"{m_min}м {s}с"
            msg = bot.send_message(cid, f"❌ Вы слишком устали и не можете отправиться за травкой. 😔🌿\n\nОтдохните и попробуйте через: <b>{time_str}</b>", parse_mode='HTML')
            finish_command(m, "farm_cd", msg, ttl=15)
            return
            
        weeds, xp_gain = get_farm_reward()
        old_xp = u["xp"]
        u["xp"] += xp_gain
        u["weed"] = u.get("weed", 0) + weeds
        u["last_farm"] = now
        new_xp = u["xp"]
        db_set("users_data", users)
        
    msg = bot.send_message(cid, f"✅ Успешный Фарм, вы получили: 🌿 {weeds} {pluralize_weed(weeds)} 😁\n🌟 Опыт: +{xp_gain} XP", parse_mode='HTML')
    finish_command(m, "farm_success", msg, ttl=30)
    
    old_rank, _, _, _ = get_user_rank_info(old_xp)
    new_rank, _, _, _ = get_user_rank_info(new_xp)
    if old_rank != new_rank:
        try:
            bot.send_message(cid, f"🎉 <b>ПОЗДРАВЛЯЕМ!</b>\n\n{get_user_mention(m.from_user)} достигает ранга: <b>{new_rank}</b> 🚀\nТак держать!", parse_mode='HTML')
            try: bot.set_chat_administrator_custom_title(chat_id=cid, user_id=uid, custom_title=new_rank)
            except: pass
        except: pass

def reply_no_rights(m):
    try: bot.delete_message(m.chat.id, m.message_id)
    except: pass
    msg = bot.send_message(m.chat.id, "⛔ У вас нет прав на использование этой команды.", parse_mode="HTML")
    auto_del(msg, 5)

def format_safe_leaderboard():
    with state_lock:
        ldrs = db_get("safe_leaders", {})
        if not ldrs: return "🏆 Рейтинг взломщиков сейфа пока пуст."
        txt = "━━━━━━━VIBE━━━━━━━\n🔐 <b>РЕЙТИНГ ВЗЛОМЩИКОВ СЕЙФА</b>\n\n"
        sorted_ldrs = sorted(ldrs.items(), key=lambda x: x[1].get("wins", 0), reverse=True)[:10]
        for i, (uid_str, uinfo) in enumerate(sorted_ldrs, 1):
            m_icon = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else "▫️"
            txt += f"{m_icon} {i}. {get_user_mention(user_id=int(uid_str), first_name=uinfo.get('name'))} — <b>{uinfo.get('wins', 0)}</b> побед\n"
        return txt + "\n━━━━━━━VIBE━━━━━━━"

def format_words_leaderboard():
    with state_lock:
        ldrs = db_get("words_leaders", {})
        if not ldrs: return "🏆 Рейтинг игры «Слова» пока пуст."
        txt = "━━━━━━━VIBE━━━━━━━\n🔤 <b>РЕЙТИНГ ИГРЫ «СЛОВА»</b>\n\n"
        for i, (uid_str, uinfo) in enumerate(sorted(ldrs.items(), key=lambda x: x[1].get("wins", 0), reverse=True)[:10], 1):
            m_icon = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else "▫️"
            txt += f"{m_icon} {i}. {get_user_mention(user_id=int(uid_str), first_name=uinfo.get('name'))} — <b>{uinfo.get('wins', 0)}</b> слов\n"
        return txt + "\n━━━━━━━VIBE━━━━━━━"

def parse_interval_input(text):
    match = re.match(r'^(\d+)([дчм])$', text.lower().strip())
    if not match: return None
    val, unit = int(match.group(1)), match.group(2)
    return val * 60 if unit == 'м' else (val * 3600 if unit == 'ч' else val * 86400)

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
    cid = m.chat.id
    if m.chat.type in ['group', 'supergroup']:
        with state_lock:
            is_words_active = cid in active_word_games
            game = active_word_games.get(cid)
        if is_words_active and game:
            if uid not in game["players"] and uid not in BOSSES and get_admin_rank(cid, uid) == 0:
                try: bot.delete_message(cid, m.message_id)
                except: pass
                warn = bot.send_message(cid, f"🤫 {get_user_mention(m.from_user)}, тсс! Идёт игра в слова, писать могут только участники!", parse_mode='HTML')
                auto_del(warn, 5)
                return False
    if m.chat.type == 'private':
        if uid not in BOSSES:
            try: bot.delete_message(cid, m.message_id)
            except: pass
            msg = bot.send_message(cid, "⛔ У вас нет прав на использование этой команды.", parse_mode='HTML')
            auto_del(msg, 5)
            return False
        return True
    register_chat(m.chat)
    if str(cid).replace("-100", "").replace("-", "") not in ALLOWED_GROUPS_RAW and cid not in ALLOWED_GROUPS:
        try: bot.delete_message(cid, m.message_id)
        except: pass
        msg = bot.send_message(cid, "⛔ Чат не авторизован.", parse_mode='HTML')
        auto_del(msg, 5)
        return False
    return True

@bot.message_handler(content_types=['new_chat_members', 'left_chat_member', 'new_chat_title', 'new_chat_photo', 'delete_chat_photo', 'group_chat_created', 'supergroup_chat_created', 'channel_chat_created', 'migrate_to_chat_id', 'migrate_from_chat_id', 'pinned_message'])
def handle_system_messages(m):
    if get_v(m.chat.id, "del_sys", False):
        try: bot.delete_message(m.chat.id, m.message_id)
        except Exception as e:
            if "message to delete not found" not in str(e): pass

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
                        except Exception as e:
                            if "message to delete not found" not in str(e): logging.error(f"[CLEANUP DEL] {e}")
                    else: remaining.append(item)
                messages_to_delete[:] = remaining
        except Exception as e: logging.error(f"[CLEANUP WORKER] {e}", exc_info=True)
threading.Thread(target=cleanup_worker, daemon=True).start()

def send_lobby_msg(cid, lobby):
    text = build_lobby_text(lobby)
    kb = lobby_kb(cid)
    msg = None
    try:
        if os.path.exists("words_preview.png"):
            with open("words_preview.png", "rb") as photo: msg = bot.send_photo(cid, photo, caption=text, reply_markup=kb, parse_mode='HTML')
        else: msg = bot.send_message(cid, text, reply_markup=kb, parse_mode='HTML')
        if msg:
            try: bot.pin_chat_message(cid, msg.message_id, disable_notification=True)
            except: pass
    except Exception as e: logging.error(f"[SEND LOBBY] {e}")
    return msg

def repost_lobby(cid):
    with state_lock:
        lobby = pending_word_lobbies.get(cid)
        if not lobby or lobby.get("started"): return
        old_msg_id = lobby.get("reg_msg_id")
        
    if old_msg_id:
        try: bot.delete_message(cid, old_msg_id)
        except Exception as e:
            if "message to delete not found" not in str(e): pass
            
    msg = send_lobby_msg(cid, lobby)
    if msg:
        with state_lock:
            if cid in pending_word_lobbies: pending_word_lobbies[cid]["reg_msg_id"] = msg.message_id

def end_word_game(cid, game, reason="timeout"):
    try: bot.unpin_all_chat_messages(cid)
    except: pass
    
    scoreboard = build_active_scoreboard(game)
    if reason == "limit": txt = f"🏁 <b>ИГРА «СЛОВА» ЗАВЕРШЕНА!</b> 🎉\nУра! Вы совместно назвали 50 слов!\n\n📊 <b>ИТОГОВЫЙ СЧЁТ:</b>\n{scoreboard}"
    else: txt = f"⌛ <b>ИГРА «СЛОВА» ОКОНЧЕНА ПО ТАЙМ-АУТУ.</b>\nНазвано слов: {game['moves']}.\n\n📊 <b>ИТОГОВЫЙ СЧЁТ:</b>\n{scoreboard}\n\n/start_words_game — начать заново"
    try:
        msg = bot.send_message(cid, txt, parse_mode='HTML')
        try: bot.pin_chat_message(cid, msg.message_id, disable_notification=False)
        except: pass
    except: pass

def word_game_active_worker():
    while True:
        try:
            time.sleep(5)
            now = time.time()
            to_end, to_hint, to_remind = [], [], []
            with state_lock:
                for cid, game in list(active_word_games.items()):
                    elapsed = now - game["last_move_ts"]
                    if elapsed > WORDS_TURN_TIMEOUT:
                        to_end.append((cid, game))
                        del active_word_games[cid]
                        continue
                    if elapsed >= 120 and not game.get("hint_given"):
                        game["hint_given"] = True
                        to_hint.append((cid, game))
                    if now - game.get("last_reminder_ts", 0) >= 30:
                        game["last_reminder_ts"] = now
                        to_remind.append((cid, game))
                        
            for cid, game in to_end: end_word_game(cid, game, reason="timeout")
            for cid, game in to_hint:
                req_letter = game["next_letter"]
                possible_words = [w for w in BASE_WORDS if w.startswith(req_letter) and w not in game["used"]]
                if possible_words:
                    hint_word = random.choice(possible_words)
                    hint = hint_word[:max(1, len(hint_word)//2)] + "..."
                    try:
                        msg = bot.send_message(cid, f"💡 <b>ПОДСКАЗКА:</b>\nНикто не может вспомнить слово на «<b>{req_letter.upper()}</b>»?\nВот половинка: <b>{hint.upper()}</b>", parse_mode='HTML')
                        auto_del(msg, 30)
                    except: pass
            for cid, game in to_remind:
                try:
                    if game.get("reminder_msg_id"):
                        try: bot.delete_message(cid, game["reminder_msg_id"])
                        except: pass
                    req_letter = game["next_letter"]
                    msg = bot.send_message(cid, f"💜 <b>Игра идёт!</b>\nНапиши слово на букву «<b>{req_letter.upper()}</b>» 👇", parse_mode='HTML')
                    with state_lock:
                        if cid in active_word_games: active_word_games[cid]["reminder_msg_id"] = msg.message_id
                except: pass
        except Exception as e: logging.error(f"[WORDS ACTIVE WORKER] {e}", exc_info=True)
threading.Thread(target=word_game_active_worker, daemon=True).start()

def word_lobby_worker():
    while True:
        try:
            time.sleep(10)
            now, to_start, to_repost = time.time(), [], []
            with state_lock:
                for cid, lobby in list(pending_word_lobbies.items()):
                    if not lobby.get("started"):
                        if now >= lobby["end_time"]: to_start.append(cid)
                        elif now >= lobby.get("next_repost", 0):
                            lobby["next_repost"] = now + 60
                            to_repost.append(cid)
            for cid in to_repost: repost_lobby(cid)
            for cid in to_start: start_word_game_now(cid)
        except Exception as e: logging.error(f"[WORD LOBBY WORKER] {e}", exc_info=True)
threading.Thread(target=word_lobby_worker, daemon=True).start()

# --- ФОНОВЫЕ ЗАДАЧИ АВТОПОСТИНГА ---
def send_specific_post(chat_id, post):
    try:
        mk = build_post_user_kb(post)
        txt = post.get("text", "")
        if post.get("photo"): msg = bot.send_photo(chat_id, post["photo"], caption=txt, reply_markup=mk, parse_mode='HTML')
        else: msg = bot.send_message(chat_id, txt, reply_markup=mk, parse_mode='HTML')
        old_msg_id = None
        with state_lock:
            if post.get("auto_delete_prev") and post.get("last_msg_id"): old_msg_id = post["last_msg_id"]
            post["last_msg_id"] = msg.message_id
        if old_msg_id:
            try: bot.delete_message(chat_id, old_msg_id)
            except Exception as e:
                if "message to delete not found" not in str(e): logging.error(f"[AUTOPOST DEL PREV] {e}")
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
            for chat_id, p in to_send: send_specific_post(chat_id, p)
            if to_send:
                with state_lock:
                    fresh = db_get("autopost", {"posts": []})
                    by_id = {item["id"]: item for item in fresh.get("posts", [])}
                    for _, sent_post in to_send:
                        target = by_id.get(sent_post["id"])
                        if target:
                            target["last_msg_id"] = sent_post.get("last_msg_id")
                            target["last_post"] = sent_post.get("last_post")
                            if "last_sent_date" in sent_post: target["last_sent_date"] = sent_post["last_sent_date"]
                    db_set("autopost", fresh)
        except Exception as e: logging.error(f"[WORKER] {e}", exc_info=True)
threading.Thread(target=autopost_worker, daemon=True).start()

# --- ХЕЛПЕРЫ ДЛЯ ИГРЫ «СЛОВА» ---
def normalize_word(name):
    n = name.strip().lower().replace("ё", "е")
    n = re.sub(r'\s*-\s*', '-', n)
    return re.sub(r'\s+', ' ', n)

def first_letter(name):
    n = normalize_word(name)
    return n[0] if n else None

def effective_last_letter(name):
    n = normalize_word(name)
    idx = len(n) - 1
    while idx >= 0 and n[idx] in "ьъ":
        idx -= 1
    return n[idx] if idx >= 0 else None

def is_known_word(norm_name):
    if norm_name in BASE_WORDS: return True
    with state_lock: return norm_name in db_get("known_words_extra", [])

def learn_word(norm_name):
    with state_lock:
        extra = db_get("known_words_extra", [])
        if norm_name not in extra:
            extra.append(norm_name)
            db_set("known_words_extra", extra)

def ai_check_is_word(raw_name):
    ans = call_ai([{"role": "user", "content": f"Слово: '{raw_name}'. Это реально существующее слово русского языка (в любой форме — существительное, глагол, прилагательное и т.д.)? Отвечай строго: ДА или НЕТ."}], 10, 0.0)
    return "ДА" in ans.upper()

def resolve_is_word(raw_name, norm_name):
    if is_known_word(norm_name): return True
    is_word = ai_check_is_word(raw_name)
    if is_word: learn_word(norm_name)
    return is_word

# --- ЛОББИ ИГРЫ «СЛОВА» ---
def build_lobby_text(lobby):
    remaining = max(0, int(lobby["end_time"] - time.time()))
    mins, secs = remaining // 60, remaining % 60
    players = lobby["players"]
    plist = "\n".join(f"• {get_user_mention(user_id=u, first_name=n)}" for i, (u, n) in enumerate(players.items(), 1)) or "Пока никто не записался."
    seed_line = f"\n🎯 Первое слово: <b>{html.escape(lobby['seed'])}</b>" if lobby.get("seed") else ""
    return (
        "━━━━━━━VIBE━━━━━━━\n"
        "🔤 <b>ИГРА «СЛОВА»</b> 🌟\n\n"
        "<i>Правила: по очереди называем слова на русском языке. "
        "Слово должно начинаться на ту букву, которой закончилось предыдущее. "
        "Повторяться нельзя! За каждое слово — +1 балл!</i>\n"
        f"{seed_line}\n\n"
        f"⏳ <b>До старта:</b> {mins}м {secs}с\n"
        f"👥 <b>Участники ({len(players)}):</b>\n{plist}\n"
        "━━━━━━━VIBE━━━━━━━"
    )

def lobby_kb(cid):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📝 Записаться на игру", url=f"https://t.me/{BOT_USERNAME}?start=regword_{cid}"))
    kb.add(types.InlineKeyboardButton("❌ Отменить регистрацию", url=f"https://t.me/{BOT_USERNAME}?start=unregword_{cid}"))
    kb.add(types.InlineKeyboardButton("🚀 Начать сейчас", callback_data="wg:startnow"))
    return kb

def handle_word_game_registration(m, payload):
    uid, fname = m.from_user.id, m.from_user.first_name
    try:
        action, cid_str = payload.split("_", 1)
        cid = int(cid_str)
    except: return bot.send_message(m.chat.id, "⚠️ Некорректная ссылка.")

    with state_lock:
        lobby = pending_word_lobbies.get(cid)
        if not lobby or lobby.get("started"): outcome = "closed"
        elif action == "regword":
            outcome = "already" if uid in lobby["players"] else "registered"
            if outcome == "registered": lobby["players"][uid] = fname
        elif action == "unregword":
            outcome = "removed" if lobby["players"].pop(uid, None) is not None else "not_in"
        else: outcome = "unknown"

    if outcome == "closed": bot.send_message(m.chat.id, "⏳ Регистрация закрыта.")
    elif outcome == "already": bot.send_message(m.chat.id, f"Ты уже записан(а), {fname}! Жди начала! 🍀")
    elif outcome == "registered":
        bot.send_message(m.chat.id, f"✅ Готово, {fname}! Жди начала игры! 🍀")
        repost_lobby(cid)
    elif outcome == "removed":
        bot.send_message(m.chat.id, "❌ Вычеркнула тебя из списка.")
        repost_lobby(cid)
    elif outcome == "not_in": bot.send_message(m.chat.id, "Ты и не был(а) записан(а).")

def start_word_game_now(cid):
    with state_lock:
        lobby = pending_word_lobbies.pop(cid, None)
        if not lobby or lobby.get("started"): return
        lobby["started"] = True
        players = dict(lobby["players"])
        reg_msg_id = lobby.get("reg_msg_id")
        seed = lobby.get("seed")

    if reg_msg_id:
        try: bot.edit_message_caption("✅ Регистрация закрыта — игра начинается!", cid, reg_msg_id)
        except:
            try: bot.edit_message_text("✅ Регистрация закрыта — игра начинается!", cid, reg_msg_id)
            except: pass

    if not players:
        try: bot.send_message(cid, "😔 Никто не успел записаться. Игра отменена.")
        except: pass
        return

    if seed:
        norm_seed = normalize_word(seed)
        if not resolve_is_word(seed, norm_seed): seed = random.choice(list(BASE_WORDS)).title()
    else: seed = random.choice(list(BASE_WORDS)).title()

    eff = effective_last_letter(seed)
    with state_lock:
        active_word_games[cid] = {
            "last_word": seed, "next_letter": eff, "used": {normalize_word(seed)},
            "last_move_ts": time.time(), "last_reminder_ts": time.time(),
            "reminder_msg_id": None, "hint_given": False, "moves": 0,
            "players": players, "scores": {uid: 0 for uid in players},
        }

    plist = "\n".join(f"• {get_user_mention(user_id=u, first_name=n)}" for u, n in players.items())
    try:
        bot.send_message(
            cid,
            "━━━━━━━VIBE━━━━━━━\n"
            f"🔤 <b>ИГРА «СЛОВА» НАЧАЛАСЬ!</b>\n\n"
            f"👥 Участники:\n{plist}\n\n"
            f"Первое слово: <b>{html.escape(seed)}</b>\n"
            f"Следующее на букву «{eff.upper()}»\n\n"
            f"⏱ Тайм-аут хода: 3 мин\n"
            f"🔇 Пока идёт игра — Лиза не встревает в чат.\n"
            "━━━━━━━VIBE━━━━━━━",
            parse_mode='HTML'
        )
    except Exception as e: logging.error(f"[WORDS START] {e}", exc_info=True)

# --- AI ЛОГИКА ---
def clean_ai_response(content):
    if not content: return "Мысль потерялась, спроси по-другому..."
    content = re.sub(r"(?si)^.*?thinking process.*?(?:output|option \d+:|final response:|answer:|draft generation:?)\s*", "", content)
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    clean_str = re.sub(r"(?i)^option \d+:\s*", "", content.strip())
    if re.fullmatch(r"[\d\.\-\sE]+", clean_str) and len(clean_str) > 3: return "Цифры одни... давай о чём-то другом."
    if re.search(r"(i cannot|i can't|as an ai|sorry|unable to fulfill|error|я искусственный интеллект)", clean_str, re.IGNORECASE): 
        return "Об этом говорить не буду..."
    clean_str = re.sub(r"^(Лиза|Lisa|Ліза):\s*", "", clean_str, flags=re.IGNORECASE).strip()
    res = clean_str + "." if clean_str and clean_str[-1].isalnum() else clean_str or "Мда..."
    return html.escape(res)

def get_current_key():
    with key_lock: return api_keys[current_key_idx]

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
            try: data = r.json()
            except: data = {}
            if r.status_code in (401, 402, 429) or (isinstance(data.get("error"), dict) and data["error"].get("code") in (401, 402, 429)):
                logging.warning(f"Лимит ключа исчерпан (status={r.status_code}). Переключаю...")
                switch_key()
                attempts += 1
                continue
            if "choices" in data and data["choices"]: return clean_ai_response(data["choices"][0].get("message", {}).get("content", ""))
            else:
                logging.error(f"[AI API ERROR]: {data}")
                break
        except Exception as e: 
            logging.error(f"[AI Exception]: {e}")
            switch_key()
            attempts += 1
            continue
    return "Сервис временно занят."

def is_threat(txt):
    try:
        ans = call_ai([{"role": "user", "content": f"Текст: '{txt}'. Это угроза докса/сватинга? Отвечай THREAT или SAFE."}], 10, 0.0)
        return "THREAT" in ans.upper()
    except: return False

def build_active_scoreboard(game):
    scores = game.get("scores", {})
    players = game.get("players", {})
    if not scores: return "Пока нет очков."
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    lines = [f"{i}. {get_user_mention(user_id=uid, first_name=players.get(uid, 'Игрок'))} — <b>{pts}</b> б." for i, (uid, pts) in enumerate(ordered, 1)]
    return "\n".join(lines)

def process_word_guess(cid, uid, fname, raw_text):
    try:
        raw = raw_text.strip()
        norm = normalize_word(raw)
        with state_lock: game = active_word_games.get(cid)
        if not game or uid not in game["players"]: return

        f_letter, req_letter = first_letter(raw), game["next_letter"]
        known_locally = is_known_word(norm)

        if req_letter and f_letter != req_letter:
            if not known_locally: return
            msg = bot.send_message(cid, f"❌ Нужна буква «{req_letter.upper()}», а не «{f_letter.upper()}».")
            auto_del(msg, 10)
            return

        with state_lock:
            if norm in game["used"]:
                msg = bot.send_message(cid, f"♻️ «{html.escape(raw)}» уже называли.")
                auto_del(msg, 10)
                return

        if not resolve_is_word(raw, norm):
            if req_letter:
                msg = bot.send_message(cid, f"🤔 Не нахожу такого слова. Ещё раз на букву «{req_letter.upper()}».")
                auto_del(msg, 10)
            return

        eff_letter = effective_last_letter(raw)
        with state_lock:
            game = active_word_games.get(cid)
            if not game or uid not in game["players"] or (game["next_letter"] and f_letter != game["next_letter"]) or norm in game["used"]: return
            
            if game.get("reminder_msg_id"):
                try: bot.delete_message(cid, game["reminder_msg_id"])
                except: pass
                game["reminder_msg_id"] = None
                
            game["used"].add(norm)
            game["last_word"], game["next_letter"] = raw, eff_letter
            game["last_move_ts"] = time.time()
            game["last_reminder_ts"] = time.time()
            game["hint_given"] = False
            game["moves"] += 1
            game["scores"][uid] = game["scores"].get(uid, 0) + 1
            
            moves_count = game["moves"]
            
            ldrs = db_get("words_leaders", {})
            ldrs.setdefault(str(uid), {"name": fname, "wins": 0})["wins"] += 1
            db_set("words_leaders", ldrs)

        if moves_count >= 50:
            with state_lock:
                if cid in active_word_games: game_obj = active_word_games.pop(cid)
            end_word_game(cid, game_obj, reason="limit")
            return

        mention = get_user_mention(user_id=uid, first_name=fname)
        bot.send_message(cid, f"🥳 {mention} отгадывает слово и получает +1 балл🔥\nСледующее слово на букву «<b>{eff_letter.upper()}</b>»", parse_mode='HTML')
    except Exception as e: logging.error(f"[WORDS MOVE] {e}", exc_info=True)

def lucky_game_result(cid, uid, fname, msg_id, win, left, emoji):
    try:
        wait_s = DICE_ANIMATION_SECONDS.get(emoji, 4.0) + DELETE_ANIM_DELAY
        time.sleep(wait_s)
        try: bot.delete_message(cid, msg_id)
        except Exception as e:
            if "message to delete not found" not in str(e): logging.error(f"[LUCKY DEL] {e}")
                
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
            with state_lock: rem = int(lucky_limits[uid]["reset_at"] - time.time())
            txt += f"\nПопыток больше нет😔\nНовые через: {max(0, rem//60)}м {max(0, rem%60)}с.\n\nЗато в боте играй без ограничений😉\n🔥 @vibe_247top_bot"
        else:
            txt += f"Осталось попыток: {left}\nСыграем еще?"
            
        msg = bot.send_message(cid, txt, reply_markup=kb, parse_mode='HTML')
        track_and_replace_specific_cmd(cid, uid, "lucky_game", msg)
    except Exception as e: logging.error(f"[LUCKY RESULT] {e}", exc_info=True)
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
                    msg = bot.send_message(cid, txt, parse_mode='HTML', disable_web_page_preview=True)
                    track_and_replace_specific_cmd(cid, uid, "lucky_game", msg)
                    if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))
                    return
                else: lim["left"] = 5
            lim["left"] -= 1
            if lim["left"] == 0: lim["reset_at"] = now + 1800
        
        emoji = random.choice(["🎯", "🎳", "🏀"])
        try: dice = bot.send_dice(cid, emoji=emoji)
        except Exception:
            bot.send_message(cid, "⚠️ Нет прав на кубики.")
            with state_lock:
                if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))
            return
        win = (emoji in ["🎯", "🎳"] and dice.dice.value == 6) or (emoji == "🏀" and dice.dice.value in [4, 5])
        executor.submit(lucky_game_result, cid, uid, fname, dice.message_id, win, lim["left"], emoji)
    except Exception as e:
        logging.error(f"[PLAY LUCKY] {e}", exc_info=True)
        with state_lock:
            if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))

# --- ОБРАБОТЧИКИ КОМАНД ---
@bot.message_handler(commands=['start'])
def cmd_start(m):
    payload = None
    if m.text:
        text_parts = m.text.split(maxsplit=1)
        if len(text_parts) > 1: payload = text_parts[1].strip()

    if payload and (payload.startswith("regword_") or payload.startswith("unregword_")):
        return handle_word_game_registration(m, payload)

    if not check_access(m): return
    if m.chat.type == 'private' and m.from_user.id in BOSSES and m.text and "settings" in m.text:
        msg = bot.reply_to(m, "📢 Выберите группу:", reply_markup=chats_selection_kb())
        return finish_command(m, "start", msg)
    kb = types.InlineKeyboardMarkup(row_width=1).add(types.InlineKeyboardButton("Что ты умеешь?", callback_data="what_can_i_do"))
    if m.chat.type == 'private' and m.from_user.id in BOSSES: kb.add(types.InlineKeyboardButton("⚙️ Настройки", callback_data="open_main_settings"))
    txt = f"Привет, {get_user_mention(m.from_user)}... Я Лиза...\n💭 Чат: https://t.me/+8WZ4kwpAaZ0yZTRi\n🛒 Наш бот: @vibe_247top_bot\n🎁 События: /events"
    msg = bot.send_message(m.chat.id, txt, reply_markup=kb, parse_mode='HTML', disable_web_page_preview=True)
    finish_command(m, "start", msg, ttl=180 if m.chat.type != 'private' else None)

@bot.message_handler(commands=['setting', 'settings'])
def cmd_settings(m):
    if not check_access(m): return
    msg = bot.send_message(m.chat.id, "🎛 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ЛИЗОЙ</b> ✨\n\nНастрой характер и функции бота под свой чат:", reply_markup=main_kb(m.chat.id, m.chat.type == 'private'), parse_mode='HTML')
    finish_command(m, "settings", msg) 

@bot.message_handler(commands=['lucky_game'])
def cmd_lg(m):
    if not check_access(m): return
    uid, cid = m.from_user.id, m.chat.id
    finish_command(m, "lucky_game_cmd")
    with state_lock:
        if (cid, uid) in active_lucky_players:
            return track_and_replace_specific_cmd(cid, uid, "lucky_game", bot.send_message(cid, "Дождись конца игры!"))
        active_lucky_players.add((cid, uid))
    play_lucky_game(cid, uid, m.from_user.first_name)

@bot.message_handler(commands=['leaders_lucky_game'])
def cmd_llg(m):
    if not check_access(m): return
    with state_lock: ldrs = db_get("lucky_leaders", {})
    txt = "━━━━━━━VIBE━━━━━━━\n🎲 <b>РЕЙТИНГ ВЕЗУНЧИКОВ</b>\n\n"
    if not ldrs: txt += "Пока никого нет."
    else: txt += "\n".join(f"{i}. {get_user_mention(user_id=int(u), first_name=info.get('name'))} — <b>{info.get('wins',0)}</b>" for i, (u, info) in enumerate(sorted(ldrs.items(), key=lambda x: x[1].get("wins",0), reverse=True)[:10], 1))
    txt += "\n━━━━━━━VIBE━━━━━━━"
    msg = bot.send_message(m.chat.id, txt, parse_mode='HTML')
    finish_command(m, "leaders_lucky_game", msg, ttl=120)

@bot.message_handler(commands=['events'])
def cmd_events(m):
    if not check_access(m): return
    evs = db_get("events", [])
    evs = evs["events"] if isinstance(evs, dict) and "events" in evs else evs
    txt = "Пока пусто!" if not evs else "📅 <b>БЛИЖАЙШИЕ СОБЫТИЯ:</b>\n\n" + "\n".join(f"🔸 <b>{html.escape(str(e.get('date')))}</b> — {html.escape(str(e.get('info')))}" for e in evs)
    msg = bot.send_message(m.chat.id, txt, parse_mode='HTML')
    finish_command(m, "events", msg, ttl=180)

@bot.message_handler(commands=['start_game_safe'])
def cmd_sgs(m):
    if not check_access(m): return
    caller_rank = get_admin_rank(m.chat.id, m.from_user.id)
    if caller_rank < 1 and m.from_user.id not in BOSSES: return reply_no_rights(m)
    with state_lock:
        if m.chat.id in active_safes:
            msg = bot.send_message(m.chat.id, "⚠️ Сейф уже активирован.")
            return finish_command(m, "start_game_safe", msg, ttl=30)
        active_safes[m.chat.id] = {"code": f"{random.randint(0,999):03d}", "hint_given": False, "gid": time.time()}
    msg = bot.send_message(m.chat.id, "🔐 <b>ИГРА НАЧАЛАСЬ</b>\n\nБронированный сейф заблокирован на ХХХ.\nПишите код в чат!\nТоп: /leaders_safe_game", parse_mode='HTML')
    finish_command(m, "start_game_safe", msg)

@bot.message_handler(commands=['leaders_safe_game'])
def cmd_lsg(m):
    if not check_access(m): return
    msg = bot.send_message(m.chat.id, format_safe_leaderboard(), parse_mode='HTML')
    finish_command(m, "leaders_safe_game", msg, ttl=120)

@bot.message_handler(commands=['start_words_game'])
def cmd_start_words(m):
    if not check_access(m): return
    caller_rank = get_admin_rank(m.chat.id, m.from_user.id)
    if caller_rank < 1 and m.from_user.id not in BOSSES: return reply_no_rights(m)
    cid = m.chat.id
    with state_lock:
        if cid in active_word_games:
            msg = bot.send_message(cid, "⚠️ Игра «Слова» уже идёт.")
            return finish_command(m, "start_words_game", msg, ttl=20)
        if cid in pending_word_lobbies:
            msg = bot.send_message(cid, "⚠️ Регистрация на игру уже открыта.")
            return finish_command(m, "start_words_game", msg, ttl=20)

    parts = m.text.split(maxsplit=1)
    seed = parts[1].strip() if len(parts) > 1 else None
    if seed and not CYRILLIC_WORD_RE.match(seed):
        msg = bot.send_message(cid, "⚠️ Пример: /start_words_game слово")
        return finish_command(m, "start_words_game", msg, ttl=20)

    with state_lock:
        pending_word_lobbies[cid] = {
            "creator_id": m.from_user.id, "seed": seed, "players": {}, "reg_msg_id": None,
            "end_time": time.time() + REGISTRATION_SECONDS, "next_repost": time.time() + 60, "started": False,
        }
        lobby_snapshot = dict(pending_word_lobbies[cid])

    sent = send_lobby_msg(cid, lobby_snapshot)
    with state_lock:
        if cid in pending_word_lobbies: pending_word_lobbies[cid]["reg_msg_id"] = sent.message_id if sent else None
    finish_command(m, "start_words_game")

@bot.message_handler(commands=['stop_words_game'])
def cmd_stop_words(m):
    if not check_access(m): return
    caller_rank = get_admin_rank(m.chat.id, m.from_user.id)
    if caller_rank < 1 and m.from_user.id not in BOSSES: return reply_no_rights(m)
    cid = m.chat.id
    with state_lock:
        game = active_word_games.pop(cid, None)
        lobby = pending_word_lobbies.pop(cid, None)

    if lobby and lobby.get("reg_msg_id"):
        try: bot.edit_message_caption("🛑 Регистрация на игру отменена.", cid, lobby["reg_msg_id"])
        except:
            try: bot.edit_message_text("🛑 Регистрация на игру отменена.", cid, lobby["reg_msg_id"])
            except: pass

    if not game and not lobby:
        msg = bot.send_message(cid, "Игра сейчас не идёт.")
        return finish_command(m, "stop_words_game", msg, ttl=20)
    if lobby and not game:
        msg = bot.send_message(cid, "🛑 Регистрация на игру отменена.")
        return finish_command(m, "stop_words_game", msg, ttl=60)
    msg = bot.send_message(cid, f"🛑 Остановлено. Названо слов: {game['moves']}. Последнее: {html.escape(game['last_word'] or '—')}\n🔊 Лиза снова на связи.")
    finish_command(m, "stop_words_game", msg, ttl=60)

@bot.message_handler(commands=['leaders_words_game'])
def cmd_leaders_words(m):
    if not check_access(m): return
    msg = bot.send_message(m.chat.id, format_words_leaderboard(), parse_mode='HTML')
    finish_command(m, "leaders_words_game", msg, ttl=120)

@bot.message_handler(commands=['words_status'])
def cmd_words_status(m):
    if not check_access(m): return
    with state_lock:
        game = active_word_games.get(m.chat.id)
        lobby = pending_word_lobbies.get(m.chat.id)
    if game:
        msg = bot.send_message(m.chat.id, f"🏙 Названо слов: {game['moves']}. Нужна буква «<b>{game['next_letter'].upper()}</b>».", parse_mode='HTML')
    elif lobby:
        remaining = max(0, int(lobby["end_time"] - time.time()))
        msg = bot.send_message(m.chat.id, f"📝 Идёт регистрация: {len(lobby['players'])} участник(ов). До старта ~{remaining//60}м {remaining%60}с.")
    else: msg = bot.send_message(m.chat.id, "Игра не идёт. /start_words_game")
    finish_command(m, "words_status", msg, ttl=30)

@bot.message_handler(commands=['farm', 'фарм'])
def cmd_farm(m):
    if not check_access(m): return
    process_farm_command(m)

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
                elif cmd in ["/farm", "/фарм"]: cmd_farm(fake)
            return bot.answer_callback_query(c.id)

        if not is_pv and str(cid).replace("-100", "") not in ALLOWED_GROUPS_RAW and cid not in ALLOWED_GROUPS:
            return bot.answer_callback_query(c.id, "⛔ Неразрешенный чат.", show_alert=True)

        if action == "lucky" and parts[1] == "again":
            target_uid = int(parts[2])
            if uid != target_uid: return bot.answer_callback_query(c.id, "⛔ Не твоя игра!", show_alert=True)
            try: bot.delete_message(cid, c.message.message_id)
            except Exception as e:
                if "message to delete not found" not in str(e): logging.error(f"[LUCKY AGAIN DEL] {e}")
            bot.answer_callback_query(c.id)
            return play_lucky_game(cid, uid, c.from_user.first_name)

        if action == "wg" and len(parts) >= 2 and parts[1] == "startnow":
            with state_lock: lobby = pending_word_lobbies.get(cid)
            if not lobby: return bot.answer_callback_query(c.id, "Лобби не найдено.", show_alert=True)
            if uid != lobby["creator_id"] and uid not in BOSSES and get_admin_rank(cid, uid) < 3:
                return bot.answer_callback_query(c.id, "⛔ Только создатель или админ могут начать игру раньше.", show_alert=True)
            bot.answer_callback_query(c.id, "🚀 Запускаю игру!")
            executor.submit(start_word_game_now, cid)
            return

        if d == "what_can_i_do":
            bot.answer_callback_query(c.id)
            return bot.edit_message_text("🔹 Общаюсь\n🔹 Слежу за матом\n🔹 Автопостинг\n🔹 Игры (/lucky_game, сейф, /start_words_game)\n🔹 Фарм травки (/farm)", cid, c.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data="back_to_start")))
            
        if d == "back_to_start":
            bot.answer_callback_query(c.id)
            kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Что ты умеешь?", callback_data="what_can_i_do"))
            if uid in BOSSES: kb.add(types.InlineKeyboardButton("⚙️ Настройки", callback_data="open_main_settings"))
            return bot.edit_message_text(f"Привет, {get_user_mention(c.from_user)}... Я Лиза.\n💭Чат: https://t.me/+8WZ4kwpAaZ0yZTRi\n🛒Бот: @vibe_247top_bot", cid, c.message.message_id, reply_markup=kb, parse_mode='HTML', disable_web_page_preview=True)

        if d == "open_main_settings":
            if uid not in BOSSES: return bot.answer_callback_query(c.id, "⛔ У вас нет прав.", show_alert=True)
            bot.answer_callback_query(c.id)
            return bot.edit_message_text("🎛 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ЛИЗОЙ</b> ✨\n\nНастрой характер и функции бота под свой чат:", cid, c.message.message_id, reply_markup=main_kb(cid, is_pv), parse_mode='HTML')

        if d == "m:toggle_intervene":
            set_v(cid, "intervene", not get_v(cid, "intervene", True))
            bot.answer_callback_query(c.id)
            return bot.edit_message_reply_markup(cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))
            
        if d == "m:toggle_sys":
            set_v(cid, "del_sys", not get_v(cid, "del_sys", False))
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
            except: return bot.answer_callback_query(c.id, "⚠️ Нажми Start в ЛС с ботом.", show_alert=True)
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
                with state_lock: active_fsm[uid] = {"action": "editing", "pid": pid}
                bot.answer_callback_query(c.id)
                return bot.edit_message_text(post_text_view(pid), cid, c.message.message_id, reply_markup=post_settings_kb(pid))
            elif sub == "create":
                c_str = parts[2]
                with state_lock:
                    data = db_get("autopost", {"posts": []})
                    new_id = str(int(time.time()))
                    data["posts"].append({
                        "id": new_id, "name": f"Пост #{len([p for p in data['posts'] if str(p.get('chat_id'))==c_str])+1}",
                        "enabled": False, "interval": 3600, "daily_time": None, "start_date": None,
                        "auto_delete_prev": False, "last_msg_id": None, "text": "Текст...",
                        "photo": None, "buttons": [], "last_post": 0, "chat_id": int(c_str) if c_str.lstrip('-').isdigit() else c_str
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
                    "interval": "Отправьте интервал (например: 45м, 3ч):", "time": "Отправьте время (например: 14:30):",
                    "date": "Отправьте дату (например: 2026-10-01):", "text": "📝 Отправьте новый текст поста.",
                    "buttons": "Отправь кнопки:\nТекст - https://ссылка.com\nИли: Текст - cmd:/lucky_game", "photo": "🖼 Отправьте фото в этот чат."
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
                        return bot.answer_callback_query(c.id, "⚠️ Ошибка", show_alert=True)
            elif sub == "preview":
                pid = parts[2]
                bot.answer_callback_query(c.id)
                with state_lock: post = next((p for p in db_get("autopost", {"posts": []}).get("posts", []) if p["id"] == pid), None)
                if post:
                    mk = build_post_user_kb(post)
                    bot.send_message(cid, "👁 <b>Предпросмотр:</b>", parse_mode='HTML')
                    if post.get("photo"): bot.send_photo(cid, post.get("photo"), caption=post.get("text", ""), reply_markup=mk, parse_mode='HTML')
                    else: bot.send_message(cid, post.get("text", ""), reply_markup=mk, parse_mode='HTML')
                    return bot.send_message(cid, "Вот так выглядит пост.", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
        bot.answer_callback_query(c.id)
    except Exception as e: logging.error(f"[CALLBACK ERROR] {e}", exc_info=True)

@bot.message_handler(content_types=['photo'])
def on_photo(m):
    try:
        if not check_access(m): return
        uid = m.from_user.id
        if m.chat.type == 'private' and uid in BOSSES:
            with state_lock: fsm = active_fsm.get(uid)
            if fsm and fsm.get("action") == "photo":
                pid = fsm["pid"]
                with state_lock:
                    active_fsm.pop(uid, None)
                    data = db_get("autopost", {"posts": []})
                    for p in data["posts"]:
                        if p["id"] == pid: p["photo"] = m.photo[-1].file_id
                    db_set("autopost", data)
                bot.reply_to(m, "🖼 Фото сохранено", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
        record_xp_and_stats(m)
    except Exception as e: logging.error(f"[ON PHOTO] {e}", exc_info=True)

@bot.message_handler(func=lambda m: True)
def text_handler(m):
    try:
        if not check_access(m): return
        uid, cid, t = m.from_user.id, m.chat.id, m.text.strip()
        fname, uname = m.from_user.first_name, m.from_user.username
        boss = uid in BOSSES

        t_lower = t.lower()
        parts = t.split()
        cmd = parts[0].lower() if parts else ""
        
        # --- СИСТЕМА УВАЖЕНИЯ ---
        if t == "+" and m.reply_to_message:
            target_uid = m.reply_to_message.from_user.id
            if target_uid == uid:
                auto_del(bot.reply_to(m, "Себе уважение оказывать нельзя! 😉"), 5)
                try: bot.delete_message(cid, m.message_id)
                except: pass
                return
            if m.reply_to_message.from_user.is_bot:
                try: bot.delete_message(cid, m.message_id)
                except: pass
                return 
                
            with state_lock:
                users = db_get("users_data", {})
                u_giver = users.setdefault(str(uid), {"xp": 0, "msgs": 0, "name": fname, "uname": uname, "first_seen": time.time(), "respects": 0, "given_respects": 0, "respect_reset": 0, "weed": 0, "last_farm": 0})
                u_target = users.setdefault(str(target_uid), {"xp": 0, "msgs": 0, "name": m.reply_to_message.from_user.first_name, "uname": m.reply_to_message.from_user.username, "first_seen": time.time(), "respects": 0, "given_respects": 0, "respect_reset": 0, "weed": 0, "last_farm": 0})
                
                now = time.time()
                if now > u_giver.get("respect_reset", 0):
                    u_giver["given_respects"] = 0
                    n_dt = datetime.now(KYIV_TZ)
                    n_mid = (n_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                    u_giver["respect_reset"] = n_mid.timestamp()
                    
                if u_giver.get("given_respects", 0) >= 3:
                    reset_time = datetime.fromtimestamp(u_giver["respect_reset"], KYIV_TZ).strftime('%H:%M')
                    msg = bot.send_message(m.chat.id, f"🛑 {get_user_mention(m.from_user)}, лимит исчерпан! Вы уже передали 3 очка уважения сегодня.\nЛимит обновится в {reset_time}.", parse_mode='HTML')
                    auto_del(msg, 10)
                else:
                    u_giver["given_respects"] = u_giver.get("given_respects", 0) + 1
                    u_target["respects"] = u_target.get("respects", 0) + 1
                    db_set("users_data", users)
                    msg = bot.send_message(m.chat.id, f"🤝 {get_user_mention(m.from_user)} выражает уважение {get_user_mention(m.reply_to_message.from_user)}!\n<i>(Осталось на сегодня: {3 - u_giver['given_respects']})</i>", parse_mode='HTML')
                    auto_del(msg, 15)
            try: bot.delete_message(cid, m.message_id)
            except: pass
            return

        # --- СИСТЕМА АДМИНИСТРИРОВАНИЯ ---
        if cmd in ["повысить", "понизить", "разжаловать", "пред", "/warn", "снятьпред", "/unwarn", "мут", "/mute", "снятьмут", "/unmute", "бан", "/ban", "снятьбан", "/unban", "кик", "/kick", "кто", "админы", "варны", "/warns", "права", "/rank_perms", "rank_perms"]:
            if cmd == "кто" and len(parts) > 1 and parts[1].lower() == "админ": cmd = "кто админ"
            
            if cmd in ["кто админ", "админы"]:
                admins = db_get("chat_admins", {}).get(str(cid), {})
                try:
                    for adm in bot.get_chat_administrators(cid):
                        if adm.status == 'creator' and str(adm.user.id) not in admins:
                            set_admin_rank(cid, adm.user.id, 5)
                            admins[str(adm.user.id)] = 5
                except: pass
                
                users_db = db_get("users_data", {})
                grouped = {5: [], 4: [], 3: [], 2: [], 1: []}
                for a_uid, rank in admins.items():
                    if rank in grouped:
                        name = users_db.get(str(a_uid), {}).get("name", "Неизвестный")
                        grouped[rank].append(get_user_mention(user_id=int(a_uid), first_name=name))
                        
                txt = "━━━━━━━VIBE━━━━━━━\n👑 <b>АДМИНИСТРАЦИЯ ЧАТА</b>\n\n"
                for r in sorted(ADMIN_RANKS.keys(), reverse=True):
                    if grouped[r]:
                        perms = get_rank_permissions(cid, r)
                        active_perms = [PERM_NAMES[p] for p, v in perms.items() if v]
                        perms_str = f" <i>({', '.join(active_perms)})</i>" if active_perms else ""
                        txt += f"<b>{ADMIN_RANKS[r]}</b>{perms_str}\n" + "\n".join(f"• {u}" for u in grouped[r]) + "\n\n"
                txt += "━━━━━━━VIBE━━━━━━━"
                return finish_command(m, "who_admin", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)

            elif cmd in ["/rank_perms", "rank_perms"]:
                if len(parts) < 2 or not parts[1].isdigit():
                    return finish_command(m, "rank_perms_err", bot.send_message(cid, "⚠️ Укажите ранг (1-5), например: /rank_perms 3"), ttl=10)
                r_num = int(parts[1])
                if r_num < 1 or r_num > 5:
                    return finish_command(m, "rank_perms_err", bot.send_message(cid, "⚠️ Ранг должен быть от 1 до 5."), ttl=10)
                
                perms = get_rank_permissions(cid, r_num)
                txt = f"⚙️ <b>ПРАВА РАНГА {r_num} ({ADMIN_RANKS[r_num]}):</b>\n\n"
                for p_key, p_name in PERM_NAMES.items():
                    status = "✅" if perms.get(p_key, False) else "❌"
                    txt += f"{status} <code>{p_key}</code> — {p_name}\n"
                return finish_command(m, "rank_perms", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

            elif cmd == "права":
                if not boss: return reply_no_rights(m)
                if len(parts) < 4:
                    return finish_command(m, "права_err", bot.send_message(cid, "⚠️ Формат: права <ранг> <permission> вкл|выкл\nПример: права 2 can_ban вкл"), ttl=15)
                r_num_str, p_key, p_val_str = parts[1], parts[2].lower(), parts[3].lower()
                if not r_num_str.isdigit() or not (1 <= int(r_num_str) <= 5):
                    return finish_command(m, "права_err", bot.send_message(cid, "⚠️ Ранг должен быть от 1 до 5."), ttl=10)
                if p_key not in PERM_NAMES:
                    return finish_command(m, "права_err", bot.send_message(cid, f"⚠️ Неизвестное право: {p_key}.\nДоступные: {', '.join(PERM_NAMES.keys())}"), ttl=15)
                if p_val_str not in ["вкл", "выкл"]:
                    return finish_command(m, "права_err", bot.send_message(cid, "⚠️ Значение должно быть 'вкл' или 'выкл'."), ttl=10)
                    
                r_num = int(r_num_str)
                new_val = (p_val_str == "вкл")
                set_rank_permission(cid, r_num, p_key, new_val)
                
                perms = get_rank_permissions(cid, r_num)
                txt = f"✅ <b>Права обновлены для ранга {r_num} ({ADMIN_RANKS[r_num]})</b>\n\n"
                for pk, p_name in PERM_NAMES.items():
                    status = "✅" if perms.get(pk, False) else "❌"
                    txt += f"{status} <code>{pk}</code> — {p_name}\n"
                return finish_command(m, "права_ok", bot.send_message(cid, txt, parse_mode="HTML"))

            elif cmd in CMD_PERM_MAP or cmd in ["варны", "/warns"]:
                req_perm = CMD_PERM_MAP.get(cmd)
                caller_rank = get_admin_rank(cid, uid)
                
                if req_perm and not has_permission(cid, uid, req_perm):
                    return reply_no_rights(m)
                    
                target_uid, target_name, args = extract_target_and_args(m, parts)
                
                if not target_uid:
                    if cmd in ["варны", "/warns"]:
                        target_uid, target_name = uid, fname
                    else:
                        return finish_command(m, "no_target", bot.send_message(cid, "Укажите пользователя (реплай или @юз/ID)"), ttl=10)
                        
                target_cur_rank = get_admin_rank(cid, target_uid)
                
                if cmd not in ["варны", "/warns"]:
                    if target_uid == BOT_ID or (target_uid == uid and cmd not in ["снятьмут", "/unmute", "снятьбан", "/unban"]):
                        msg = bot.send_message(cid, "Это действие нельзя применить к данной цели.")
                        return finish_command(m, "invalid_target", msg, ttl=5)
                        
                    if not boss:
                        if target_uid in BOSSES or caller_rank <= target_cur_rank:
                            return reply_no_rights(m)

                if cmd in ["повысить", "понизить", "разжаловать"]:
                    if cmd == "разжаловать": new_rank = 0
                    else:
                        if args and args[0].isdigit(): new_rank = int(args[0])
                        else: new_rank = target_cur_rank + 1 if cmd == "повысить" else target_cur_rank - 1
                            
                    if new_rank > 5: new_rank = 5
                    if new_rank < 0: new_rank = 0
                    
                    if not boss:
                        if caller_rank <= new_rank and new_rank != 0: return reply_no_rights(m)
                            
                    set_admin_rank(cid, target_uid, new_rank)
                    if new_rank == 0: txt = f"📉 {get_user_mention(user_id=target_uid, first_name=target_name)} разжалован до обычного участника."
                    else:
                        rank_name = ADMIN_RANKS.get(new_rank, f"{new_rank} РАНГ")
                        act = "повышен" if new_rank > target_cur_rank else "понижен"
                        txt = f"📈 {get_user_mention(user_id=target_uid, first_name=target_name)} {act} до должности:\n<b>{rank_name}</b>"
                    return finish_command(m, "admin_change", bot.send_message(cid, txt, parse_mode="HTML"))
                    
                elif cmd in ["бан", "/ban"]:
                    time_arg = args[0] if args else ""
                    dur_secs, parsed = parse_duration(time_arg)
                    if parsed:
                        reason = " ".join(args[1:]) if len(args) > 1 else "Не указана"
                        time_str = time_arg
                    else:
                        reason = " ".join(args) if args else "Не указана"
                        dur_secs = 0
                        time_str = "навсегда"
                        
                    until = int(time.time() + dur_secs) if dur_secs > 0 else 0
                    try:
                        bot.ban_chat_member(cid, target_uid, until_date=until)
                        mention_admin = get_user_mention(user_id=uid, first_name=fname)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = f"🔨 {mention_admin} забанил {mention_target} — {time_str}\nПричина: {reason}"
                        log_moderation_action(m.chat.title or str(cid), txt)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "ban", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["снятьбан", "/unban"]:
                    try:
                        bot.unban_chat_member(cid, target_uid, only_if_banned=True)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = f"✅ Снят бан с {mention_target}"
                        log_moderation_action(m.chat.title or str(cid), txt)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "unban", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["кик", "/kick"]:
                    reason = " ".join(args) if args else "Не указана"
                    try:
                        bot.ban_chat_member(cid, target_uid, until_date=0)
                        bot.unban_chat_member(cid, target_uid, only_if_banned=True)
                        mention_admin = get_user_mention(user_id=uid, first_name=fname)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = f"👢 {mention_admin} кикнул {mention_target}\nПричина: {reason}"
                        log_moderation_action(m.chat.title or str(cid), txt)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "kick", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["мут", "/mute"]:
                    time_arg = args[0] if args else ""
                    dur_secs, parsed = parse_duration(time_arg)
                    if parsed:
                        reason = " ".join(args[1:]) if len(args) > 1 else "Не указана"
                        time_str = time_arg
                    else:
                        reason = " ".join(args) if args else "Не указана"
                        dur_secs = 0
                        time_str = "навсегда"
                        
                    until = int(time.time() + dur_secs) if dur_secs > 0 else 0
                    try:
                        bot.restrict_chat_member(cid, target_uid, until_date=until, permissions=ChatPermissions(can_send_messages=False))
                        mention_admin = get_user_mention(user_id=uid, first_name=fname)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = f"🔇 {mention_admin} выдал Мут {mention_target} на {time_str}\nПричина: {reason}"
                        log_moderation_action(m.chat.title or str(cid), txt)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "mute", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["снятьмут", "/unmute"]:
                    try:
                        bot.restrict_chat_member(cid, target_uid, permissions=ChatPermissions(
                            can_send_messages=True, can_send_media_messages=True, 
                            can_send_other_messages=True, can_add_web_page_previews=True))
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = f"✅ Снят мут с {mention_target}"
                        log_moderation_action(m.chat.title or str(cid), txt)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "unmute", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["пред", "/warn"]:
                    warn_count = 1
                    if args and args[0].isdigit():
                        warn_count = int(args[0])
                        reason = " ".join(args[1:]) or "Не указана"
                    else:
                        reason = " ".join(args) or "Не указана"
                    issue_warn(cid, m.chat.title, target_uid, target_name, uid, fname, reason, m, warn_count)
                    return

                elif cmd in ["снятьпред", "/unwarn"]:
                    with state_lock:
                        db = db_get("chat_warns", {})
                        cw = db.setdefault(str(cid), {})
                        uw = cw.setdefault(str(target_uid), {"count": 0, "history": []})
                        if uw["count"] > 0:
                            uw["count"] -= 1
                            if uw["history"]: uw["history"].pop()
                            count = uw["count"]
                            max_warns = get_v(cid, "max_warns", 3)
                            db_set("chat_warns", db)
                            mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                            txt = f"✅ Снято предупреждение с {mention_target} ({count}/{max_warns})"
                            log_moderation_action(m.chat.title or str(cid), txt)
                        else:
                            txt = "У пользователя нет предупреждений."
                    return finish_command(m, "unwarn", bot.send_message(cid, txt, parse_mode="HTML"))
                    
                elif cmd in ["варны", "/warns"]:
                    with state_lock:
                        db = db_get("chat_warns", {})
                        cw = db.get(str(cid), {})
                        uw = cw.get(str(target_uid), {"count": 0, "history": []})
                        count = uw.get("count", 0)
                        history = uw.get("history", [])
                    
                    max_warns = get_v(cid, "max_warns", 3)
                    mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                    
                    txt = f"⚠️ <b>Предупреждения</b> {mention_target} ({count}/{max_warns}):\n"
                    if history:
                        for idx, item in enumerate(history[-5:], 1):
                            dt_str = datetime.fromtimestamp(item["date"], KYIV_TZ).strftime('%d.%m.%Y %H:%M')
                            txt += f"{idx}. {item['reason']} (выдал {html.escape(item['by_name'])} {dt_str})\n"
                    else:
                        txt += "\nИстория пуста."
                    return finish_command(m, "warns", bot.send_message(cid, txt, parse_mode="HTML"))

        # --- ОБЩИЕ КОМАНДЫ ---
        if t_lower == "мой профиль":
            finish_command(m, "my_profile")
            return handle_profile_request(m, uid, m.from_user)
            
        if t_lower in ["фарм", "/farm", "/фарм"]:
            return process_farm_command(m)

        if t_lower.startswith("кто ты"):
            if m.reply_to_message:
                finish_command(m, "who_are_you")
                return handle_profile_request(m, m.reply_to_message.from_user.id, m.reply_to_message.from_user)
            target_uid, _, _ = extract_target_and_args(m, parts)
            if target_uid:
                finish_command(m, "who_are_you")
                return handle_profile_request(m, target_uid)

        record_xp_and_stats(m)

        if m.chat.type == 'private' and boss:
            with state_lock: fsm = active_fsm.get(uid)
            if fsm:
                action, pid = fsm.get("action"), fsm.get("pid")
                with state_lock: active_fsm.pop(uid, None)
                
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
                                except: pass
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

        with state_lock: is_safe_active = cid in active_safes
        if is_safe_active and re.fullmatch(r'\d{3}', t):
            with state_lock:
                if cid in active_safes and t == active_safes[cid]["code"]:
                    del active_safes[cid]
                    ldrs = db_get("safe_leaders", {})
                    ldrs.setdefault(str(uid), {"name": m.from_user.first_name, "wins": 0})["wins"] += 1
                    db_set("safe_leaders", ldrs)
                    msg = bot.send_message(cid, f"🎉 СЕЙФ ВЗЛОМАН\nМастер {get_user_mention(m.from_user)} подобрал код: {t}", parse_mode='HTML')
                    auto_del(msg, 180)
            return

        with state_lock: is_words_active = cid in active_word_games
        if is_words_active and CYRILLIC_WORD_RE.match(t):
            executor.submit(process_word_guess, cid, uid, m.from_user.first_name, t)

        if m.chat.type in ['group', 'supergroup'] and not boss:
            if any(bad_word in t_lower for bad_word in MUTES):
                try: bot.delete_message(cid, m.message_id)
                except: pass
                issue_warn(cid, m.chat.title, uid, fname, BOT_ID, "Автомодератор", "Автомодерация: нецензурная лексика", None)
                return

            def threat_check():
                try:
                    if is_threat(m.text):
                        until = int(time.time()) + 300
                        bot.restrict_chat_member(cid, uid, until_date=until, permissions=ChatPermissions(can_send_messages=False))
                        mention = get_user_mention(m.from_user)
                        bosses_ping = " ".join([f"<a href='tg://user?id={b}'>⚠️</a>" for b in BOSSES])
                        alert_msg = f"{bosses_ping} 🛡 Обнаружена угроза от {mention}. Пользователь автоматически заглушен на 5 минут для проверки."
                        bot.send_message(cid, alert_msg, parse_mode='HTML')
                except Exception as e: logging.error(f"[THREAT ACTION] {e}", exc_info=True)
            executor.submit(threat_check)

        direct = m.chat.type == 'private' or (m.reply_to_message and m.reply_to_message.from_user.id == BOT_ID) or "лиза" in t_lower or f"@{BOT_USER}" in t_lower
        if direct or any(w in t_lower for w in CONFL):
            with state_lock: cities_running = cid in active_word_games
            if cities_running: return  
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
        try: bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except KeyboardInterrupt: break
        except Exception as e:
            logging.error(f"Сбой связи: {e}", exc_info=True)
            time.sleep(5)
