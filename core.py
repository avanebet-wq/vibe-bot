# -*- coding: utf-8 -*-
"""VIBE Bot — core module."""
from runtime import *

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
    rank = get_admin_rank(cid, uid)
    if rank <= 0: return False
    perms = get_rank_permissions(cid, rank)
    return perms.get(perm_name, False)

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

def parse_duration_from_args(args):
    """Парсит срок как из слитного токена ('5м', '5мин'), так и из двух
    раздельных слов ('5', 'минут'). Возвращает (секунды, ok, сколько_токенов_съедено)."""
    if not args: return 0, False, 0
    dur, ok = parse_duration(args[0])
    if ok: return dur, True, 1
    if len(args) > 1 and args[0].isdigit():
        dur, ok = parse_duration(args[0] + args[1])
        if ok: return dur, True, 2
    return 0, False, 0

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
                        if (data.get("uname") or "").lower() == uname:
                            target_uid = int(u_id_str)
                            target_name = data.get("name", uname)
                            break
                if not target_uid:
                    # В локальной базе не нашли (юзер ещё не писал в чат /
                    # сменил username) — пробуем резолвнуть напрямую через
                    # Telegram API и проверить, что он реально состоит в чате.
                    try:
                        chat_obj = bot.get_chat(f"@{uname}")
                        member = bot.get_chat_member(m.chat.id, chat_obj.id)
                        if member and member.status not in ('left', 'kicked'):
                            target_uid = chat_obj.id
                            target_name = chat_obj.first_name or uname
                            with state_lock:
                                users = db_get("users_data", {})
                                u = users.setdefault(str(target_uid), {})
                                u["uname"] = uname
                                u.setdefault("name", target_name)
                                db_set("users_data", users)
                    except Exception:
                        pass
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

def get_user_mention(user_obj=None, user_id=None, first_name=None):
    if user_obj: user_id, first_name = user_obj.id, user_obj.first_name
    safe_name = html.escape(str(first_name or "User"))
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>' if user_id else safe_name

def issue_warn(cid, chat_title, target_uid, target_name, admin_uid, admin_name, reason, m_to_reply, warn_count=1):
    max_warns = get_v(cid, "max_warns", 3)
    warn_action = get_v(cid, "warn_action", "mute")
    
    with state_lock:
        db = db_get("chat_warns", {})
        cw = db.setdefault(str(cid), {})
        uw = cw.setdefault(str(target_uid), {"count": 0, "history": []})
        now = time.time()
        history = [h for h in uw.get("history", []) if not h.get("expires") or h.get("expires", 0) > now]
        uw["history"] = history
        uw["count"] = len(history)
        uw["count"] += warn_count
        warn_period = get_v(cid, "warn_period", 0)
        uw["history"].append({
            "reason": reason, "by_uid": admin_uid, "by_name": admin_name, "date": time.time(),
            "expires": (time.time() + warn_period) if warn_period else 0
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

def register_chat(chat):
    if chat.type in ['group', 'supergroup', 'channel']:
        with state_lock:
            cache = db_get("chats_cache", {})
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
            if uid not in game["players"] and get_admin_rank(cid, uid) == 0:
                try: bot.delete_message(cid, m.message_id)
                except: pass
                warn = bot.send_message(cid, f"🤫 {get_user_mention(m.from_user)}, тсс! Идёт игра в слова, писать могут только участники!", parse_mode='HTML')
                auto_del(warn, 5)
                return False
    if m.chat.type == 'private':
        cmd_word = ""
        if m.text:
            parts_ = m.text.split()
            if parts_: cmd_word = parts_[0].lstrip("/").split("@")[0].lower()
        if cmd_word in ("start", "settings"):
            return True
        try: bot.delete_message(cid, m.message_id)
        except: pass
        msg = bot.send_message(cid, "⛔ В личных сообщениях доступны только /start и /settings.", parse_mode='HTML')
        auto_del(msg, 5)
        return False
    # Лиза доступна в любой группе/супергруппе, куда её добавили.
    # Никаких заранее прописанных chat_id больше нет.
    register_chat(m.chat)
    return True

def reply_no_rights(m):
    try: bot.delete_message(m.chat.id, m.message_id)
    except: pass
    msg = bot.send_message(m.chat.id, "⛔ У вас нет прав на использование этой команды.", parse_mode="HTML")
    auto_del(msg, 5)

def parse_interval_input(text):
    match = re.match(r'^(\d+)([дчм])$', text.lower().strip())
    if not match: return None
    val, unit = int(match.group(1)), match.group(2)
    return val * 60 if unit == 'м' else (val * 3600 if unit == 'ч' else val * 86400)

def moderation_rank_name(rank):
    return ADMIN_RANKS.get(int(rank), "🌿 Обычный участник") if rank else "🌿 Обычный участник"

def is_superadmin(cid, uid):
    """Абсолютные права получает создатель Telegram-чата; старого whitelist больше нет."""
    return get_admin_rank(cid, uid) >= 5

def can_moderate_target(cid, actor_uid, target_uid):
    """Иерархия Iris: нельзя менять решения/роль равного или старшего ранга."""
    actor_rank = get_admin_rank(cid, actor_uid)
    target_rank = get_admin_rank(cid, target_uid)
    return actor_rank > target_rank

def save_moderation_record(cid, action, target_uid, target_name, actor_uid, actor_name, reason="", duration=0):
    with state_lock:
        data = db_get("moderation_log", {})
        chat = data.setdefault(str(cid), [])
        chat.append({
            "action": action, "target_uid": target_uid, "target_name": target_name,
            "actor_uid": actor_uid, "actor_name": actor_name,
            "reason": reason, "duration": duration, "date": time.time()
        })
        data[str(cid)] = chat[-500:]
        db_set("moderation_log", data)

def get_warn_data(cid, uid):
    db = db_get("chat_warns", {})
    return db.get(str(cid), {}).get(str(uid), {"count": 0, "history": []})

def clear_warns(cid, uid, amount=None):
    with state_lock:
        db = db_get("chat_warns", {})
        cw = db.setdefault(str(cid), {})
        uw = cw.setdefault(str(uid), {"count": 0, "history": []})
        if amount is None:
            removed = uw.get("count", 0)
            uw["count"] = 0
            uw["history"] = []
        else:
            removed = min(amount, uw.get("count", 0))
            uw["count"] -= removed
            if removed:
                uw["history"] = uw.get("history", [])[:-removed]
        db_set("chat_warns", db)
        return removed, uw.get("count", 0)

def format_moderation_message(action, actor, target, duration=None, reason=None, extra=None):
    icons = {"ban":"🚫", "unban":"♻️", "mute":"🔇", "unmute":"🔊", "kick":"👢", "warn":"⚠️", "unwarn":"🧹"}
    titles = {
        "ban":"VIBE-БАН", "unban":"VIBE-РАЗБАН", "mute":"VIBE-ПАУЗА",
        "unmute":"VIBE-РАЗМОРОЗКА", "kick":"VIBE-КИК", "warn":"VIBE-ПРЕД",
        "unwarn":"VIBE-ОЧИСТКА"
    }
    out = f"{icons.get(action, '🛡️')} <b>{titles.get(action, action.upper())}</b>\n"
    out += f"👤 Пользователь: {target}\n"
    out += f"🛡 Модератор: {actor}"
    if duration is not None:
        out += f"\n⏱ Срок: {duration}"
    if reason:
        out += f"\n📝 Причина: {html.escape(str(reason))}"
    if extra:
        out += f"\n{extra}"
    return out

def _dk_store(cid):
    return db_get("command_access", {}).get(str(cid), {})

def get_command_threshold(cid, command):
    code = DK_ALIASES.get(command.lower().strip(), command.lower().strip())
    custom = _dk_store(cid)
    if code in custom:
        try: return int(custom[code])
        except Exception: pass
    return int(DK_DEFAULTS.get(code, 0))

def set_command_threshold(cid, command, rank):
    data = db_get("command_access", {})
    chat = data.setdefault(str(cid), {})
    chat[command.lower().strip()] = int(rank)
    db_set("command_access", data)

def get_personal_dk(cid, uid):
    return db_get("personal_command_access", {}).get(str(cid), {}).get(str(uid), {})

def set_personal_dk(cid, uid, command, enabled):
    data = db_get("personal_command_access", {})
    chat = data.setdefault(str(cid), {})
    user = chat.setdefault(str(uid), {})
    user[command.lower().strip()] = bool(enabled)
    db_set("personal_command_access", data)

def command_allowed_by_dk(cid, uid, command):
    if get_admin_rank(cid, uid) >= 5: return True
    code = DK_ALIASES.get(command.lower().strip(), command.lower().strip())
    personal = get_personal_dk(cid, uid)
    if code in personal:
        return personal[code]
    threshold = get_command_threshold(cid, code)
    if threshold >= 6: return False
    if threshold <= 0: return True
    return get_admin_rank(cid, uid) >= threshold

def save_dk_log(cid, uid, name, command, old_value, new_value):
    data = db_get("dk_log", {})
    rows = data.setdefault(str(cid), [])
    rows.append({"uid": uid, "name": name, "command": command, "old": old_value, "new": new_value, "date": time.time()})
    data[str(cid)] = rows[-500:]
    db_set("dk_log", data)

def format_dk_list(cid, uid=None):
    entries = [
        ("+модер", "+модер"), ("повысить", "повысить"), ("кто админ", "кто админ"), ("модер лог", "модер лог"),
        ("выдача варнов", "выдача варнов"), ("снятие варнов", "снятие варнов"), ("выдача мута", "выдача мута"),
        ("проверить мут", "проверить мут"), ("список мутов", "список мутов"), ("бан", "бан"), ("разбан", "разбан"),
        ("банлист", "банлист"), ("причина бана", "причина бана"), ("кик", "кик"), ("амнистия", "амнистия"),
        ("доступ команд", "доступ команд"), ("личный дк", "личный дк"),
    ]
    personal = get_personal_dk(cid, uid) if uid else {}
    lines = []
    for label, code in entries:
        threshold = get_command_threshold(cid, code)
        if code in personal:
            status = "👤" if personal[code] else "🚫"
            text = "лично разрешено" if personal[code] else "лично запрещено"
        else:
            status = "❌" if threshold >= 6 else "✅"
            text = "всем" if threshold == 0 else ("выкл." if threshold >= 6 else f"с {threshold} ранга")
        lines.append(f"{status} <code>{label}</code> — {text}")
    return "━━━━━━━VIBE━━━━━━━\n⚙️ <b>ДОСТУП КОМАНД</b>\n\n" + "\n".join(lines) + "\n\n<i>0 — всем • 1–5 — с ранга • 6 — выключено</i>\n━━━━━━━VIBE━━━━━━━"

def finish_command(m, cmd_name, sent_msg=None, ttl=None, delete_user_msg=True):
    if delete_user_msg and not getattr(m, "is_callback", False) and m.chat.type != 'private':
        try: bot.delete_message(m.chat.id, m.message_id)
        except Exception as e:
            err = str(e)
            if "message to delete not found" not in err and "message can't be deleted" not in err:
                logging.error(f"[DEL CMD:{cmd_name}] {e}")
    if sent_msg:
        track_and_replace_specific_cmd(m.chat.id, m.from_user.id, cmd_name, sent_msg)
        if ttl: auto_del(sent_msg, ttl)

def get_v(cid, k, d=False):
    with state_lock: return db_get("settings", {}).get(str(cid), {}).get(k, d)

def auto_del(msg, ttl=180):
    if msg:
        with state_lock: messages_to_delete.append({"cid": msg.chat.id, "mid": msg.message_id, "time": time.time() + ttl})

def track_and_replace_specific_cmd(chat_id, user_id, cmd_name, new_msg):
    if not new_msg: return
    with state_lock:
        key = (chat_id, user_id, cmd_name)
        if key in last_command_messages:
            try: bot.delete_message(chat_id, last_command_messages[key])
            except Exception as e:
                if "message to delete not found" not in str(e): logging.error(f"[DEL OLD CMD] {e}")
        last_command_messages[key] = new_msg.message_id


def set_chat_setting(cid, key, value):
    """Persist a single chat setting in the shared SQLite-backed settings store."""
    with state_lock:
        settings = db_get("chat_settings", {})
        chat = dict(settings.get(str(cid), {}))
        chat[key] = value
        settings[str(cid)] = chat
        db_set("chat_settings", settings)
