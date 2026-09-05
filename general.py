# -*- coding: utf-8 -*-
"""VIBE Bot — general module."""
from runtime import *
from runtime import _EMOJI_RE
from core import *

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

def trim_btn_text(text, limit=22):
    """Обрезает текст кнопки до `limit` символов, не считая эмодзи в начале текста."""
    text = text or ""
    m_prefix = re.match(r'^([\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF\U00002B00-\U00002BFF\uFE0F]+\s*)', text)
    prefix = m_prefix.group(1) if m_prefix else ""
    rest = text[len(prefix):]
    if len(rest) > limit:
        rest = rest[:limit - 1].rstrip() + "…"
    return prefix + rest

def set_v(cid, k, val):
    with state_lock:
        s = db_get("settings", {})
        s.setdefault(str(cid), {"freq": 40, "anger": 40, "intervene": True, "del_sys": False, "max_warns": 3, "warn_action": "mute", "random_reactions": True, "butt_in": False, "butt_in_chance": 15})[k] = val
        db_set("settings", s)

def set_message_reaction_raw(chat_id, message_id, emoji):
    """Прямой вызов Telegram Bot API setMessageReaction — используется как обход,
    так как в установленной версии pyTelegramBotAPI метод set_message_reaction отсутствует."""
    url = f"https://api.telegram.org/bot{TOKEN}/setMessageReaction"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reaction": [{"type": "emoji", "emoji": emoji}],
    }
    r = requests.post(url, json=payload, timeout=10)
    data = {}
    try: data = r.json()
    except Exception: pass
    if not (r.status_code == 200 and data.get("ok")):
        raise RuntimeError(f"setMessageReaction failed: {r.status_code} {data}")

def react_with_emoji(chat_id, message_id, emoji):
    try:
        if hasattr(bot, "set_message_reaction"):
            try:
                reaction = [types.ReactionTypeEmoji(emoji)]
            except AttributeError:
                reaction = [{"type": "emoji", "emoji": emoji}]
            bot.set_message_reaction(chat_id, message_id, reaction=reaction)
        else:
            set_message_reaction_raw(chat_id, message_id, emoji)
    except Exception as e:
        logging.error(f"[REACTION] {e}", exc_info=True)

def maybe_react_randomly(m):
    """С шансом RANDOM_REACTION_CHANCE ставит реакцию на случайное сообщение в чате."""
    try:
        if not get_v(m.chat.id, "random_reactions", True): return
        now = time.monotonic()
        last = random_reaction_last.get(m.chat.id, 0.0)
        if now - last < RANDOM_REACTION_COOLDOWN_SECONDS: return
        if random.random() >= RANDOM_REACTION_CHANCE: return
        random_reaction_last[m.chat.id] = now
        if hasattr(bot, "set_message_reaction"):
            try:
                reaction = [types.ReactionTypeEmoji(RANDOM_REACTION_EMOJI)]
            except AttributeError:
                reaction = [{"type": "emoji", "emoji": RANDOM_REACTION_EMOJI}]
            bot.set_message_reaction(m.chat.id, m.message_id, reaction=reaction)
        else:
            set_message_reaction_raw(m.chat.id, m.message_id, RANDOM_REACTION_EMOJI)
    except Exception as e:
        logging.error(f"[RANDOM REACTION] {e}", exc_info=True)

_stats_pending = {}
_stats_lock = threading.RLock()
_STATS_FLUSH_MESSAGES = 10
_STATS_FLUSH_SECONDS = 10

def flush_stats(uid=None):
    """Принудительно сохраняет накопленную статистику.
    Статистика сообщений не пишет SQLite на каждое сообщение — это резко снижает I/O.
    """
    with _stats_lock:
        if uid is None:
            targets = list(_stats_pending)
        else:
            targets = [str(uid)] if str(uid) in _stats_pending else []
        if not targets:
            return
        users = db_get("users_data", {})
        activity = db_get("chat_activity", {})
        changed_users = False
        changed_activity = False
        for key in targets:
            item = _stats_pending.pop(key, None)
            if not item:
                continue
            changed_users = changed_activity = True
        if changed_users:
            db_set("users_data", users)
        if changed_activity:
            db_set("chat_activity", activity)

def _stats_flush_worker():
    while True:
        try:
            time.sleep(_STATS_FLUSH_SECONDS)
            flush_stats()
        except Exception as e:
            logging.error(f"[STATS FLUSH] {e}")

threading.Thread(target=_stats_flush_worker, daemon=True, name="vibe-stats-flush").start()

def record_xp_and_stats(m):
    if m.chat.type not in ['group', 'supergroup']: return
    uid = m.from_user.id
    if m.from_user.is_bot: return

    fname, uname = m.from_user.first_name, m.from_user.username
    cid = m.chat.id
    now = time.time()

    with _stats_lock:
        users = db_get("users_data", {})
        u = users.setdefault(str(uid), {
            "xp": 0, "msgs": 0, "name": fname, "uname": uname, "first_seen": now,
            "respects": 0, "given_respects": 0, "respect_reset": 0
        })
        if uname: u["uname"] = uname
        u["name"] = fname
        old_xp = u.get("xp", 0)
        u["xp"] = old_xp + random.randint(1, 3)
        u["msgs"] = u.get("msgs", 0) + 1
        u["last_seen"] = now

        activity = db_get("chat_activity", {})
        ca = activity.setdefault(str(cid), {})
        cu = ca.setdefault(str(uid), {"first_seen": now, "last_seen": now, "msgs": 0, "name": fname})
        cu["last_seen"] = now
        cu["msgs"] = cu.get("msgs", 0) + 1
        cu["name"] = fname
        if uname: cu["uname"] = uname
        # История по дням и часам нужна для Iris-style статистики.
        day_key = datetime.fromtimestamp(now, KYIV_TZ).strftime("%Y-%m-%d")
        hour_key = datetime.fromtimestamp(now, KYIV_TZ).strftime("%H")
        daily = cu.setdefault("daily", {})
        hourly = cu.setdefault("hourly", {})
        daily[day_key] = int(daily.get(day_key, 0) or 0) + 1
        hourly[hour_key] = int(hourly.get(hour_key, 0) or 0) + 1
        # Не даём служебной истории разрастаться бесконечно.
        if len(daily) > 120:
            for k in sorted(daily)[:-120]: daily.pop(k, None)

        pending = _stats_pending.setdefault(str(uid), {"count": 0, "last_flush": now})
        pending["count"] += 1
        should_flush = pending["count"] >= _STATS_FLUSH_MESSAGES or now - pending["last_flush"] >= _STATS_FLUSH_SECONDS
        if should_flush:
            pending["last_flush"] = now
            db_set("users_data", users)
            db_set("chat_activity", activity)
            pending["count"] = 0

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
    flush_stats(target_uid)
    with state_lock:
        users = db_get("users_data", {})
        u = users.get(str(target_uid))
    if not u:
        try: bot.reply_to(m, "🤷‍♀️ Информации об этом пользователе пока нет.")
        except: pass
        return
    
    xp, msgs = u.get("xp", 0), u.get("msgs", 0)
    respects = u.get("respects", 0)
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
