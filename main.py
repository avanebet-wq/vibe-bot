import os, time, logging, random, html, threading, re
from datetime import datetime, timedelta
import telebot
from telebot import types
from telebot.types import ChatPermissions

from config import TOKEN, BOSSES, ALLOWED_GROUPS, ALLOWED_GROUPS_RAW, DENIED_MSG, KYIV_TZ
from db import get_cfg, set_cfg, get_autopost, set_autopost, get_mutes, set_mutes, get_lucky_leaders, set_lucky_leaders, get_safe_leaders, set_safe_leaders, get_chats_cache, set_chats_cache
from ai import call_ai

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = telebot.TeleBot(TOKEN)

# In-memory states
active_safes = {}
lucky_limits = {}
active_lucky_players = set()
messages_to_delete = []
mem_lock = threading.Lock()
waiting_states = {}
active_editing_post = {}

def get_user_mention(user_obj=None, user_id=None, first_name=None):
    if user_obj: user_id, first_name = user_obj.id, user_obj.first_name
    safe_name = html.escape(str(first_name or "Пользователь"))
    if user_id: return f'<a href="tg://user?id={user_id}">{safe_name}</a>'
    return safe_name

def is_allowed(m):
    if m.chat.type == 'private': return m.from_user.id in BOSSES
    clean_cid = str(m.chat.id).replace("-100", "").replace("-", "")
    return clean_cid in ALLOWED_GROUPS_RAW or m.chat.id in ALLOWED_GROUPS

def check_access(m):
    if not is_allowed(m):
        try: bot.reply_to(m, DENIED_MSG)
        except: pass
        return False
    return True

def auto_del(msg, ttl=180):
    if msg:
        with mem_lock:
            messages_to_delete.append({"cid": msg.chat.id, "mid": msg.message_id, "time": time.time() + ttl})

def cleanup_worker():
    while True:
        time.sleep(5)
        now = time.time()
        with mem_lock:
            rem = []
            for item in messages_to_delete:
                if now >= item["time"]:
                    try: bot.delete_message(item["cid"], item["mid"])
                    except: pass
                else: rem.append(item)
            messages_to_delete[:] = rem

threading.Thread(target=cleanup_worker, daemon=True).start()

def autopost_worker():
    while True:
        time.sleep(15)
        try:
            data = get_autopost()
            now_ts = datetime.now(KYIV_TZ).timestamp()
            today_date = datetime.now(KYIV_TZ).strftime("%Y-%m-%d")
            curr_time = datetime.now(KYIV_TZ).strftime("%H:%M")
            updated = False

            for p in data.get("posts", []):
                if not p.get("enabled"): continue
                dt = p.get("daily_time")
                if dt:
                    if dt <= curr_time and p.get("last_sent_date") != today_date:
                        send_post(p["chat_id"], p)
                        p["last_sent_date"] = today_date
                        updated = True
                else:
                    interval = p.get("interval", 0)
                    if interval > 0 and now_ts - p.get("last_post", 0) >= interval:
                        send_post(p["chat_id"], p)
                        p["last_post"] = now_ts
                        updated = True
            if updated: set_autopost(data)
        except Exception as e: logging.error(f"Autopost Error: {e}")

def send_post(chat_id, post):
    markup = build_post_kb(post)
    try:
        if post.get("photo"):
            msg = bot.send_photo(chat_id, post["photo"], caption=post.get("text", ""), reply_markup=markup, parse_mode='HTML')
        else:
            msg = bot.send_message(chat_id, post.get("text", ""), reply_markup=markup, parse_mode='HTML')
        if post.get("auto_delete_prev") and post.get("last_msg_id"):
            try: bot.delete_message(chat_id, post["last_msg_id"])
            except: pass
        post["last_msg_id"] = msg.message_id
    except Exception as e: logging.error(f"Send post error: {e}")

def build_post_kb(post):
    rows = post.get("buttons", [])
    if not rows: return None
    kb = types.InlineKeyboardMarkup()
    for row in rows:
        row_btns = []
        for b in row:
            if b.get("url"): row_btns.append(types.InlineKeyboardButton(b["text"], url=b["url"]))
            elif b.get("command"): row_btns.append(types.InlineKeyboardButton(b["text"], callback_data=f"cmd_exec_{post['id']}_{b['command']}"))
        if row_btns: kb.row(*row_btns)
    return kb

threading.Thread(target=autopost_worker, daemon=True).start()

@bot.message_handler(commands=['start'])
def start_cmd(m):
    if not check_access(m): return
    txt = f"Привет, {get_user_mention(m.from_user)}... Я Лиза...\n💭 Чат: https://t.me/+8WZ4kwpAaZ0yZTRi\n🛒 Наш бот: @vibe_247top_bot"
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Что ты умеешь?", callback_data="info"))
    if m.chat.type == 'private' and m.from_user.id in BOSSES:
        kb.add(types.InlineKeyboardButton("⚙️ Настройки", callback_data="settings"))
    msg = bot.send_message(m.chat.id, txt, reply_markup=kb, parse_mode='HTML', disable_web_page_preview=True)
    if m.chat.type != 'private': auto_del(msg)

@bot.message_handler(commands=['lucky_game'])
def lucky_cmd(m):
    if not check_access(m): return
    uid, cid = m.from_user.id, m.chat.id
    try: bot.delete_message(cid, m.message_id)
    except: pass
    
    if (cid, uid) in active_lucky_players: return
    active_lucky_players.add((cid, uid))
    
    limit_info = lucky_limits.setdefault(uid, {"left": 5, "reset_at": 0})
    if limit_info["left"] <= 0 and time.time() < limit_info["reset_at"]:
        rem = int(limit_info["reset_at"] - time.time())
        bot.send_message(cid, f"Попыток нет. Жди {rem//60}м {rem%60}с.\nЗато в боте играй без ограничений😉\n🔥 @vibe_247top_bot")
        active_lucky_players.remove((cid, uid))
        return
        
    limit_info["left"] -= 1
    if limit_info["left"] == 0: limit_info["reset_at"] = time.time() + 1800
    
    emoji = random.choice(["🎯", "🎳", "🏀"])
    dice = bot.send_dice(cid, emoji=emoji)
    val = dice.dice.value
    win = (emoji in ["🎯", "🎳"] and val == 6) or (emoji == "🏀" and val in [4, 5])
    
    def result_task():
        time.sleep(5)
        try: bot.delete_message(cid, dice.message_id)
        except: pass
        mention = get_user_mention(m.from_user)
        if win:
            ldrs = get_lucky_leaders()
            u = ldrs.setdefault(str(uid), {"name": m.from_user.first_name, "wins": 0})
            u["wins"] += 1
            set_lucky_leaders(ldrs)
            txt = f"🎉 {mention}, выиграл +1 балл! Осталось: {limit_info['left']}"
        else:
            txt = f"😔 Не повезло. Осталось: {limit_info['left']}"
            
        if limit_info["left"] == 0: txt += "\nЗато в боте играй без ограничений😉\n🔥 @vibe_247top_bot"
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🎲 Снова", callback_data=f"lucky_again_{uid}")) if limit_info["left"]>0 else None
        bot.send_message(cid, txt, reply_markup=kb, parse_mode='HTML')
        active_lucky_players.remove((cid, uid))
        
    threading.Thread(target=result_task, daemon=True).start()

@bot.message_handler(func=lambda m: True)
def text_handler(m):
    if not check_access(m): return
    uid, cid, t = m.from_user.id, m.chat.id, m.text.strip().lower()

    if any(x in t for x in ["где бот", "магазин", "стафф"]):
        return auto_del(bot.send_message(cid, "Та ну, всё тут: @vibe_247top_bot"))

    if cid in active_safes and re.fullmatch(r'\d{3}', t):
        if t == active_safes[cid]["code"]:
            del active_safes[cid]
            ldrs = get_safe_leaders()
            u = ldrs.setdefault(str(uid), {"name": m.from_user.first_name, "wins": 0})
            u["wins"] += 1
            set_safe_leaders(ldrs)
            bot.send_message(cid, f"🎉 СЕЙФ ВЗЛОМАН\nМастер {get_user_mention(m.from_user)} подобрал код: {t}")
        return

    if m.chat.type in ['group', 'supergroup']:
        s = get_cfg().get(str(cid), {})
        if s.get("intervene", True) and random.randint(1, 100) <= s.get("freq", 40):
            sys_prompt = "Ты Лиза, расслабленная девочка. Отвечай коротко (1-2 предложения), без списков. На русском. Без скобок."
            def ai_task():
                ans = call_ai([{"role": "system", "content": sys_prompt}, {"role": "user", "content": m.text}])
                bot.send_message(cid, f"{get_user_mention(m.from_user)}, {ans}", parse_mode='HTML')
            threading.Thread(target=ai_task, daemon=True).start()

if __name__ == "__main__":
    logging.info("Запуск бота...")
    while True:
        try: bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except Exception as e:
            logging.error(f"Сбой: {e}")
            time.sleep(5)
