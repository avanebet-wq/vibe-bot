import os, re, time, random, requests, html, threading, logging
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
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is alive!")
        def log_message(self, format, *args): pass
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

if not TOKEN or not OPENROUTER_KEY:
    logging.critical("СЕКРЕТЫ НЕ НАЙДЕНЫ!")
    exit(1)

bot = telebot.TeleBot(TOKEN)
ME = bot.get_me()
BOT_ID, BOT_USER = ME.id, (ME.username or "").lower()
executor = ThreadPoolExecutor(max_workers=10)

# --- СОСТОЯНИЯ ---
active_safes, lucky_limits, active_lucky_players = {}, {}, set()
untrusted_warned, last_command_messages, messages_to_delete = set(), {}, []
mem_lock = threading.Lock()

waiting_autopost_text, waiting_autopost_photo = set(), set()
waiting_autopost_time, waiting_autopost_date = set(), set()
waiting_autopost_interval, waiting_autopost_buttons = set(), set()
active_editing_post = {}

# --- УТИЛИТЫ ---
def get_v(cid, k, d=40): return db_get("settings", {}).get(str(cid), {}).get(k, d)
def set_v(cid, k, val):
    s = db_get("settings", {})
    s.setdefault(str(cid), {"freq": 40, "anger": 40, "intervene": True})[k] = val
    db_set("settings", s)

def get_user_mention(user_obj=None, user_id=None, first_name=None):
    if user_obj: user_id, first_name = user_obj.id, user_obj.first_name
    return f'<a href="tg://user?id={user_id}">{html.escape(str(first_name or "User"))}</a>' if user_id else html.escape(str(first_name or "User"))

def format_safe_leaderboard():
    ldrs = db_get("safe_leaders", {})
    if not ldrs: return "🏆 Рейтинг взломщиков сейфа пока пуст."
    txt = "🏆 Рейтинг взломщиков сейфа VIBE\n\n"
    for i, (uid_str, uinfo) in enumerate(sorted(ldrs.items(), key=lambda x: x[1].get("wins", 0), reverse=True)[:10], 1):
        m_icon = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else "▫️"
        txt += f"{m_icon} {i}. {get_user_mention(user_id=int(uid_str), first_name=uinfo.get('name'))} — <b>{uinfo.get('wins', 0)}</b> побед\n"
    return txt.strip()

def parse_interval_input(text):
    match = re.match(r'^(\d+)([дчм])$', text.lower().strip())
    if not match: return None
    val, unit = int(match.group(1)), match.group(2)
    return val * 60 if unit == 'м' else (val * 3600 if unit == 'ч' else val * 86400)

def track_and_replace_specific_cmd(chat_id, user_id, cmd_name, new_msg):
    if not new_msg: return
    key = (chat_id, user_id, cmd_name)
    if key in last_command_messages:
        try: bot.delete_message(chat_id, last_command_messages[key])
        except: pass
    last_command_messages[key] = new_msg.message_id

def register_chat(chat):
    if chat.type in ['group', 'supergroup', 'channel']:
        cache = db_get("chats_cache", {"-1004374303475": "Основная VIBE", "-1003514059820": "Вторая группа"})
        cid_str = str(chat.id)
        if cache.get(cid_str) != (chat.title or f"Чат {cid_str}"):
            cache[cid_str] = chat.title or f"Чат {cid_str}"
            db_set("chats_cache", cache)

def check_access(m):
    uid = m.from_user.id if m.from_user else 0
    if m.chat.type == 'private':
        if uid not in BOSSES:
            try: bot.reply_to(m, DENIED_MSG, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔗 Разработчик", url="https://t.me/VER_CIDE")))
            except: pass
            return False
        return True
    register_chat(m.chat)
    if str(m.chat.id).replace("-100", "").replace("-", "") not in ALLOWED_GROUPS_RAW and m.chat.id not in ALLOWED_GROUPS:
        try: bot.reply_to(m, DENIED_MSG)
        except: pass
        return False
    return True

def auto_del(msg, ttl=180):
    if msg:
        with mem_lock: messages_to_delete.append({"cid": msg.chat.id, "mid": msg.message_id, "time": time.time() + ttl})

def cleanup_worker():
    while True:
        time.sleep(5)
        now = time.time()
        with mem_lock:
            remaining = []
            for item in messages_to_delete:
                if now >= item["time"]:
                    try: bot.delete_message(item["cid"], item["mid"])
                    except: pass
                else: remaining.append(item)
            messages_to_delete[:] = remaining
threading.Thread(target=cleanup_worker, daemon=True).start()

# --- AI ЛОГИКА ---
def clean_ai_response(content):
    if not content: return "С радостью помогу, но фильтры ограничивают..."
    content = re.sub(r"(?si)^.*?thinking process.*?(?:output|option \d+:|final response:|answer:|draft generation:?)\s*", "", content)
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    clean_str = re.sub(r"(?i)^option \d+:\s*", "", content.strip())
    if re.fullmatch(r"[\d\.\-\sE]+", clean_str) and len(clean_str) > 3: return "Фильтры ограничивают ответ..."
    if re.search(r"(i cannot|i can't|as an ai|sorry|unable to fulfill|error|я искусственный интеллект)", clean_str, re.IGNORECASE): return "Фильтры ограничивают ответ..."
    clean_str = re.sub(r"^(Лиза|Lisa|Ліза):\s*", "", clean_str, flags=re.IGNORECASE).strip()
    return clean_str + "." if clean_str and clean_str[-1].isalnum() else clean_str or "Не могу ответить."

def call_ai(messages, max_tokens=300, temp=0.5):
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_KEY}"}, json={"model": AI_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temp}, timeout=20)
        data = r.json()
        if "choices" in data and data["choices"]: return clean_ai_response(data["choices"][0].get("message", {}).get("content", ""))
    except Exception as e: logging.error(f"[AI] {e}")
    return "Сервис временно занят."

def is_threat(txt):
    try: return "THREAT" in call_ai([{"role": "user", "content": f"Текст: '{txt}'. Это угроза докса/сватинга? Отвечай THREAT или SAFE."}], 10, 0.0).upper()
    except: return False

# --- КЛАВИАТУРЫ ---
def main_kb(cid, is_pv=False):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(f"⚡ Вмешательство: {'Вкл' if get_v(cid, 'intervene', True) else 'Выкл'}", callback_data="m_toggle_intervene"),
        types.InlineKeyboardButton(f"📊 Частота: {get_v(cid, 'freq')}%", callback_data="m_freq"),
        types.InlineKeyboardButton(f"🔥 Строгость: {get_v(cid, 'anger')}%", callback_data="m_anger")
    )
    kb.add(types.InlineKeyboardButton("📢 Автопостинг", callback_data="m_autopost_list") if is_pv else types.InlineKeyboardButton("⚙️ Настройки группы", callback_data="to_group_settings"))
    if is_pv: kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_start"))
    return kb

def chats_selection_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for cid, cname in db_get("chats_cache", {}).items(): kb.add(types.InlineKeyboardButton(f"📢 {cname}", callback_data=f"ap_chat_{cid}"))
    return kb.add(types.InlineKeyboardButton("« Назад", callback_data="open_main_settings"))

def autopost_list_kb(cid):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for p in [p for p in db_get("autopost", {"posts": []}).get("posts", []) if str(p.get("chat_id")) == str(cid)]:
        kb.add(types.InlineKeyboardButton(f"{'✅' if p.get('enabled') else '❌'} {p.get('name', 'Пост')}", callback_data=f"ap_select_{p['id']}"))
    kb.add(types.InlineKeyboardButton("➕ Создать", callback_data=f"ap_create_post_{cid}"), types.InlineKeyboardButton("🗑 Удалить", callback_data=f"ap_delete_menu_{cid}"))
    return kb.add(types.InlineKeyboardButton("« Назад", callback_data="m_autopost_list"))

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
    kb.add(types.InlineKeyboardButton(f"💡 Статус: {'Вкл' if post.get('enabled') else 'Выкл'}", callback_data=f"ap_toggle_{pid}"), types.InlineKeyboardButton(f"⏱ Интервал: {istr}", callback_data=f"ap_int_menu_{pid}"))
    kb.add(types.InlineKeyboardButton(f"🕑 Время: {post.get('daily_time') or 'Выкл'}", callback_data=f"ap_time_menu_{pid}"), types.InlineKeyboardButton(f"📅 Дата: {post.get('start_date') or 'Сегодня'}", callback_data=f"ap_date_menu_{pid}"))
    kb.add(types.InlineKeyboardButton(f"♻️ Удаление: {'Вкл' if post.get('auto_delete_prev') else 'Выкл'}", callback_data=f"ap_autodel_{pid}"), types.InlineKeyboardButton(f"🖼 Фото: {'Есть' if post.get('photo') else 'Нет'}", callback_data=f"ap_photo_{pid}"))
    kb.add(types.InlineKeyboardButton("📝 Текст", callback_data=f"ap_text_{pid}"), types.InlineKeyboardButton(f"🔘 Кнопки ({sum(len(r) for r in post.get('buttons',[]))})", callback_data=f"ap_btns_{pid}"))
    kb.add(types.InlineKeyboardButton("👁 Предпросмотр", callback_data=f"ap_preview_{pid}"), types.InlineKeyboardButton("🚀 Отправить", callback_data=f"ap_send_{pid}"))
    return kb.add(types.InlineKeyboardButton("« К постам", callback_data=f"ap_chat_{post.get('chat_id', '')}"))

def post_text_view(pid):
    post = next((p for p in db_get("autopost", {"posts": []}).get("posts", []) if p["id"] == pid), None)
    if not post: return "⚠️ Пост не найден."
    cname = db_get("chats_cache", {}).get(str(post.get("chat_id")), str(post.get("chat_id")))
    dt = post.get("daily_time")
    time_str = f"Ежедневно в {dt}" if dt else "По интервалу"
    rep_str = "Ежедневно" if dt else ("Выключено" if post.get("interval",3600)==0 else f"Каждые {post['interval']//60}м")
    return f"🕑 Пост\n💡 Статус: {'Вкл' if post.get('enabled') else 'Выкл'}\n📢 Чат: {cname}\n🕑 Время: {time_str}\n🔁 Повтор: {rep_str}"

# --- ФОНОВЫЕ ЗАДАЧИ ---
def send_specific_post(chat_id, post):
    try:
        mk = build_post_user_kb(post)
        msg = bot.send_photo(chat_id, post["photo"], caption=post.get("text", ""), reply_markup=mk, parse_mode='HTML') if post.get("photo") else bot.send_message(chat_id, post.get("text", ""), reply_markup=mk, parse_mode='HTML')
        if post.get("auto_delete_prev") and post.get("last_msg_id"):
            try: bot.delete_message(chat_id, post["last_msg_id"])
            except: pass
        post["last_msg_id"] = msg.message_id
    except Exception as e: logging.error(f"[AUTOPOST] {e}")

def autopost_worker():
    while True:
        time.sleep(15)
        try:
            now_ts = datetime.now(KYIV_TZ).timestamp()
            td_str, curr_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d"), datetime.now(KYIV_TZ).strftime("%H:%M")
            data = db_get("autopost", {"posts": []})
            updated = False
            for p in data.get("posts", []):
                if not p.get("enabled") or (p.get("start_date") and td_str < p["start_date"]): continue
                dt = p.get("daily_time")
                if dt:
                    if curr_str >= dt and p.get("last_sent_date") != td_str:
                        send_specific_post(p.get("chat_id"), p)
                        p["last_post"], p["last_sent_date"], updated = now_ts, td_str, True
                elif p.get("interval", 0) > 0 and now_ts - p.get("last_post", 0) >= p["interval"]:
                    send_specific_post(p.get("chat_id"), p)
                    p["last_post"], updated = now_ts, True
            if updated: db_set("autopost", data)
        except Exception as e: logging.error(f"[WORKER] {e}")
threading.Thread(target=autopost_worker, daemon=True).start()

# --- ИГРОВАЯ ЛОГИКА ---
def lucky_game_result(cid, uid, fname, msg_id, win, left):
    try:
        time.sleep(7)
        try: bot.delete_message(cid, msg_id)
        except: pass
        mention = get_user_mention(user_id=uid, first_name=fname)
        if win:
            ldrs = db_get("lucky_leaders", {})
            u = ldrs.setdefault(str(uid), {"name": fname, "wins": 0})
            u["wins"] += 1
            db_set("lucky_leaders", ldrs)
            rank = sum(1 for v in ldrs.values() if v.get("wins",0) > u["wins"]) + 1
            txt = f"🎉 {mention}, невероятно! Ты выиграл +1 балл!\nТвое место: #{rank}\n"
        else: txt = f"😔 {mention}, не повезло.\n"
        
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🎲 Снова", callback_data=f"lucky_again_{uid}")) if left > 0 else None
        if left == 0:
            rem = int(lucky_limits[uid]["reset_at"] - time.time())
            txt += f"\nПопыток больше нет😔\nНовые через: {max(0, rem//60)}м {max(0, rem%60)}с.\n\nЗато в боте играй без ограничений😉\n🔥 @vibe_247top_bot"
        else: txt += f"Осталось попыток: {left}\nСыграем еще?"
        track_and_replace_specific_cmd(cid, uid, "lucky_game", bot.send_message(cid, txt, reply_markup=kb, parse_mode='HTML'))
    finally:
        if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))

def play_lucky_game(cid, uid, fname):
    try:
        lim = lucky_limits.setdefault(uid, {"left": 5, "reset_at": 0})
        now = time.time()
        if lim["left"] <= 0:
            if now < lim["reset_at"]:
                rem = int(lim["reset_at"] - now)
                txt = f"{get_user_mention(user_id=uid, first_name=fname)},\nПопыток нет😔\nНовые через: {rem//60}м {rem%60}с.\n\nЗато в боте без ограничений😉\n🔥 @vibe_247top_bot"
                track_and_replace_specific_cmd(cid, uid, "lucky_game", bot.send_message(cid, txt, parse_mode='HTML'))
                if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))
                return
            else: lim["left"] = 5
        lim["left"] -= 1
        if lim["left"] == 0: lim["reset_at"] = now + 1800
        
        emoji = random.choice(["🎯", "🎳", "🏀"])
        try: dice = bot.send_dice(cid, emoji=emoji)
        except:
            bot.send_message(cid, "⚠️ Нет прав на кубики.")
            if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))
            return
        win = (emoji in ["🎯", "🎳"] and dice.dice.value == 6) or (emoji == "🏀" and dice.dice.value in [4, 5])
        executor.submit(lucky_game_result, cid, uid, fname, dice.message_id, win, lim["left"])
    except Exception as e:
        if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))

# --- ОБРАБОТЧИКИ КОМАНД ---
@bot.message_handler(commands=['start'])
def cmd_start(m):
    if not check_access(m): return
    if m.chat.type == 'private' and m.from_user.id in BOSSES and m.text and "settings" in m.text: return bot.reply_to(m, "📢 Выберите группу:", reply_markup=chats_selection_kb())
    kb = types.InlineKeyboardMarkup(row_width=1).add(types.InlineKeyboardButton("Что ты умеешь?", callback_data="what_can_i_do"))
    if m.chat.type == 'private' and m.from_user.id in BOSSES: kb.add(types.InlineKeyboardButton("⚙️ Настройки", callback_data="open_main_settings"))
    msg = bot.send_message(m.chat.id, f"Привет, {get_user_mention(m.from_user)}... Я Лиза...\n💭 Чат: https://t.me/+8WZ4kwpAaZ0yZTRi\n🛒 Наш бот: @vibe_247top_bot\n🎁 События: /events", reply_markup=kb, parse_mode='HTML', disable_web_page_preview=True)
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
    except: pass
    if (cid, uid) in active_lucky_players: return track_and_replace_specific_cmd(cid, uid, "lucky_game", bot.send_message(cid, "Дождись конца игры!"))
    active_lucky_players.add((cid, uid))
    play_lucky_game(cid, uid, m.from_user.first_name)

@bot.message_handler(commands=['leaders_lucky_game'])
def cmd_llg(m):
    if not check_access(m): return
    try: bot.delete_message(m.chat.id, m.message_id)
    except: pass
    ldrs = db_get("lucky_leaders", {})
    if not ldrs: txt = "🏆 Рейтинг пуст."
    else: txt = "🏆 Рейтинг везунчиков\n\n" + "\n".join(f"{i}. {get_user_mention(user_id=int(u), first_name=info.get('name'))} — <b>{info.get('wins',0)}</b>" for i, (u, info) in enumerate(sorted(ldrs.items(), key=lambda x: x[1].get("wins",0), reverse=True)[:10], 1))
    track_and_replace_specific_cmd(m.chat.id, m.from_user.id, "leaders_lucky_game", bot.send_message(m.chat.id, txt, parse_mode='HTML'))

@bot.message_handler(commands=['events'])
def cmd_events(m):
    if not check_access(m): return
    evs = db_get("events", [])
    evs = evs["events"] if isinstance(evs, dict) and "events" in evs else evs
    auto_del(bot.send_message(m.chat.id, "Пока пусто!" if not evs else "Планируется:\n" + "\n".join(f"🔸 {e.get('date')} — {e.get('info')}" for e in evs)), 180)

@bot.message_handler(commands=['start_game_safe'])
def cmd_sgs(m):
    if not check_access(m) or m.from_user.id not in BOSSES: return
    if m.chat.id in active_safes: return bot.send_message(m.chat.id, "⚠️ Сейф уже активирован.")
    active_safes[m.chat.id] = {"code": f"{random.randint(0,999):03d}", "hint_given": False, "gid": time.time()}
    track_and_replace_specific_cmd(m.chat.id, m.from_user.id, "start_game_safe", bot.send_message(m.chat.id, "🔐 ИГРА НАЧАЛАСЬ\nБронированный сейф заблокирован на ХХХ.\nПишите код в чат.\nТоп: /leaders_safe_game"))

@bot.message_handler(commands=['leaders_safe_game'])
def cmd_lsg(m):
    if not check_access(m): return
    track_and_replace_specific_cmd(m.chat.id, m.from_user.id, "leaders_safe_game", bot.send_message(m.chat.id, format_safe_leaderboard(), parse_mode='HTML'))

# --- КОЛБЕКИ ---
@bot.callback_query_handler(func=lambda c: True)
def cb_handler(c):
    cid, uid, d = c.message.chat.id, c.from_user.id, c.data
    is_pv = c.message.chat.type == 'private'

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

    if d.startswith("lucky_again_"):
        if uid != int(d.split("_")[2]): return bot.answer_callback_query(c.id, "⛔ Не твоя игра!", show_alert=True)
        try: bot.delete_message(cid, c.message.message_id)
        except: pass
        bot.answer_callback_query(c.id)
        return play_lucky_game(cid, uid, c.from_user.first_name)

    if d == "what_can_i_do":
        bot.answer_callback_query(c.id)
        return bot.edit_message_text("🔹 Общаюсь\n🔹 Слежу за матом\n🔹 Автопостинг\n🔹 Игры (/lucky_game, сейф)", cid, c.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data="back_to_start")))
        
    if d == "back_to_start":
        bot.answer_callback_query(c.id)
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Что ты умеешь?", callback_data="what_can_i_do"))
        if uid in BOSSES: kb.add(types.InlineKeyboardButton("⚙️ Настройки", callback_data="open_main_settings"))
        return bot.edit_message_text(f"Привет, {get_user_mention(c.from_user)}... Я Лиза.\n💭Чат: https://t.me/+8WZ4kwpAaZ0yZTRi\n🛒Бот: @vibe_247top_bot", cid, c.message.message_id, reply_markup=kb, disable_web_page_preview=True)

    if d == "open_main_settings":
        if uid not in BOSSES: return bot.answer_callback_query(c.id, "⛔", show_alert=True)
        bot.answer_callback_query(c.id)
        return bot.edit_message_text("🎛 Панель:", cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))

    if d == "m_toggle_intervene":
        set_v(cid, "intervene", not get_v(cid, "intervene", True))
        bot.answer_callback_query(c.id)
        return bot.edit_message_reply_markup(cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))

    if d in ["m_freq", "m_anger"]:
        bot.answer_callback_query(c.id)
        t = "freq" if d == "m_freq" else "anger"
        kb = types.InlineKeyboardMarkup(row_width=4)
        kb.add(*[types.InlineKeyboardButton(f"{v}%", callback_data=f"s_{t}_{v}") for v in [10, 20, 30, 40, 50, 70, 100]])
        kb.add(types.InlineKeyboardButton("« Назад", callback_data="open_main_settings"))
        return bot.edit_message_text("📊 Выберите значение:", cid, c.message.message_id, reply_markup=kb)

    if d.startswith("s_"):
        t, v = d.split("_")[1], d.split("_")[2]
        bot.answer_callback_query(c.id)
        set_v(cid, t, int(v))
        return bot.edit_message_text("✅ Параметр зафиксирован.", cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))

    if d == "to_group_settings" and uid in BOSSES:
        try: bot.send_message(uid, "📢 Выберите группу:", reply_markup=chats_selection_kb())
        except: return bot.answer_callback_query(c.id, "⚠️ Нажми Start в ЛС с ботом.", show_alert=True)
        return bot.answer_callback_query(c.id, "Отправлено в ЛС")

    if d == "m_autopost_list":
        bot.answer_callback_query(c.id)
        return bot.edit_message_text("📢 Группа для автопостинга:", cid, c.message.message_id, reply_markup=chats_selection_kb())

    if d.startswith("ap_chat_"):
        bot.answer_callback_query(c.id)
        return bot.edit_message_text("📋 Посты:", cid, c.message.message_id, reply_markup=autopost_list_kb(d.split("_")[2]))

    if d.startswith("ap_select_"):
        pid = d.split("_")[2]
        active_editing_post[uid] = pid
        bot.answer_callback_query(c.id)
        return bot.edit_message_text(post_text_view(pid), cid, c.message.message_id, reply_markup=post_settings_kb(pid))

    if d.startswith("ap_create_post_"):
        c_str = d.split("_")[3]
        data = db_get("autopost", {"posts": []})
        data["posts"].append({"id": str(int(time.time())), "name": f"Пост #{len([p for p in data['posts'] if str(p.get('chat_id'))==c_str])+1}", "enabled": False, "interval": 3600, "daily_time": None, "start_date": None, "auto_delete_prev": False, "last_msg_id": None, "text": "Текст...", "photo": None, "buttons": [], "last_post": 0, "chat_id": int(c_str) if c_str.lstrip('-').isdigit() else c_str})
        db_set("autopost", data)
        bot.answer_callback_query(c.id, "✅ Создан")
        return bot.edit_message_text("📋 Посты", cid, c.message.message_id, reply_markup=autopost_list_kb(c_str))

    if d.startswith("ap_delete_menu_"):
        bot.answer_callback_query(c.id)
        kb = types.InlineKeyboardMarkup(row_width=1)
        for p in [p for p in db_get("autopost", {"posts": []}).get("posts", []) if str(p.get("chat_id")) == d.split("_")[3]]: kb.add(types.InlineKeyboardButton(f"🗑 Удалить: {p.get('name', 'Пост')}", callback_data=f"ap_delfinal_{p['id']}"))
        return bot.edit_message_text("🗑 Выберите пост:", cid, c.message.message_id, reply_markup=kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"ap_chat_{d.split('_')[3]}")))

    if d.startswith("ap_delfinal_"):
        pid = d.split("_")[2]
        data = db_get("autopost", {"posts": []})
        c_str = next((str(p.get("chat_id")) for p in data["posts"] if p["id"] == pid), "-1004374303475")
        data["posts"] = [p for p in data["posts"] if p["id"] != pid]
        db_set("autopost", data)
        bot.answer_callback_query(c.id, "🗑 Удален")
        return bot.edit_message_text("📋 Посты", cid, c.message.message_id, reply_markup=autopost_list_kb(c_str))

    if d.startswith("ap_toggle_") or d.startswith("ap_autodel_"):
        pid = d.split("_")[2]
        data = db_get("autopost", {"posts": []})
        for p in data["posts"]:
            if p["id"] == pid:
                if "toggle" in d: p["enabled"] = not p.get("enabled", False)
                else: p["auto_delete_prev"] = not p.get("auto_delete_prev", False)
        db_set("autopost", data)
        bot.answer_callback_query(c.id, "✅")
        return bot.edit_message_text(post_text_view(pid), cid, c.message.message_id, reply_markup=post_settings_kb(pid))

    if d.startswith("ap_int_menu_"):
        pid = d.split("_")[3]
        bot.answer_callback_query(c.id)
        kb = types.InlineKeyboardMarkup(row_width=3)
        kb.add(types.InlineKeyboardButton("15м", callback_data=f"ap_setint_{pid}_900"), types.InlineKeyboardButton("1ч", callback_data=f"ap_setint_{pid}_3600"), types.InlineKeyboardButton("6ч", callback_data=f"ap_setint_{pid}_21600"))
        kb.add(types.InlineKeyboardButton("Выкл", callback_data=f"ap_setint_{pid}_0"), types.InlineKeyboardButton("✏ Свое", callback_data=f"ap_custom_int_{pid}"))
        return bot.edit_message_text("⏱ Выберите интервал:", cid, c.message.message_id, reply_markup=kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"ap_select_{pid}")))

    if d.startswith("ap_setint_") or d.startswith("ap_settime_") or d.startswith("ap_setdate_") or d.startswith("ap_clrbtns_") or d.startswith("ap_delph_"):
        pid = d.split("_")[2]
        data = db_get("autopost", {"posts": []})
        for p in data["posts"]:
            if p["id"] == pid:
                if "setint" in d: p["interval"], p["daily_time"] = int(d.split("_")[3]), None
                elif "settime" in d: p["daily_time"] = None if d.split("_")[3] == "OFF" else d.split("_")[3]
                elif "setdate" in d: p["start_date"] = None if d.split("_")[3] == "OFF" else d.split("_")[3]
                elif "clrbtns" in d: p["buttons"] = []
                elif "delph" in d: p["photo"] = None
        db_set("autopost", data)
        bot.answer_callback_query(c.id, "✅ Сохранено")
        return bot.edit_message_text(post_text_view(pid), cid, c.message.message_id, reply_markup=post_settings_kb(pid))

    if d.startswith("ap_custom_"):
        pid = d.split("_")[3]
        active_editing_post[uid] = pid
        bot.answer_callback_query(c.id)
        if "int" in d: waiting_autopost_interval.add(uid); msg = "Отправьте интервал (например: 45м, 3ч):"
        elif "time" in d: waiting_autopost_time.add(uid); msg = "Отправьте время (например: 14:30):"
        elif "date" in d: waiting_autopost_date.add(uid); msg = "Отправьте дату (например: 2026-10-01):"
        return bot.send_message(cid, msg)

    if d.startswith("ap_text_") or d.startswith("ap_setbtns_") or d.startswith("ap_setph_"):
        pid = d.split("_")[2]
        active_editing_post[uid] = pid
        bot.answer_callback_query(c.id)
        if "text" in d: waiting_autopost_text.add(uid); msg = "📝 Отправьте новый текст поста."
        elif "btns" in d: waiting_autopost_buttons.add(uid); msg = "Отправь кнопки:\nТекст - https://ссылка.com\nИли: Текст - cmd:/lucky_game"
        elif "ph" in d: waiting_autopost_photo.add(uid); msg = "🖼 Отправьте фото в этот чат."
        return bot.send_message(cid, msg)

    if d.startswith("ap_photo_") or d.startswith("ap_btns_") or d.startswith("ap_time_menu_") or d.startswith("ap_date_menu_"):
        pid = d.split("_")[2] if "photo" in d or "btns" in d else d.split("_")[3]
        bot.answer_callback_query(c.id)
        kb = types.InlineKeyboardMarkup(row_width=1)
        if "photo" in d: kb.add(types.InlineKeyboardButton("🖼 Загрузить", callback_data=f"ap_setph_{pid}"), types.InlineKeyboardButton("🗑 Удалить", callback_data=f"ap_delph_{pid}"))
        elif "btns" in d: kb.add(types.InlineKeyboardButton("➕ Настроить", callback_data=f"ap_setbtns_{pid}"), types.InlineKeyboardButton("🗑 Удалить все", callback_data=f"ap_clrbtns_{pid}"))
        elif "time" in d: kb.add(types.InlineKeyboardButton("12:00", callback_data=f"ap_settime_{pid}_12:00"), types.InlineKeyboardButton("Выкл", callback_data=f"ap_settime_{pid}_OFF"), types.InlineKeyboardButton("Свое", callback_data=f"ap_custom_time_{pid}"))
        elif "date" in d: kb.add(types.InlineKeyboardButton("Сразу", callback_data=f"ap_setdate_{pid}_OFF"), types.InlineKeyboardButton("Ввести", callback_data=f"ap_custom_date_{pid}"))
        return bot.edit_message_text("Настройка:", cid, c.message.message_id, reply_markup=kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"ap_select_{pid}")))

    if d.startswith("ap_send_"):
        pid = d.split("_")[2]
        data = db_get("autopost", {"posts": []})
        post = next((p for p in data["posts"] if p["id"] == pid), None)
        if post:
            try:
                send_specific_post(int(post.get("chat_id", -1004374303475)), post)
                post["last_post"] = time.time()
                db_set("autopost", data)
                return bot.answer_callback_query(c.id, "🚀 Отправлено!", show_alert=True)
            except Exception as e: return bot.answer_callback_query(c.id, f"⚠️ Ошибка: {e}", show_alert=True)

    if d.startswith("ap_preview_"):
        pid = d.split("_")[2]
        bot.answer_callback_query(c.id)
        post = next((p for p in db_get("autopost", {"posts": []}).get("posts", []) if p["id"] == pid), None)
        if post:
            mk = build_post_user_kb(post)
            bot.send_message(cid, "👁 <b>Предпросмотр:</b>", parse_mode='HTML')
            if post.get("photo"): bot.send_photo(cid, post["photo"], caption=post.get("text", ""), reply_markup=mk, parse_mode='HTML')
            else: bot.send_message(cid, post.get("text", ""), reply_markup=mk, parse_mode='HTML')
            return bot.send_message(cid, "Вот так выглядит пост.", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap_select_{pid}")))

    bot.answer_callback_query(c.id)

# --- ОБРАБОТЧИКИ ВВОДА ---
@bot.message_handler(content_types=['photo'])
def on_photo(m):
    if not check_access(m): return
    uid = m.from_user.id
    if m.chat.type == 'private' and uid in BOSSES and uid in waiting_autopost_photo:
        waiting_autopost_photo.remove(uid)
        pid = active_editing_post.get(uid)
        data = db_get("autopost", {"posts": []})
        for p in data["posts"]:
            if p["id"] == pid: p["photo"] = m.photo[-1].file_id
        db_set("autopost", data)
        bot.reply_to(m, "🖼 Фото сохранено", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap_select_{pid}")))

@bot.message_handler(func=lambda m: True)
def text_handler(m):
    if not check_access(m): return
    uid, cid, t = m.from_user.id, m.chat.id, m.text.strip()
    boss = uid in BOSSES

    if m.chat.type == 'private' and boss:
        pid = active_editing_post.get(uid)
        if uid in waiting_autopost_buttons:
            waiting_autopost_buttons.remove(uid)
            new_btns = []
            for line in t.split('\n'):
                row = []
                for part in line.split('|'):
                    if '-' in part:
                        try:
                            t_btn, val = part.split('-', 1)
                            t_btn, val = t_btn.strip(), val.strip()
                            if val.startswith("cmd:"): row.append({"text": t_btn, "command": val[4:].strip()})
                            elif val.startswith("cb:"): row.append({"text": t_btn, "callback_data": val[3:].strip()})
                            else: row.append({"text": t_btn, "url": val if val.startswith("http") else "https://"+val})
                        except: pass
                if row: new_btns.append(row)
            data = db_get("autopost", {"posts": []})
            for p in data["posts"]:
                if p["id"] == pid: p["buttons"] = new_btns
            db_set("autopost", data)
            return bot.reply_to(m, "✅ Кнопки сохранены", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap_select_{pid}")))

        if uid in waiting_autopost_text:
            waiting_autopost_text.remove(uid)
            data = db_get("autopost", {"posts": []})
            for p in data["posts"]:
                if p["id"] == pid: p["text"] = t
            db_set("autopost", data)
            return bot.reply_to(m, "📝 Текст сохранен", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap_select_{pid}")))

        if uid in waiting_autopost_interval:
            waiting_autopost_interval.remove(uid)
            sec = parse_interval_input(t)
            if sec is not None:
                data = db_get("autopost", {"posts": []})
                for p in data["posts"]:
                    if p["id"] == pid: p["interval"], p["daily_time"] = sec, None
                db_set("autopost", data)
                return bot.reply_to(m, "⏱ Интервал сохранен", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap_select_{pid}")))
            return bot.reply_to(m, "⚠️ Ошибка формата. Пример: 30м, 2ч")

        if uid in waiting_autopost_time:
            waiting_autopost_time.remove(uid)
            if re.match(r'^\d{2}:\d{2}$', t):
                data = db_get("autopost", {"posts": []})
                for p in data["posts"]:
                    if p["id"] == pid: p["daily_time"] = t
                db_set("autopost", data)
                return bot.reply_to(m, "🕑 Время сохранено", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap_select_{pid}")))
            return bot.reply_to(m, "⚠️ Формат: ЧЧ:ММ (12:00)")

        if uid in waiting_autopost_date:
            waiting_autopost_date.remove(uid)
            if re.match(r'^\d{4}-\d{2}-\d{2}$', t):
                data = db_get("autopost", {"posts": []})
                for p in data["posts"]:
                    if p["id"] == pid: p["start_date"] = t
                db_set("autopost", data)
                return bot.reply_to(m, "📅 Дата сохранена", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap_select_{pid}")))
            return bot.reply_to(m, "⚠️ Формат: ГГГГ-ММ-ДД (2026-10-01)")

    t_lower = t.lower()
    if cid in active_safes and re.fullmatch(r'\d{3}', t):
        if t == active_safes[cid]["code"]:
            del active_safes[cid]
            ldrs = db_get("safe_leaders", {})
            ldrs.setdefault(str(uid), {"name": m.from_user.first_name, "wins": 0})["wins"] += 1
            db_set("safe_leaders", ldrs)
            auto_del(bot.send_message(cid, f"🎉 СЕЙФ ВЗЛОМАН\nМастер {get_user_mention(m.from_user)} подобрал код: {t}"), 180)
        return

    if m.chat.type in ['group', 'supergroup'] and not boss and any(s in t_lower for s in SUSP):
        def threat_check():
            if is_threat(m.text):
                try:
                    bot.ban_chat_member(cid, uid)
                    auto_del(bot.send_message(cid, f"{get_user_mention(m.from_user)}, 🛡 Зафиксирована угроза. Бан."), 180)
                except: pass
        executor.submit(threat_check)

    direct = m.chat.type == 'private' or (m.reply_to_message and m.reply_to_message.from_user.id == BOT_ID) or "лиза" in t_lower or f"@{BOT_USER}" in t_lower
    if direct or any(w in t_lower for w in CONFL):
        if not get_v(cid, "intervene", True) or random.randint(1, 100) > get_v(cid, "freq", 40): return
        prompt = f"В чате ссора: {m.reply_to_message.text} -> {m.text}. Резюме:" if (any(w in t_lower for w in CONFL) and m.reply_to_message) else m.text
        def ai_task():
            bot.send_chat_action(cid, 'typing')
            ans = call_ai([{"role": "system", "content": SYS_PROMPT}, {"role": "user", "content": prompt}])
            if m.chat.type == 'private': bot.send_message(cid, ans)
            else: bot.send_message(cid, f"{get_user_mention(m.from_user)}, {ans}", parse_mode='HTML')
        executor.submit(ai_task)

if __name__ == "__main__":
    logging.info("Бот запущен и готов к работе!")
    while True:
        try: bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except KeyboardInterrupt: break
        except Exception as e:
            logging.error(f"Сбой: {e}")
            time.sleep(5)
