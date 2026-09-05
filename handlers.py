# -*- coding: utf-8 -*-
"""VIBE Bot — handlers module."""
from runtime import *
from ai import *
from autopost import *
from cleanup import *
from core import *
from games import *
from general import *
from triggers import *
from ui import *
from triggers import handle_trigger_command
from cleanup import (
    _handle_advanced_cleanup,
    _cleanup_dk_allowed,
    _cleanup_messages,
    _get_chat_setting,
    _parse_period_words,
    _record_join,
    _render_template,
    _save_welcome,
    _set_chat_rules,
)


# ============================================================
# Iris-style: темы модераторов, голосования и локальный антиспам
# ============================================================
MOD_THEME_PRESETS = {
    "классика": {0:"Участник",1:"Младший модератор",2:"Старший модератор",3:"Младший администратор",4:"Старший администратор",5:"Создатель"},
    "огонь": {0:"Новичок",1:"Страж",2:"Хранитель",3:"Администратор",4:"Главный администратор",5:"Владелец"},
    "космос": {0:"Гость",1:"Кадет",2:"Офицер",3:"Командир",4:"Капитан",5:"Командующий"},
}

def _mod_titles(cid):
    return get_v(cid, "moderator_titles", MOD_THEME_PRESETS["классика"]) or MOD_THEME_PRESETS["классика"]

def _set_mod_titles(cid, titles):
    set_chat_setting(cid, "moderator_titles", {str(k): str(v) for k,v in titles.items()})

def _format_mod_titles(cid):
    t=_mod_titles(cid)
    return "👑 <b>НАЗВАНИЯ РАНГОВ</b>\n\n" + "\n".join(f"{r} — <b>{html.escape(str(t.get(str(r), t.get(r, '—'))))}</b>" for r in range(6))

def _vote_store():
    return db_get("command_votes", {})

def _save_vote(votes):
    db_set("command_votes", votes)

def _vote_keyboard(vid):
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("✅ За", callback_data=f"vote:{vid}:yes"), types.InlineKeyboardButton("❌ Против", callback_data=f"vote:{vid}:no"))
    kb.add(types.InlineKeyboardButton("ℹ️ Инфо", callback_data=f"vote:{vid}:info"))
    return kb

def _vote_text(v):
    return (f"🗳 <b>ГОЛОСОВАНИЕ #{v['id']}</b>\n\n"
            f"Команда: <code>{html.escape(v['command'])}</code>\n"
            f"Порог: <b>{v['need']}</b> голосов\n"
            f"Минимальный ранг организатора: <b>{v['rank']}</b>\n\n"
            f"✅ За: <b>{len(v['yes'])}</b>\n❌ Против: <b>{len(v['no'])}</b>")

def _try_execute_vote(v):
    if len(v['yes']) < v['need']:
        return False
    cid=v['chat_id']; organizer=v['organizer_id']; cmd=v['command']
    try:
        fake=types.SimpleNamespace(text=cmd, from_user=types.SimpleNamespace(id=organizer, first_name=v.get('organizer_name','Организатор'), username=None), chat=bot.get_chat(cid), message_id=v.get('message_id',0), is_callback=True)
        text_handler(fake)
        return True
    except Exception as e:
        logging.error(f"[VOTE EXEC] {e}", exc_info=True)
        return False

def _topic_command(m, t_lower):
    cid, uid = m.chat.id, m.from_user.id
    if not command_allowed_by_dk(cid, uid, "темы"): return reply_no_rights(m)
    if t_lower in ("!темы", "темы", "темы модераторов"):
        presets="\n".join(f"<b>{i}.</b> {html.escape(name)}" for i,name in enumerate(MOD_THEME_PRESETS.keys(),1))
        return finish_command(m,"topics",bot.send_message(cid,"🎨 <b>ТЕМЫ МОДЕРАТОРОВ</b>\n\n"+presets+"\n\nПример: <code>!Темы огонь</code>",parse_mode="HTML"),ttl=120)
    if t_lower.startswith("!темы ") or t_lower.startswith("темы "):
        value=t_lower.split(None,1)[1].strip()
        keys=list(MOD_THEME_PRESETS)
        key=keys[int(value)-1] if value.isdigit() and 1 <= int(value) <= len(keys) else value
        if key not in MOD_THEME_PRESETS:
            return finish_command(m,"topics_err",bot.send_message(cid,"⚠️ Такой темы нет."),ttl=20)
        _set_mod_titles(cid, MOD_THEME_PRESETS[key])
        return finish_command(m,"topics_set",bot.send_message(cid,f"🎨 Тема модераторов установлена: <b>{html.escape(key)}</b>.",parse_mode="HTML"),ttl=20)
    if t_lower == "модераторы названия":
        return finish_command(m,"mod_titles",bot.send_message(cid,_format_mod_titles(cid),parse_mode="HTML"),ttl=120)
    if t_lower.startswith("модераторы названия\n"):
        if get_admin_rank(cid,uid)<5: return reply_no_rights(m)
        lines=m.text.splitlines()[1:]
        titles=dict(_mod_titles(cid))
        for line in lines:
            mm=re.match(r"\s*([0-5])\s+и=(.+?);",line)
            if mm: titles[mm.group(1)]=mm.group(2).strip()
        _set_mod_titles(cid,titles)
        return finish_command(m,"mod_titles_set",bot.send_message(cid,"✅ Названия рангов обновлены."),ttl=20)
    if t_lower.startswith("+иконка модераторов ") or t_lower.startswith("+админ иконка "):
        if get_admin_rank(cid,uid)<5: return reply_no_rights(m)
        emoji=t_lower.split()[-1][:8]; set_chat_setting(cid,"moderator_icon",emoji)
        return finish_command(m,"mod_icon",bot.send_message(cid,f"⭐ Иконка модераторов изменена на {html.escape(emoji)}."),ttl=15)
    if t_lower in ("-иконка модераторов","-админ иконка"):
        if get_admin_rank(cid,uid)<5: return reply_no_rights(m)
        set_chat_setting(cid,"moderator_icon","⭐️"); return finish_command(m,"mod_icon_off",bot.send_message(cid,"⭐️ Иконка модераторов возвращена."),ttl=15)

def _vote_command(m, t_lower):
    cid,uid=m.chat.id,m.from_user.id
    if t_lower.startswith("+гк "):
        if get_admin_rank(cid,uid)<1: return reply_no_rights(m)
        parts=m.text.split(maxsplit=3)
        try: need=int(parts[1]); rest=parts[2:]
        except Exception: return finish_command(m,"vote_err",bot.send_message(cid,"⚠️ Формат: +Гк 5 1 Бан @user"),ttl=20)
        rank=0; command=rest[-1] if rest else ""
        if len(rest)>=2 and rest[0].isdigit(): rank=int(rest[0]); command=rest[1]
        if not command: return finish_command(m,"vote_err",bot.send_message(cid,"⚠️ Укажи команду для голосования."),ttl=20)
        vid=str(int(time.time()*1000)); votes=_vote_store(); votes[vid]={"id":vid,"chat_id":cid,"organizer_id":uid,"organizer_name":m.from_user.first_name,"need":max(1,min(100,need)),"rank":max(0,min(5,rank)),"command":command,"yes":[],"no":[],"message_id":0,"created":time.time()}; _save_vote(votes)
        msg=bot.send_message(cid,_vote_text(votes[vid]),reply_markup=_vote_keyboard(vid),parse_mode="HTML"); votes[vid]["message_id"]=msg.message_id; _save_vote(votes)
        return finish_command(m,"vote_start")
    if t_lower.startswith("-гк "):
        vid=t_lower.split()[-1]; votes=_vote_store()
        if vid not in votes: return finish_command(m,"vote_stop_err",bot.send_message(cid,"⚠️ Голосование не найдено."),ttl=15)
        if votes[vid]["organizer_id"]!=uid and get_admin_rank(cid,uid)<5: return reply_no_rights(m)
        votes.pop(vid,None); _save_vote(votes); return finish_command(m,"vote_stop",bot.send_message(cid,f"🛑 Голосование #{vid} отменено."),ttl=20)
    if t_lower.startswith("гб "):
        if t_lower == "гб список":
            votes=[v for v in _vote_store().values() if v.get("chat_id")==cid and v.get("type")=="ban"]
            txt="🗳 <b>АКТИВНЫЕ ГБ</b>\n\n"+"\n".join(f"#{v['id']} — {get_user_mention(user_id=v['target_id'])}: {len(v['yes'])}/{v['need']}" for v in votes) if votes else "🗳 Активных голосований нет."
            return finish_command(m,"gb_list",bot.send_message(cid,txt,parse_mode="HTML"),ttl=60)
        target_uid,target_name,_=extract_target_and_args(m,m.text.split())
        if not target_uid: return finish_command(m,"gb_err",bot.send_message(cid,"⚠️ Укажи пользователя ответом, @username или ID."),ttl=15)
        vid=str(int(time.time()*1000)); votes=_vote_store(); votes[vid]={"id":vid,"type":"ban","chat_id":cid,"organizer_id":uid,"target_id":target_uid,"target_name":target_name,"need":5,"rank":0,"yes":[],"no":[],"created":time.time(),"message_id":0}; _save_vote(votes)
        msg=bot.send_message(cid,f"🗳 <b>ГОЛОСОВАНИЕ ЗА БАН #{vid}</b>\n\nПользователь: {get_user_mention(user_id=target_uid,first_name=target_name)}\nПорог: <b>5</b> голосов\n\n✅ За: <b>0</b>\n❌ Против: <b>0</b>",reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("✅ За",callback_data=f"gb:{vid}:yes"),types.InlineKeyboardButton("❌ Против",callback_data=f"gb:{vid}:no")),parse_mode="HTML"); votes[vid]["message_id"]=msg.message_id; _save_vote(votes); return finish_command(m,"gb_start")

def _local_antispam_join(m,new_user):
    cid=m.chat.id
    if not get_v(cid,"iris_antispam",True): return False
    spam=set(str(x) for x in (get_v(cid,"antispam_ids",[]) or []))
    if str(new_user.id) not in spam: return False
    try:
        bot.ban_chat_member(cid,new_user.id)
        bot.send_message(cid,f"🛡 Лиза заблокировала {get_user_mention(user_id=new_user.id,first_name=new_user.first_name)}: пользователь находится в локальной базе антиспама.")
    except Exception as e: logging.warning(f"[ANTISPAM] {e}")
    return True


def _antiraid_join(m, new_user):
    """Локальный анти-рейд: при всплеске входов удаляет новых участников."""
    cid = m.chat.id
    cfg = get_v(cid, "antiraid", {}) or {}
    if not cfg.get("enabled"):
        return False
    try:
        threshold = max(2, int(cfg.get("threshold", 5)))
        window = max(10, min(3600, int(cfg.get("window", 60))))
    except Exception:
        threshold, window = 5, 60
    now = time.time()
    data = db_get("antiraid_joins", {})
    rows = data.setdefault(str(cid), [])
    rows = [x for x in rows if now - float(x.get("at", 0)) <= window]
    rows.append({"id": int(new_user.id), "at": now, "name": new_user.first_name or "Участник"})
    data[str(cid)] = rows[-200:]
    db_set("antiraid_joins", data)
    if len(rows) < threshold:
        return False
    # Удаляем участников из текущего всплеска, кроме администраторов/создателя.
    removed = 0
    for item in list(rows):
        target = int(item.get("id"))
        try:
            member = bot.get_chat_member(cid, target)
            if member.status in ("creator", "administrator"):
                continue
            bot.ban_chat_member(cid, target)
            bot.unban_chat_member(cid, target, only_if_banned=True)
            removed += 1
        except Exception as e:
            logging.warning(f"[ANTIRAID] {cid}/{target}: {e}")
    data[str(cid)] = []
    db_set("antiraid_joins", data)
    if removed:
        try:
            bot.send_message(cid, f"🛡 <b>Антирейд</b>: обнаружен всплеск входов. Удалено новых участников: <b>{removed}</b>.", parse_mode="HTML")
        except Exception:
            pass
    return removed > 0

def handle_system_messages(m):
    try:
        if getattr(m, "new_chat_members", None):
            for new_user in m.new_chat_members:
                if new_user.id == BOT_ID:
                    register_chat(m.chat)
                    try:
                        bot.send_message(
                            m.chat.id,
                            "🎉 <b>Лиза установлена!</b>\n\n"
                            "Выдайте мне права администратора, чтобы я могла полноценно работать.\n"
                            "После этого используйте <code>+Правила</code>, <code>+Приветствие</code>, <code>Настройки</code> или <code>Триггеры</code>.",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logging.error(f"[INSTALL] {e}", exc_info=True)
                    continue
                if new_user.is_bot:
                    continue
                if _local_antispam_join(m, new_user):
                    continue
                if _antiraid_join(m, new_user):
                    continue
                _record_join(m.chat.id, new_user)
                # Telegram service-message sender is the inviter in the common
                # add-to-chat case. Keep this fact for Iris-style inviter queries.
                try:
                    if getattr(m, "from_user", None) and m.from_user.id != new_user.id:
                        inv = db_get("chat_invites", {})
                        inv.setdefault(str(m.chat.id), {})[str(new_user.id)] = {
                            "inviter_id": m.from_user.id,
                            "inviter_name": m.from_user.first_name or "Пользователь",
                            "date": time.time(),
                        }
                        db_set("chat_invites", inv)
                except Exception:
                    pass
                welcome = _get_chat_setting(m.chat.id, "welcome", "")
                if welcome:
                    # Iris-style: приветствуем только впервые увиденного участника.
                    data = db_get("chat_activity", {})
                    row = data.setdefault(str(m.chat.id), {}).setdefault(str(new_user.id), {})
                    already_greeted = bool(row.get("greeted", False))
                    if not already_greeted:
                        try:
                            rendered = _render_template(welcome, new_user)
                            msg = bot.send_message(m.chat.id, rendered, parse_mode="HTML")
                            row["greeted"] = True
                            row["last_greet_message_id"] = msg.message_id
                            db_set("chat_activity", data)
                            ttl = int(_get_chat_setting(m.chat.id, "welcome_delete_after", 0) or 0)
                            if ttl > 0:
                                auto_del(msg, min(ttl, 86400))
                        except Exception as e:
                            logging.error(f"[WELCOME] {e}", exc_info=True)
        if getattr(m, "left_chat_member", None):
            u=m.left_chat_member
            if u and not u.is_bot:
                cfg=get_v(m.chat.id,"autokick",{}) or {}
                exits=cfg.get("exits",0)
                if exits:
                    data=db_get("chat_exits",{}); chat=data.setdefault(str(m.chat.id),{}); row=chat.setdefault(str(u.id),{"times":[]})
                    now=time.time(); window=int(cfg.get("window",3600)); row["times"]=[x for x in row.get("times",[]) if now-x<=window]; row["times"].append(now); db_set("chat_exits",data)
                    if len(row["times"])>=int(exits):
                        try:
                            if cfg.get("action","kick")=="ban": bot.ban_chat_member(m.chat.id,u.id)
                            else: bot.ban_chat_member(m.chat.id,u.id); bot.unban_chat_member(m.chat.id,u.id,only_if_banned=True)
                        except Exception as e: logging.warning(f"[AUTOKICK] {e}")
        if get_v(m.chat.id, "del_sys", False):
            try:
                bot.delete_message(m.chat.id, m.message_id)
            except Exception as e:
                if "message to delete not found" not in str(e):
                    logging.error(f"[SYS DEL] {e}")
    except Exception as e:
        logging.error(f"[SYSTEM MSG] {e}", exc_info=True)

def cmd_start(m):
    payload = None
    if m.text:
        text_parts = m.text.split(maxsplit=1)
        if len(text_parts) > 1:
            payload = text_parts[1].strip()

    if payload and (payload.startswith("regword_") or payload.startswith("unregword_")):
        return handle_word_game_registration(m, payload)

    if not check_access(m):
        return

    if m.chat.type == "private":
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("➕ Добавить Лизу в группу", url=f"https://t.me/{BOT_USERNAME}?startgroup=true"))
        kb.add(types.InlineKeyboardButton("📖 Базовая настройка", callback_data="liza_setup_help"))
        txt = (
            "👋 <b>Привет!</b>\n\n"
            "Я <b>Лиза</b> 🌿 — чат-менеджер для Telegram.\n"
            "Чтобы установить меня, нажми кнопку ниже, выбери группу и обязательно выдай мне права администратора.\n\n"
            "После установки настрой приветствие, правила и нужные функции прямо в группе."
        )
        msg = bot.send_message(m.chat.id, txt, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        finish_command(m, "start", msg, ttl=180)
        return

    # /start в группе — подтверждение установки в стиле Iris.
    register_chat(m.chat)
    try:
        member = bot.get_chat_member(m.chat.id, BOT_ID)
        status = member.status
    except Exception:
        status = "unknown"
    if status not in ("administrator", "creator"):
        txt = (
            "🌿 <b>Лиза ещё не установлена полностью.</b>\n\n"
            "Назначьте меня администратором группы и выдайте необходимые права — "
            "это нужно для приветствий, модерации, закрепов и очистки чата."
        )
    else:
        txt = (
            "🎉 <b>Лиза установлена!</b>\n\n"
            "Теперь можно настроить:\n"
            "• <code>+Правила</code>\n"
            "• <code>+Приветствие</code>\n"
            "• <code>Настройки</code>\n"
            "• <code>Триггеры</code>\n\n"
            "Все команды можно писать с префиксом <code>!</code>, <code>.</code>, <code>/</code> "
            "или со словом «Лиза»."
        )
    msg = bot.send_message(m.chat.id, txt, parse_mode="HTML")
    finish_command(m, "start", msg, ttl=180)

def cmd_settings(m):
    if not check_access(m): return
    if m.chat.type == "private":
        if get_admin_rank(m.chat.id, m.from_user.id) < 5:
            return reply_no_rights(m)
    elif get_admin_rank(m.chat.id, m.from_user.id) < 5:
        return reply_no_rights(m)
    msg = bot.send_message(m.chat.id, "🎛 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ЛИЗОЙ</b> ✨\n\nНастрой характер и функции бота под свой чат:", reply_markup=main_kb(m.chat.id, m.chat.type == 'private'), parse_mode='HTML')
    finish_command(m, "settings", msg)

def cmd_lg(m):
    if not check_access(m): return
    uid, cid = m.from_user.id, m.chat.id
    finish_command(m, "lucky_game_cmd")
    with state_lock:
        if (cid, uid) in active_lucky_players:
            return track_and_replace_specific_cmd(cid, uid, "lucky_game", bot.send_message(cid, "⏳ Дождись конца текущей игры!"))
        active_lucky_players.add((cid, uid))
    play_lucky_game(cid, uid, m.from_user.first_name)

def cmd_llg(m):
    if not check_access(m): return
    with state_lock: ldrs = db_get("lucky_leaders", {})
    txt = "━━━━━━━VIBE━━━━━━━\n🎲 <b>РЕЙТИНГ ВЕЗУНЧИКОВ</b>\n\n"
    if not ldrs: txt += "Пока никого нет."
    else: txt += "\n".join(f"{i}. {get_user_mention(user_id=int(u), first_name=info.get('name'))} — <b>{info.get('wins',0)}</b>" for i, (u, info) in enumerate(sorted(ldrs.items(), key=lambda x: x[1].get("wins",0), reverse=True)[:10], 1))
    txt += "\n━━━━━━━VIBE━━━━━━━"
    msg = bot.send_message(m.chat.id, txt, parse_mode='HTML')
    finish_command(m, "leaders_lucky_game", msg, ttl=120)

def cmd_events(m):
    if not check_access(m): return
    evs = db_get("events", [])
    evs = evs["events"] if isinstance(evs, dict) and "events" in evs else evs
    txt = "📅 Пока никаких событий не запланировано." if not evs else "📅 <b>БЛИЖАЙШИЕ СОБЫТИЯ</b>\n\n" + "\n".join(f"🔸 <b>{html.escape(str(e.get('date')))}</b> — {html.escape(str(e.get('info')))}" for e in evs)
    msg = bot.send_message(m.chat.id, txt, parse_mode='HTML')
    finish_command(m, "events", msg, ttl=180)

def cmd_sgs(m):
    if not check_access(m): return
    caller_rank = get_admin_rank(m.chat.id, m.from_user.id)
    if caller_rank < 1 and get_admin_rank(m.chat.id, m.from_user.id) < 5: return reply_no_rights(m)
    with state_lock:
        if m.chat.id in active_safes:
            msg = bot.send_message(m.chat.id, "⚠️ Сейф уже активирован.")
            return finish_command(m, "start_game_safe", msg, ttl=30)
        active_safes[m.chat.id] = {"code": f"{random.randint(0,999):03d}", "hint_given": False, "gid": time.time()}
    safe_txt = (
        "━━━━━━━VIBE━━━━━━━\n"
        "🔐 <b>ИГРА «СЕЙФ» НАЧАЛАСЬ!</b>\n\n"
        "<i>Бронированный сейф заблокирован 3-значным кодом.</i>\n"
        "🔢 Пишите свои варианты прямо в чат — код от 000 до 999!\n\n"
        "🏆 Топ взломщиков: /leaders_safe_game\n"
        "━━━━━━━VIBE━━━━━━━"
    )
    msg = bot.send_message(m.chat.id, safe_txt, parse_mode='HTML')
    finish_command(m, "start_game_safe", msg)

def cmd_lsg(m):
    if not check_access(m): return
    msg = bot.send_message(m.chat.id, format_safe_leaderboard(), parse_mode='HTML')
    finish_command(m, "leaders_safe_game", msg, ttl=120)

def cmd_start_words(m):
    if not check_access(m): return
    caller_rank = get_admin_rank(m.chat.id, m.from_user.id)
    if caller_rank < 1 and get_admin_rank(m.chat.id, m.from_user.id) < 5: return reply_no_rights(m)
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
        msg = bot.send_message(cid, "⚠️ Неверное стартовое слово. Пример: /start_words_game арбуз", parse_mode='HTML')
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

def cmd_stop_words(m):
    if not check_access(m): return
    caller_rank = get_admin_rank(m.chat.id, m.from_user.id)
    if caller_rank < 1 and get_admin_rank(m.chat.id, m.from_user.id) < 5: return reply_no_rights(m)
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
        msg = bot.send_message(cid, "🤷‍♀️ Игра сейчас не идёт.")
        return finish_command(m, "stop_words_game", msg, ttl=20)
    if lobby and not game:
        msg = bot.send_message(cid, "🛑 Регистрация на игру отменена.")
        return finish_command(m, "stop_words_game", msg, ttl=60)
    msg = bot.send_message(cid, f"🛑 <b>Игра остановлена.</b>\nНазвано слов: {game['moves']}. Последнее: «{html.escape(game['last_word'] or '—')}»\n🔊 Лиза снова на связи в чате.", parse_mode='HTML')
    finish_command(m, "stop_words_game", msg, ttl=60)

def cmd_leaders_words(m):
    if not check_access(m): return
    msg = bot.send_message(m.chat.id, format_words_leaderboard(), parse_mode='HTML')
    finish_command(m, "leaders_words_game", msg, ttl=120)

def cmd_words_status(m):
    if not check_access(m): return
    with state_lock:
        game = active_word_games.get(m.chat.id)
        lobby = pending_word_lobbies.get(m.chat.id)
    if game:
        msg = bot.send_message(m.chat.id, f"🔤 Названо слов: <b>{game['moves']}</b>. Нужна буква «<b>{game['next_letter'].upper()}</b>».", parse_mode='HTML')
    elif lobby:
        remaining = max(0, int(lobby["end_time"] - time.time()))
        msg = bot.send_message(m.chat.id, f"📝 Идёт регистрация: {len(lobby['players'])} участник(ов). До старта ~{remaining//60}м {remaining%60}с.", parse_mode='HTML')
    else: msg = bot.send_message(m.chat.id, "😴 Игра сейчас не идёт.\nЗапустить: /start_words_game")
    finish_command(m, "words_status", msg, ttl=30)

def cb_handler(c):
    try:
        if c.data.startswith("giveaway:join:"):
            try:
                _,_,cid_s,gid=c.data.split(":",3); cid=int(cid_s); uid=c.from_user.id
                store=db_get("giveaways",{}) or {}; chat=store.get(str(cid),{}); g=chat.get(gid)
                if not g or g.get("status")!="active": return bot.answer_callback_query(c.id,"Розыгрыш завершён.",show_alert=True)
                if uid not in g["participants"]:
                    g["participants"].append(uid); db_set("giveaways",store)
                bot.answer_callback_query(c.id,"🎁 Ты участвуешь!")
                try: bot.edit_message_reply_markup(cid,c.message.message_id,reply_markup=c.message.reply_markup)
                except Exception: pass
            except Exception as e: logging.error(f"[GIVEAWAY CALLBACK] {e}",exc_info=True)
            return
        if c.data.startswith("marry:"):
            try:
                _,pid,action=c.data.split(":",2); pending=db_get("marriage_pending",{}); offer=pending.get(pid)
                if not offer: return bot.answer_callback_query(c.id,"Предложение уже недействительно.")
                if c.from_user.id != int(offer["to"]): return bot.answer_callback_query(c.id,"Принять предложение может только адресат.",show_alert=True)
                if action=="no":
                    pending.pop(pid,None); db_set("marriage_pending",pending); bot.answer_callback_query(c.id,"Предложение отклонено."); bot.edit_message_text("💔 Предложение брака отклонено.",c.message.chat.id,c.message.message_id); return
                rels=db_get("relationships",{})
                if rels.get(str(offer["from"]),{}).get("spouse_id") or rels.get(str(offer["to"]),{}).get("spouse_id"):
                    pending.pop(pid,None); db_set("marriage_pending",pending); return bot.answer_callback_query(c.id,"Брак уже невозможен.",show_alert=True)
                now=int(time.time()); rels[str(offer["from"])]={"spouse_id":int(offer["to"]),"since":now}; rels[str(offer["to"])]={"spouse_id":int(offer["from"]),"since":now}; db_set("relationships",rels)
                pending.pop(pid,None); db_set("marriage_pending",pending); bot.answer_callback_query(c.id,"💍 Брак заключён!")
                bot.edit_message_text(f"💍 Брак заключён: {get_user_mention(user_id=offer['from'],first_name=offer['from_name'])} ❤️ {get_user_mention(user_id=offer['to'],first_name=offer['to_name'])}",c.message.chat.id,c.message.message_id,parse_mode="HTML")
            except Exception as e: logging.error(f"[MARRY CALLBACK] {e}",exc_info=True)
            return
        if c.data.startswith("fun_duel:"):
            parts=c.data.split(":"); cid=int(parts[1]); target=int(parts[3])
            if c.from_user.id!=target:
                return bot.answer_callback_query(c.id,"⛔ Это предложение не тебе.",show_alert=True)
            m=types.SimpleNamespace(chat=types.SimpleNamespace(id=cid),from_user=c.from_user,text="дуэль да",message_id=c.message.message_id)
            bot.answer_callback_query(c.id,"⚔️ Дуэль принята!")
            return _duel_accept(m)
        if c.data.startswith("fun_dice:"):
            parts=c.data.split(":"); cid=int(parts[1]); target=int(parts[3])
            if c.from_user.id!=target:
                return bot.answer_callback_query(c.id,"⛔ Это предложение не тебе.",show_alert=True)
            m=types.SimpleNamespace(chat=types.SimpleNamespace(id=cid),from_user=c.from_user,text="кубы да",message_id=c.message.message_id)
            bot.answer_callback_query(c.id,"🎲 Игра принята!")
            return _dice_accept(m)
        cid, uid, d = c.message.chat.id, c.from_user.id, c.data
        is_pv = c.message.chat.type == 'private'
        parts = d.split(":")
        action = parts[0]

        if action in ("vote", "gb") and len(parts) >= 3:
            vid=parts[1]; choice=parts[2]; votes=_vote_store(); v=votes.get(vid)
            if not v: return bot.answer_callback_query(c.id,"Голосование завершено.",show_alert=True)
            if choice=="info": return bot.answer_callback_query(c.id,_vote_text(v).replace("<b>","").replace("</b>",""),show_alert=True)
            if uid not in v["yes"] and uid not in v["no"]:
                v["yes" if choice=="yes" else "no"].append(uid); _save_vote(votes)
                try: bot.edit_message_text(_vote_text(v) if v.get("type")!="ban" else f"🗳 <b>ГОЛОСОВАНИЕ ЗА БАН #{vid}</b>\n\nПользователь: {get_user_mention(user_id=v['target_id'],first_name=v.get('target_name'))}\nПорог: <b>{v['need']}</b> голосов\n\n✅ За: <b>{len(v['yes'])}</b>\n❌ Против: <b>{len(v['no'])}</b>", cid, v["message_id"], reply_markup=c.message.reply_markup, parse_mode="HTML")
                except Exception: pass
            if len(v["yes"])>=v["need"]:
                if v.get("type")=="ban":
                    try: bot.ban_chat_member(cid,v["target_id"])
                    except Exception as e: logging.warning(f"[GB BAN] {e}")
                else: _try_execute_vote(v)
                votes.pop(vid,None); _save_vote(votes)
                try: bot.edit_message_text("✅ <b>Голосование завершено.</b> Решение принято.",cid,v["message_id"],parse_mode="HTML")
                except Exception: pass
            return bot.answer_callback_query(c.id,"Голос принят.")

        if d.startswith("cmd_exec_"):
            cmd = d.split("_", 2)[2]
            if cmd.startswith("/"):
                fake = c.message
                fake.text, fake.from_user, fake.is_callback = cmd, c.from_user, True
                if cmd == "/lucky_game": cmd_lg(fake)
                elif cmd == "/leaders_lucky_game": cmd_llg(fake)
            return bot.answer_callback_query(c.id)

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
            if uid != lobby["creator_id"] and get_admin_rank(cid, uid) < 5 and get_admin_rank(cid, uid) < 3:
                return bot.answer_callback_query(c.id, "⛔ Только создатель или админ могут начать игру раньше.", show_alert=True)
            if len(lobby["players"]) < MIN_WORD_PLAYERS:
                return bot.answer_callback_query(c.id, f"⚠️ Нужно минимум {MIN_WORD_PLAYERS} участника, чтобы начать игру.", show_alert=True)
            bot.answer_callback_query(c.id, "🚀 Запускаю игру!")
            executor.submit(start_word_game_now, cid)
            return

        if d == "what_can_i_do":
            bot.answer_callback_query(c.id)
            txt_can_do = (
                "✨ <b>ЧТО Я УМЕЮ</b>\n\n"
                "🔹 Общаюсь и поддерживаю беседу\n"
                "🔹 Слежу за порядком и матом в чате\n"
                "🔹 Автопостинг по расписанию\n"
                "🔹 Игры: /lucky_game, сейф, /start_words_game\n"
                "🔹 Иногда сама встреваю в чат и ставлю реакции 🔥"
            )
            return bot.edit_message_text(txt_can_do, cid, c.message.message_id, parse_mode='HTML', reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data="back_to_start")))
            
        if d == "back_to_start":
            bot.answer_callback_query(c.id)
            kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Что ты умеешь?", callback_data="what_can_i_do"))
            if get_admin_rank(cid, uid) >= 5: kb.add(types.InlineKeyboardButton("⚙️ Настройки", callback_data="open_main_settings"))
            back_txt = (
                f"👋 Привет, {get_user_mention(c.from_user)}!\n"
                "Я <b>Лиза</b> 🌿 — слежу за порядком и веселю чат.\n\n"
            )
            return bot.edit_message_text(back_txt, cid, c.message.message_id, reply_markup=kb, parse_mode='HTML', disable_web_page_preview=True)

        if d == "liza_setup_help":
            bot.answer_callback_query(c.id)
            txt = ("⚙️ <b>УСТАНОВКА ЛИЗЫ</b>\n\n"
                   "1. Нажми «Добавить Лизу в группу».\n"
                   "2. Выбери нужную группу.\n"
                   "3. Назначь Лизу администратором и не отключай выданные права.\n"
                   "4. В группе используй <code>+Правила</code>, <code>+Приветствие</code> и <code>Настройки</code>.\n\n"
                   "Лиза работает в любой группе, куда её добавили.")
            return bot.edit_message_text(txt, cid, c.message.message_id, parse_mode="HTML",
                                         reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data="back_to_start")))
        if d == "noop":
            return bot.answer_callback_query(c.id, "ℹ️ Добавьте Лизу хотя бы в одну группу.", show_alert=True)

        if d == "open_main_settings":
            if (is_pv and get_admin_rank(cid, uid) < 5) or (not is_pv and get_admin_rank(cid, uid) < 5):
                return bot.answer_callback_query(c.id, "⛔ Только создатель чата может менять базовые настройки.", show_alert=True)
            bot.answer_callback_query(c.id)
            return bot.edit_message_text("🎛 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ЛИЗОЙ</b> ✨\n\nНастрой характер и функции бота под свой чат:", cid, c.message.message_id, reply_markup=main_kb(cid, is_pv), parse_mode='HTML')

        if d.startswith("m:") or action == "s":
            if (is_pv and get_admin_rank(cid, uid) < 5) or (not is_pv and get_admin_rank(cid, uid) < 5):
                return bot.answer_callback_query(c.id, "⛔ Только создатель чата может менять эти настройки.", show_alert=True)

        if d == "m:toggle_intervene":
            set_v(cid, "intervene", not get_v(cid, "intervene", True))
            bot.answer_callback_query(c.id)
            return bot.edit_message_reply_markup(cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))
            
        if d == "m:toggle_sys":
            set_v(cid, "del_sys", not get_v(cid, "del_sys", False))
            bot.answer_callback_query(c.id)
            return bot.edit_message_reply_markup(cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))

        if d == "m:toggle_reactions":
            set_v(cid, "random_reactions", not get_v(cid, "random_reactions", True))
            bot.answer_callback_query(c.id)
            return bot.edit_message_reply_markup(cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))

        if d == "m:toggle_butt_in":
            set_v(cid, "butt_in", not get_v(cid, "butt_in", False))
            bot.answer_callback_query(c.id)
            return bot.edit_message_reply_markup(cid, c.message.message_id, reply_markup=main_kb(cid, is_pv))

        if d in ["m:freq", "m:anger", "m:butt_in_chance"]:
            bot.answer_callback_query(c.id)
            t = {"m:freq": "freq", "m:anger": "anger", "m:butt_in_chance": "butt_in_chance"}[d]
            kb = types.InlineKeyboardMarkup(row_width=4)
            kb.add(*[types.InlineKeyboardButton(f"{v}%", callback_data=f"s:{t}:{v}") for v in [5, 10, 15, 20, 30, 50, 70, 100]])
            kb.add(types.InlineKeyboardButton("« Назад", callback_data="open_main_settings"))
            labels = {"freq": "📊 Частота ответов", "anger": "😠 Токсичность", "butt_in_chance": "🎚 Шанс вмешаться в диалог"}
            return bot.edit_message_text(f"{labels[t]} — выберите значение:", cid, c.message.message_id, reply_markup=kb)

        if action == "s":
            t, v = parts[1], parts[2]
            bot.answer_callback_query(c.id, "✅ Сохранено")
            set_v(cid, t, int(v))
            return bot.edit_message_text("🎛 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ЛИЗОЙ</b> ✨\n\nНастрой характер и функции бота под свой чат:", cid, c.message.message_id, reply_markup=main_kb(cid, is_pv), parse_mode='HTML')

        if d == "to_group_settings" and get_admin_rank(cid, uid) >= 5:
            try: bot.send_message(uid, "📢 <b>Выберите группу для автопостинга:</b>", reply_markup=chats_selection_kb(), parse_mode='HTML')
            except: return bot.answer_callback_query(c.id, "⚠️ Нажми Start в ЛС с ботом.", show_alert=True)
            return bot.answer_callback_query(c.id, "📤 Отправлено в ЛС")

        if d == "m:autopost_list":
            bot.answer_callback_query(c.id)
            return bot.edit_message_text("📢 <b>Выберите группу для автопостинга:</b>", cid, c.message.message_id, reply_markup=chats_selection_kb(), parse_mode='HTML')

        if action == "ap":
            sub = parts[1]
            if sub == "chat":
                target_cid = parts[2]
                bot.answer_callback_query(c.id)
                return bot.edit_message_text("📋 <b>Посты в этой группе:</b>", cid, c.message.message_id, reply_markup=autopost_list_kb(target_cid), parse_mode='HTML')
            elif sub == "select":
                pid = parts[2]
                with state_lock: active_fsm[uid] = {"action": "editing", "pid": pid}
                bot.answer_callback_query(c.id)
                return bot.edit_message_text(post_text_view(pid), cid, c.message.message_id, reply_markup=post_settings_kb(pid), parse_mode='HTML')
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
                bot.answer_callback_query(c.id, "✅ Пост создан")
                return bot.edit_message_text("📋 <b>Посты в этой группе:</b>", cid, c.message.message_id, reply_markup=autopost_list_kb(c_str), parse_mode='HTML')
            elif sub == "delmenu":
                bot.answer_callback_query(c.id)
                kb = types.InlineKeyboardMarkup(row_width=1)
                target_chat = parts[2]
                for p in [p for p in db_get("autopost", {"posts": []}).get("posts", []) if str(p.get("chat_id")) == target_chat]:
                    kb.add(types.InlineKeyboardButton(trim_btn_text(f"🗑 Удалить: {p.get('name', 'Пост')}"), callback_data=f"ap:delfinal:{p['id']}"))
                return bot.edit_message_text("🗑 <b>Какой пост удалить?</b>", cid, c.message.message_id, reply_markup=kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:chat:{target_chat}")), parse_mode='HTML')
            elif sub == "delfinal":
                pid = parts[2]
                with state_lock:
                    data = db_get("autopost", {"posts": []})
                    c_str = next((str(p.get("chat_id")) for p in data["posts"] if p["id"] == pid), str(cid))
                    data["posts"] = [p for p in data["posts"] if p["id"] != pid]
                    db_set("autopost", data)
                bot.answer_callback_query(c.id, "🗑 Пост удалён")
                return bot.edit_message_text("📋 <b>Посты в этой группе:</b>", cid, c.message.message_id, reply_markup=autopost_list_kb(c_str), parse_mode='HTML')
            elif sub in ["toggle", "autodel", "pin"]:
                pid = parts[2]
                with state_lock:
                    data = db_get("autopost", {"posts": []})
                    for p in data["posts"]:
                        if p["id"] == pid:
                            if sub == "toggle": p["enabled"] = not p.get("enabled", False)
                            elif sub == "autodel": p["auto_delete_prev"] = not p.get("auto_delete_prev", False)
                            else: p["pin_after_send"] = not p.get("pin_after_send", False)
                    db_set("autopost", data)
                bot.answer_callback_query(c.id, "✅")
                return bot.edit_message_text(post_text_view(pid), cid, c.message.message_id, reply_markup=post_settings_kb(pid), parse_mode='HTML')
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
                return bot.edit_message_text(post_text_view(pid), cid, c.message.message_id, reply_markup=post_settings_kb(pid), parse_mode='HTML')
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
                    "interval": "⏱ Отправьте интервал в формате: <b>45м</b>, <b>3ч</b> или <b>1д</b>.",
                    "time": "🕑 Отправьте время в формате <b>ЧЧ:ММ</b>, например: <b>14:30</b>.",
                    "date": "📅 Отправьте дату старта в формате <b>ГГГГ-ММ-ДД</b>, например: <b>2026-10-01</b>.",
                    "text": "📝 Отправьте новый текст поста.",
                    "buttons": (
                        "🔘 Отправьте кнопки, каждая строка — новый ряд, кнопки через «|»:\n\n"
                        "<code>Текст - https://ссылка.com</code>\n"
                        "<code>Текст - cmd:/lucky_game</code>"
                    ),
                    "photo": "🖼 Отправьте фото в этот чат."
                }
                return bot.send_message(cid, msgs.get(act, "✏️ Отправьте значение:"), parse_mode='HTML')
            elif sub in ["photo_menu", "btns_menu", "time_menu", "date_menu"]:
                pid = parts[2]
                bot.answer_callback_query(c.id)
                kb = types.InlineKeyboardMarkup(row_width=1)
                if sub == "photo_menu": kb.add(types.InlineKeyboardButton("🖼 Загрузить", callback_data=f"ap:photo:{pid}"), types.InlineKeyboardButton("🗑 Удалить", callback_data=f"ap:delph:{pid}"))
                elif sub == "btns_menu": kb.add(types.InlineKeyboardButton("➕ Настроить", callback_data=f"ap:btns:{pid}"), types.InlineKeyboardButton("🗑 Удалить все", callback_data=f"ap:clrbtns:{pid}"))
                elif sub == "time_menu": kb.add(types.InlineKeyboardButton("12:00", callback_data=f"ap:settime:{pid}:12:00"), types.InlineKeyboardButton("Выкл", callback_data=f"ap:settime:{pid}:OFF"), types.InlineKeyboardButton("Свое", callback_data=f"ap:custom_time:{pid}"))
                elif sub == "date_menu": kb.add(types.InlineKeyboardButton("Сразу", callback_data=f"ap:setdate:{pid}:OFF"), types.InlineKeyboardButton("Ввести", callback_data=f"ap:custom_date:{pid}"))
                sub_titles = {"photo_menu": "🖼 <b>Фото поста</b>", "btns_menu": "🔘 <b>Кнопки поста</b>", "time_menu": "🕑 <b>Ежедневное время</b>", "date_menu": "📅 <b>Дата старта</b>"}
                return bot.edit_message_text(sub_titles.get(sub, "⚙️ Настройка:"), cid, c.message.message_id, reply_markup=kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")), parse_mode='HTML')
            elif sub == "send":
                pid = parts[2]
                with state_lock:
                    data = db_get("autopost", {"posts": []})
                    post = next((p for p in data["posts"] if p["id"] == pid), None)
                if post:
                    try:
                        send_specific_post(int(post.get("chat_id", cid)), post)
                        with state_lock:
                            fresh = db_get("autopost", {"posts": []})
                            for p in fresh.get("posts", []):
                                if p["id"] == pid:
                                    p["last_post"] = time.time()
                                    p["last_msg_id"] = post.get("last_msg_id")
                                    if "last_pinned_msg_id" in post: p["last_pinned_msg_id"] = post["last_pinned_msg_id"]
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
                    bot.send_message(cid, "👁 <b>Предпросмотр поста</b>", parse_mode='HTML')
                    if post.get("photo"): bot.send_photo(cid, post.get("photo"), caption=post.get("text", ""), reply_markup=mk, parse_mode='HTML')
                    else: bot.send_message(cid, post.get("text", ""), reply_markup=mk, parse_mode='HTML')
                    return bot.send_message(cid, "☝️ Вот так этот пост увидят подписчики.", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
        bot.answer_callback_query(c.id)
    except Exception as e: logging.error(f"[CALLBACK ERROR] {e}", exc_info=True)

def on_photo(m):
    try:
        if not check_access(m): return
        uid = m.from_user.id
        if m.chat.type == 'private' and get_admin_rank(cid, uid) >= 5:
            with state_lock: fsm = active_fsm.get(uid)
            if fsm and fsm.get("action") == "photo":
                pid = fsm["pid"]
                with state_lock:
                    active_fsm.pop(uid, None)
                    data = db_get("autopost", {"posts": []})
                    for p in data["posts"]:
                        if p["id"] == pid: p["photo"] = m.photo[-1].file_id
                    db_set("autopost", data)
                bot.reply_to(m, "✅ Фото сохранено!", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
        record_xp_and_stats(m)
    except Exception as e: logging.error(f"[ON PHOTO] {e}", exc_info=True)


# ============================================================
# Развлекательный блок Iris-style: рулетка, дуэли, кубы,
# шипперинг, случайные числа и повтор текста.
# ============================================================

def _fun_state():
    return db_get("fun_state", {}) or {}

def _save_fun_state(v):
    db_set("fun_state", v)

def _target_from_message(m, parts=None):
    parts = parts or m.text.split()
    try:
        uid, name, rest = extract_target_and_args(m, parts)
        return uid, name
    except Exception:
        return None, None

def _entertainment_command(m, t_lower):
    cid, uid = m.chat.id, m.from_user.id

    # Русская рулетка. По умолчанию проигравший удаляется из чата.
    if t_lower in ("!русская рулетка", "русская рулетка", "ирис рулетка", "!рулетка", "рулетка"):
        if not command_allowed_by_dk(cid, uid, "русская рулетка"):
            return reply_no_rights(m)
        lost = random.randint(1, 6) == 1
        if lost:
            try:
                bot.delete_message(cid, m.message_id)
            except Exception:
                pass
            try:
                bot.ban_chat_member(cid, uid)
                bot.unban_chat_member(cid, uid, only_if_banned=True)
                return bot.send_message(cid, f"🔫 {get_user_mention(m.from_user)} проиграл(а) в русскую рулетку и был(а) удалён(а) из чата!")
            except Exception:
                return bot.send_message(cid, f"💥 {get_user_mention(m.from_user)} проиграл(а) в русскую рулетку!")
        return bot.send_message(cid, f"😮‍💨 {get_user_mention(m.from_user)} выжил(а) в русской рулетке.")

    # Дуэль: предложение пользователю, принятие через кнопку.
    if t_lower.startswith("дуэль ") or t_lower == "дуэль да":
        if not command_allowed_by_dk(cid, uid, "дуэль"):
            return reply_no_rights(m)
        if t_lower == "дуэль да":
            return _duel_accept(m)
        target_uid, target_name = _target_from_message(m)
        if not target_uid or target_uid == uid:
            return bot.send_message(cid, "⚠️ Укажи другого участника ответом на его сообщение или через @username.")
        state=_fun_state(); key=f"duel:{cid}"
        if key in state:
            return bot.send_message(cid,"⚠️ В этом чате уже есть активное предложение дуэли.")
        state[key]={"chat_id":cid,"from":uid,"to":target_uid,"from_name":m.from_user.first_name,"to_name":target_name,"created":time.time()}; _save_fun_state(state)
        kb=types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("⚔️ Принять дуэль",callback_data=f"fun_duel:{cid}:{uid}:{target_uid}"))
        return bot.send_message(cid,f"⚔️ {get_user_mention(m.from_user)} вызывает {get_user_mention(user_id=target_uid,first_name=target_name)} на дуэль!",reply_markup=kb)

    if t_lower in ("дуэль отмена", "-дуэль"):
        state=_fun_state(); key=f"duel:{cid}"
        d=state.get(key)
        if d and (d["from"]==uid or get_admin_rank(cid,uid)>=5):
            state.pop(key,None); _save_fun_state(state)
            return bot.send_message(cid,"🛑 Предложение дуэли отменено.")
        return bot.send_message(cid,"ℹ️ Активного предложения дуэли нет.")

    if t_lower.startswith("кубы ") or t_lower == "кубы да":
        if not command_allowed_by_dk(cid, uid, "кубы"):
            return reply_no_rights(m)
        if t_lower == "кубы да":
            return _dice_accept(m)
        target_uid, target_name = _target_from_message(m)
        if not target_uid or target_uid == uid:
            return bot.send_message(cid,"⚠️ Укажи другого участника ответом или через @username.")
        state=_fun_state(); key=f"dice:{cid}"
        if key in state:
            return bot.send_message(cid,"⚠️ В этом чате уже есть активное предложение в кубы.")
        state[key]={"chat_id":cid,"from":uid,"to":target_uid,"from_name":m.from_user.first_name,"to_name":target_name,"created":time.time()}; _save_fun_state(state)
        kb=types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("🎲 Принять игру",callback_data=f"fun_dice:{cid}:{uid}:{target_uid}"))
        return bot.send_message(cid,f"🎲 {get_user_mention(m.from_user)} предлагает {get_user_mention(user_id=target_uid,first_name=target_name)} сыграть в кубы!",reply_markup=kb)

    if t_lower == "кубы отмена":
        state=_fun_state(); key=f"dice:{cid}"; d=state.get(key)
        if d and (d["from"]==uid or get_admin_rank(cid,uid)>=5):
            state.pop(key,None); _save_fun_state(state); return bot.send_message(cid,"🛑 Игра в кубы отменена.")
        return bot.send_message(cid,"ℹ️ Активной игры в кубы нет.")

    if t_lower.startswith("рандом"):
        parts=m.text.split()
        nums=[]
        for x in parts[1:3]:
            try: nums.append(int(x))
            except ValueError: pass
        if not nums or len(nums)>2:
            return bot.send_message(cid,"🎲 Формат: <code>Рандом 100</code> или <code>Рандом 10 100</code>",parse_mode="HTML")
        a,b=(0,nums[0]) if len(nums)==1 else (nums[0],nums[1])
        if a>b: a,b=b,a
        return bot.send_message(cid,f"🎲 Случайное число: <b>{random.randint(a,b)}</b>",parse_mode="HTML")

    if t_lower.startswith("!скажи") or t_lower.startswith("скажи"):
        text=m.text.split(None,1)[1] if len(m.text.split(None,1))>1 else ""
        if not text.strip(): return bot.send_message(cid,"💬 Напиши текст после команды.")
        return bot.send_message(cid,html.escape(text),parse_mode="HTML")

    if t_lower in ("шипперим","шипперим ") or t_lower.startswith("шипперим "):
        if not command_allowed_by_dk(cid,uid,"шипперим"): return reply_no_rights(m)
        target_uid,target_name=_target_from_message(m)
        members=db_get("chat_members",{}).get(str(cid),{}) or {}
        candidates=[]
        for sid,info in members.items():
            try: sid=int(sid)
            except: continue
            if sid!=uid and not (target_uid and sid!=target_uid):
                if not info.get("is_bot",False): candidates.append((sid,info.get("name","Участник")))
        if target_uid:
            candidates=[(target_uid,target_name or "Участник")]
        if not candidates: return bot.send_message(cid,"😿 Пока некого шипперить.")
        pair=random.choice(candidates)
        state=_fun_state(); pairs=state.setdefault(f"ships:{cid}",[])
        pairs.append([uid,pair[0],time.time()]); _save_fun_state(state)
        return bot.send_message(cid,f"💞 {get_user_mention(m.from_user)} × {get_user_mention(user_id=pair[0],first_name=pair[1])}\nПохоже, у этой пары есть химия! ✨")

    if t_lower in ("пейринг","общий пейринг"):
        state=_fun_state(); pairs=state.get(f"ships:{cid}",[]) or []
        if not pairs: return bot.send_message(cid,"💞 Пейрингов пока нет.")
        lines=[]
        for a,b,_ in pairs[-20:]: lines.append(f"• {get_user_mention(user_id=a)} × {get_user_mention(user_id=b)}")
        return bot.send_message(cid,"💞 <b>ПЕЙРИНГИ</b>\n\n"+"\n".join(lines),parse_mode="HTML")

    if t_lower == "!сбросить пейринг":
        state=_fun_state(); state.pop(f"ships:{cid}",None); _save_fun_state(state)
        return bot.send_message(cid,"🧹 История пейрингов очищена.")

    if t_lower in ("+шип меня","-шип меня"):
        state=_fun_state(); key=f"shipopt:{cid}"; opts=state.get(key,{})
        opts[str(uid)] = t_lower.startswith("+")
        state[key]=opts; _save_fun_state(state)
        return bot.send_message(cid,"💞 Тебя можно шипперить." if opts[str(uid)] else "🚫 Тебя больше не будут шипперить.")

    return None


def _duel_accept(m):
    cid,uid=m.chat.id,m.from_user.id; state=_fun_state(); key=f"duel:{cid}"; d=state.get(key)
    if not d or d["to"]!=uid: return bot.send_message(cid,"⚠️ Для тебя нет активного предложения дуэли.")
    state.pop(key,None); _save_fun_state(state)
    winner,loser=(d["from"],uid) if random.choice([True,False]) else (uid,d["from"])
    try: bot.ban_chat_member(cid,loser); bot.unban_chat_member(cid,loser,only_if_banned=True)
    except Exception: pass
    return bot.send_message(cid,f"⚔️ Дуэль окончена! Победитель: {get_user_mention(user_id=winner)}\n💥 Проигравший: {get_user_mention(user_id=loser)}")


def _dice_accept(m):
    cid,uid=m.chat.id,m.from_user.id; state=_fun_state(); key=f"dice:{cid}"; d=state.get(key)
    if not d or d["to"]!=uid: return bot.send_message(cid,"⚠️ Для тебя нет активного предложения в кубы.")
    state.pop(key,None); _save_fun_state(state)
    try:
        a=bot.send_dice(cid,emoji="🎲"); b=bot.send_dice(cid,emoji="🎲")
        av=getattr(a.dice,"value",0); bv=getattr(b.dice,"value",0)
        winner=d["from"] if av>bv else d["to"] if bv>av else None
        if winner is None: return bot.send_message(cid,f"🎲 Ничья! {av} : {bv}")
        return bot.send_message(cid,f"🎲 Результат: {av} : {bv}\n🏆 Победитель: {get_user_mention(user_id=winner)}")
    except Exception as e:
        logging.error(f"[DICE] {e}"); return bot.send_message(cid,"⚠️ Не удалось запустить кубы.")



# ============================================================
# IRIS: заметки и таймеры — реальные сохранение/доставка
# ============================================================
def _parse_duration(value):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(с|сек|секунд[аы]?|м|мин|минут[аы]?|ч|час|часа|часов|д|дн|дня|дней)", value.strip().lower())
    if not m:
        return None
    n=float(m.group(1)); unit=m.group(2)
    mult=1 if unit in ('с','сек','секунда','секунды','секунд') else 60 if unit in ('м','мин','минута','минуты','минут') else 3600 if unit in ('ч','час','часа','часов') else 86400
    seconds=int(n*mult)
    return seconds if seconds>0 else None

def _notes_for(uid):
    return db_get('user_notes', {}).get(str(uid), [])

def _save_notes(uid, notes):
    data=db_get('user_notes', {}); data[str(uid)]=notes[-100:]; db_set('user_notes', data)

def _timers_worker():
    while True:
        try:
            timers=db_get('liza_timers', {})
            changed=False; now=time.time()
            for tid,t in list(timers.items()):
                if float(t.get('due',0)) > now: continue
                chat_id=int(t['chat_id']); uid=int(t['user_id']); text=t.get('text','⏰ Таймер сработал!')
                try:
                    mention=get_user_mention(user_id=uid, first_name=t.get('name','пользователь'))
                    bot.send_message(chat_id, f"⏰ {mention}, таймер сработал!\n{text}", parse_mode='HTML')
                except Exception as e:
                    logging.warning(f'[TIMER SEND] {e}')
                timers.pop(tid,None); changed=True
            if changed: db_set('liza_timers', timers)
        except Exception as e:
            logging.error(f'[TIMER WORKER] {e}')
        time.sleep(2)

def _notes_timers_command(m, t_lower, t):
    uid=m.from_user.id; cid=m.chat.id
    # Заметка: сохранить личную заметку. Формат: Заметка текст / Заметка: текст
    if t_lower in ('заметки','мои заметки','список заметок'):
        notes=_notes_for(uid)
        if not notes: txt='📝 <b>Заметки</b>\n\nУ тебя пока нет заметок.'
        else:
            lines=[f"<b>{i}</b>. {html.escape(n['text'])}" for i,n in enumerate(notes,1)]
            txt='📝 <b>Твои заметки</b>\n\n'+'\n'.join(lines)
        return finish_command(m,'notes_list',bot.send_message(cid,txt,parse_mode='HTML'),ttl=30)
    if t_lower.startswith('заметка ') or t_lower.startswith('заметка:'):
        value=t.split(':',1)[1].strip() if ':' in t.split(None,1)[0] else t.split(None,1)[1].strip()
        if not value: return finish_command(m,'notes_err',bot.send_message(cid,'⚠️ Напиши текст заметки.'),ttl=10)
        notes=_notes_for(uid); notes.append({'text':value[:2000],'created':time.time()}); _save_notes(uid,notes)
        return finish_command(m,'note_add',bot.send_message(cid,f'✅ Заметка №{len(notes)} сохранена.'),ttl=15)
    if re.fullmatch(r'(?:удалить|удали) заметку \d+',t_lower):
        idx=int(t_lower.split()[-1]); notes=_notes_for(uid)
        if idx<1 or idx>len(notes): return finish_command(m,'note_err',bot.send_message(cid,'⚠️ Такой заметки нет.'),ttl=10)
        notes.pop(idx-1); _save_notes(uid,notes)
        return finish_command(m,'note_del',bot.send_message(cid,'🗑 Заметка удалена.'),ttl=15)
    if t_lower.startswith('таймеры') or t_lower=='мои таймеры':
        timers=db_get('liza_timers',{}); own=[(k,v) for k,v in timers.items() if int(v.get('user_id',0))==uid and int(v.get('chat_id',0))==cid]
        own.sort(key=lambda x: x[1].get('due',0))
        if not own: txt='⏰ <b>Таймеры</b>\n\nАктивных таймеров нет.'
        else:
            lines=[]
            for i,(tid,v) in enumerate(own,1):
                left=max(0,int(v['due']-time.time())); lines.append(f"<b>{i}</b>. через {left//3600}ч {(left%3600)//60}м {left%60}с — {html.escape(v['text'])}")
            txt='⏰ <b>Твои таймеры</b>\n\n'+'\n'.join(lines)
        return finish_command(m,'timers_list',bot.send_message(cid,txt,parse_mode='HTML'),ttl=20)
    if t_lower.startswith('таймер '):
        parts0=t.split(None,2)
        if len(parts0)<3:
            return finish_command(m,'timer_err',bot.send_message(cid,'⚠️ Формат: <code>Таймер 10м текст</code>'),ttl=10)
        seconds=_parse_duration(parts0[1])
        if not seconds or seconds>30*86400:
            return finish_command(m,'timer_err',bot.send_message(cid,'⚠️ Время: например 30с, 10м, 2ч, 1д. Максимум — 30 дней.'),ttl=10)
        timers=db_get('liza_timers',{})
        tid=str(max([int(x) for x in timers.keys() if str(x).isdigit()] or [0])+1)
        timers[tid]={'chat_id':cid,'user_id':uid,'name':m.from_user.first_name or 'пользователь','due':time.time()+seconds,'text':parts0[2][:1000]}
        db_set('liza_timers',timers)
        return finish_command(m,'timer_add',bot.send_message(cid,f'⏰ Таймер #{tid} установлен на {parts0[1]}.'),ttl=15)
    if re.fullmatch(r'(?:удалить|отменить) таймер \d+',t_lower):
        idx=int(t_lower.split()[-1]); timers=db_get('liza_timers',{}); own=[(k,v) for k,v in timers.items() if int(v.get('user_id',0))==uid and int(v.get('chat_id',0))==cid]; own.sort(key=lambda x:x[1].get('due',0))
        if idx<1 or idx>len(own): return finish_command(m,'timer_err',bot.send_message(cid,'⚠️ Такой таймер не найден.'),ttl=10)
        timers.pop(own[idx-1][0],None); db_set('liza_timers',timers)
        return finish_command(m,'timer_del',bot.send_message(cid,'🗑 Таймер отменён.'),ttl=15)
    return None

# ============================================================
# IRIS: отношения и браки — функциональный модуль
# ============================================================


def _economy_command(m, t_lower, t):
    """Функциональная локальная валюта: баланс, ежедневный бонус, переводы и топ."""
    cid, uid = m.chat.id, m.from_user.id
    balances = db_get("iriski_balances", {})
    key = str(uid)
    row = balances.setdefault(key, {"balance": 0, "daily": 0, "earned": 0, "spent": 0})
    row.setdefault("balance", 0); row.setdefault("daily", 0); row.setdefault("earned", 0); row.setdefault("spent", 0)

    if t_lower in ("баланс", "мой баланс", "ириски", "мои ириски", "монеты"):
        return finish_command(m, "balance", bot.send_message(cid, f"💰 У тебя <b>{int(row['balance'])}</b> ирисок.", parse_mode="HTML"), ttl=15)

    if t_lower in ("бонус", "ежедневный бонус", "бонус дня"):
        now=int(time.time()); last=int(row.get("daily",0) or 0)
        if last and now-last < 86400:
            left=86400-(now-last)
            return finish_command(m,"daily_wait",bot.send_message(cid,f"🎁 Бонус уже получен. Следующий через <b>{left//3600}ч {(left%3600)//60}м</b>.",parse_mode="HTML"),ttl=15)
        import random
        amount=random.randint(20,50)
        row["balance"] += amount; row["earned"] += amount; row["daily"] = now
        balances[key]=row; db_set("iriski_balances",balances)
        return finish_command(m,"daily_bonus",bot.send_message(cid,f"🎁 Ежедневный бонус: <b>+{amount}</b> ирисок!\n💰 Баланс: <b>{row['balance']}</b>",parse_mode="HTML"),ttl=20)

    if t_lower in ("топ ирисок", "топ монет", "топ баланса"):
        rows=[]
        users=db_get("users_data",{})
        for uid_s,data in balances.items():
            amount=int(data.get("balance",0) or 0)
            if amount<=0: continue
            name=users.get(str(uid_s),{}).get("name",f"ID:{uid_s}")
            rows.append((amount,name,int(uid_s)))
        rows.sort(reverse=True,key=lambda x:x[0])
        lines=[f"<b>{i}</b>. {get_user_mention(user_id=u, first_name=name)} — <b>{amt}</b>" for i,(amt,name,u) in enumerate(rows[:10],1)]
        txt="💰 <b>ТОП ИРИСОК</b>\n\n"+("\n".join(lines) if lines else "Пока никто не накопил ириски.")
        return finish_command(m,"economy_top",bot.send_message(cid,txt,parse_mode="HTML"),ttl=30)

    if t_lower.startswith(("передать ириски ", "перевести ириски ", "дать ириски ")):
        parts=t.split()
        target_uid,target_name,args=extract_target_and_args(m,parts)
        if not target_uid:
            return finish_command(m,"transfer_err",bot.send_message(cid,"⚠️ Укажи пользователя: <code>Передать ириски @user 50</code> или ответь на его сообщение.",parse_mode="HTML"),ttl=15)
        if target_uid==uid:
            return finish_command(m,"transfer_err",bot.send_message(cid,"⚠️ Нельзя переводить ириски самому себе."),ttl=10)
        try: amount=int(args[-1])
        except Exception: amount=0
        if amount<=0 or amount>1000000:
            return finish_command(m,"transfer_err",bot.send_message(cid,"⚠️ Укажи положительное количество ирисок."),ttl=10)
        target=balances.setdefault(str(target_uid),{"balance":0,"daily":0,"earned":0,"spent":0})
        if int(row["balance"])<amount:
            return finish_command(m,"transfer_err",bot.send_message(cid,f"⚠️ Недостаточно ирисок. Баланс: {row['balance']}"),ttl=10)
        row["balance"]-=amount; row["spent"]+=amount
        target["balance"]+=amount; target["earned"]+=amount
        db_set("iriski_balances",balances)
        mention=get_user_mention(user_id=target_uid,first_name=target_name or "пользователь")
        return finish_command(m,"transfer",bot.send_message(cid,f"💸 Передано <b>{amount}</b> ирисок → {mention}.\n💰 Твой баланс: <b>{row['balance']}</b>",parse_mode="HTML"),ttl=20)
    return None



def _vip_catalog_command(m, t_lower, t):
    """Functional local VIP/catalog module. No external payments or purchase links."""
    cid, uid = m.chat.id, m.from_user.id
    vips = db_get("user_vip", {}) or {}
    balances = db_get("iriski_balances", {}) or {}
    now = int(time.time())

    def vip_until(user_id):
        return int((vips.get(str(user_id), {}) or {}).get("until", 0) or 0)

    if t_lower in ("каталог", "каталог лизы", "магазин"):
        msg = ("🛍 <b>КАТАЛОГ ЛИЗЫ</b>\n\n"
               "💎 <b>VIP на 7 дней</b> — 200 ирисок\n"
               "💎 <b>VIP на 30 дней</b> — 650 ирисок\n\n"
               "Покупка: <code>Купить VIP 7</code> или <code>Купить VIP 30</code>\n"
               "Статус: <code>VIP</code>")
        return finish_command(m, "catalog", bot.send_message(cid, msg, parse_mode="HTML"), ttl=30)

    if t_lower in ("vip", "мой vip", "мой вип", "вип"):
        until = vip_until(uid)
        if until > now:
            left = until - now
            days = left // 86400
            hours = (left % 86400) // 3600
            return finish_command(m, "vip_status", bot.send_message(cid, f"💎 VIP активен ещё <b>{days} дн. {hours} ч.</b>\nДо: <b>{datetime.fromtimestamp(until, KYIV_TZ).strftime('%d.%m.%Y %H:%M')}</b>", parse_mode="HTML"), ttl=20)
        return finish_command(m, "vip_status", bot.send_message(cid, "💎 VIP не активен. Открой <code>Каталог</code>.", parse_mode="HTML"), ttl=15)

    if t_lower.startswith("купить vip ") or t_lower.startswith("купить вип "):
        parts=t_lower.split()
        try: days=int(parts[-1])
        except Exception: days=0
        prices={7:200,30:650}
        if days not in prices:
            return finish_command(m,"vip_buy_err",bot.send_message(cid,"⚠️ Доступно: <code>Купить VIP 7</code> или <code>Купить VIP 30</code>.",parse_mode="HTML"),ttl=15)
        cost=prices[days]
        bal=balances.setdefault(str(uid),{"balance":0,"daily":0,"earned":0,"spent":0})
        if int(bal.get("balance",0))<cost:
            return finish_command(m,"vip_buy_err",bot.send_message(cid,f"⚠️ Недостаточно ирисок. Нужно <b>{cost}</b>, у тебя <b>{int(bal.get('balance',0))}</b>.",parse_mode="HTML"),ttl=15)
        old=vip_until(uid)
        start=max(now,old)
        until=start+days*86400
        bal["balance"]-=cost; bal["spent"]=int(bal.get("spent",0))+cost
        balances[str(uid)]=bal
        vips[str(uid)]={"until":until,"name":m.from_user.first_name or "Пользователь"}
        db_set("iriski_balances",balances); db_set("user_vip",vips)
        return finish_command(m,"vip_buy",bot.send_message(cid,f"💎 VIP куплен на <b>{days} дней</b> за <b>{cost}</b> ирисок.\nДо: <b>{datetime.fromtimestamp(until,KYIV_TZ).strftime('%d.%m.%Y %H:%M')}</b>",parse_mode="HTML"),ttl=25)

    if t_lower in ("кто вип", "кто випы", "випы"):
        rows=[]
        for suid,data in vips.items():
            until=int(data.get("until",0) or 0)
            if until>now:
                rows.append((until,int(suid),data.get("name","Пользователь")))
        rows.sort(reverse=True)
        lines=[f"• {get_user_mention(user_id=u,first_name=n)} — до {datetime.fromtimestamp(until,KYIV_TZ).strftime('%d.%m.%Y %H:%M')}" for until,u,n in rows[:20]]
        return finish_command(m,"vip_list",bot.send_message(cid,"💎 <b>VIP УЧАСТНИКИ</b>\n\n"+("\n".join(lines) if lines else "Активных VIP нет."),parse_mode="HTML"),ttl=60)

    if t_lower.startswith("+vip ") or t_lower.startswith("+вип ") or t_lower.startswith("-vip ") or t_lower.startswith("-вип "):
        if get_admin_rank(cid,uid)<4: return reply_no_rights(m)
        parts=t.split()
        target_uid,target_name,args=extract_target_and_args(m,parts)
        if not target_uid: return finish_command(m,"vip_admin_err",bot.send_message(cid,"⚠️ Укажи пользователя через @username или reply."),ttl=10)
        if t_lower.startswith("-"):
            vips.pop(str(target_uid),None); db_set("user_vip",vips)
            return finish_command(m,"vip_remove",bot.send_message(cid,f"🗑 VIP снят с {get_user_mention(user_id=target_uid,first_name=target_name)}.",parse_mode="HTML"),ttl=15)
        days=int(args[-1]) if args and args[-1].isdigit() else 7
        days=max(1,min(days,3650)); until=max(now,vip_until(target_uid))+days*86400
        vips[str(target_uid)]={"until":until,"name":target_name or "Пользователь"}; db_set("user_vip",vips)
        return finish_command(m,"vip_grant",bot.send_message(cid,f"💎 VIP выдан {get_user_mention(user_id=target_uid,first_name=target_name)} на <b>{days} дн.</b>",parse_mode="HTML"),ttl=20)
    return None

def _clans_command(m, t_lower, t):
    """Локальные кланы: создание, вступление, выход и управление составом."""
    cid, uid = m.chat.id, m.from_user.id
    if m.chat.type not in ("group", "supergroup"):
        return None
    if not t_lower.startswith("клан") and t_lower not in ("кланы", "мой клан"):
        return None

    clans = db_get("chat_clans", {})
    chat_clans = clans.setdefault(str(cid), {})
    members = db_get("clan_members", {})
    now = int(time.time())

    def save():
        db_set("chat_clans", clans)
        db_set("clan_members", members)

    def my_clan():
        for name, c in chat_clans.items():
            if str(uid) in c.get("members", []):
                return name, c
        return None, None

    if t_lower in ("кланы", "клан", "мой клан"):
        name, c = my_clan()
        if name:
            return finish_command(m, "clan_info", bot.send_message(cid,
                f"⚔️ <b>КЛАН: {html.escape(name)}</b>\n\n"
                f"Глава: {get_user_mention(user_id=c['owner'], first_name=c.get('owner_name','Глава'))}\n"
                f"Участников: <b>{len(c.get('members', []))}</b>\n"
                f"Создан: <b>{datetime.fromtimestamp(c.get('created', now), KYIV_TZ).strftime('%d.%m.%Y')}</b>\n\n"
                f"Вступление: <code>Клан вступить {html.escape(name)}</code>", parse_mode="HTML"), ttl=45)
        names = sorted(chat_clans)
        txt = "⚔️ <b>КЛАНЫ ЧАТА</b>\n\n" + ("\n".join(f"• <b>{html.escape(n)}</b> — {len(c.get('members', []))} чел." for n,c in chat_clans.items()) if names else "Кланов пока нет.")
        return finish_command(m, "clans", bot.send_message(cid, txt, parse_mode="HTML"), ttl=45)

    parts = t.split(maxsplit=2)
    action = parts[1].lower() if len(parts) > 1 else ""
    arg = parts[2].strip() if len(parts) > 2 else ""

    if action in ("создать", "+создать"):
        if not arg:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Формат: <code>Клан создать Название</code>", parse_mode="HTML"), ttl=15)
        if my_clan()[0]:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Ты уже состоишь в клане."), ttl=15)
        name = re.sub(r"\s+", " ", arg).strip()[:32]
        if len(name) < 2:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Название слишком короткое."), ttl=15)
        key = name.casefold()
        if any(n.casefold() == key for n in chat_clans):
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Такой клан уже существует."), ttl=15)
        chat_clans[name] = {"owner": uid, "owner_name": m.from_user.first_name or "Глава", "members": [str(uid)], "created": now}
        members.setdefault(str(uid), {})[str(cid)] = name
        save()
        return finish_command(m, "clan_create", bot.send_message(cid, f"⚔️ Клан <b>{html.escape(name)}</b> создан. Ты его глава.", parse_mode="HTML"), ttl=20)

    if action in ("вступить", "join"):
        if not arg:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Формат: <code>Клан вступить Название</code>", parse_mode="HTML"), ttl=15)
        if my_clan()[0]:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Ты уже состоишь в клане."), ttl=15)
        target = next((n for n in chat_clans if n.casefold() == arg.casefold()), None)
        if not target:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Такой клан не найден."), ttl=15)
        chat_clans[target].setdefault("members", []).append(str(uid))
        members.setdefault(str(uid), {})[str(cid)] = target
        save()
        return finish_command(m, "clan_join", bot.send_message(cid, f"⚔️ Ты вступил(а) в клан <b>{html.escape(target)}</b>.", parse_mode="HTML"), ttl=20)

    if action in ("передать", "transfer"):
        name, c = my_clan()
        if not name or int(c.get("owner", 0)) != uid:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Передать клан может только его глава."), ttl=15)
        target_uid, target_name, _ = extract_target_and_args(m, t.split())
        if not target_uid:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Ответь на сообщение участника или укажи @username."), ttl=15)
        target_uid = int(target_uid)
        if target_uid == uid:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Ты уже глава этого клана."), ttl=10)
        if str(target_uid) not in [str(x) for x in c.get("members", [])]:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Передать клан можно только его участнику."), ttl=15)
        c["owner"] = target_uid
        c["owner_name"] = target_name or "Глава"
        save()
        return finish_command(m, "clan_transfer", bot.send_message(cid, f"👑 Глава клана <b>{html.escape(name)}</b> передан {get_user_mention(user_id=target_uid, first_name=target_name)}.", parse_mode="HTML"), ttl=20)

    if action in ("кик", "исключить", "remove"):
        name, c = my_clan()
        if not name or int(c.get("owner", 0)) != uid:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Исключать участников может только глава клана."), ttl=15)
        target_uid, target_name, _ = extract_target_and_args(m, t.split())
        if not target_uid:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Ответь на сообщение участника или укажи @username."), ttl=15)
        target_uid = int(target_uid)
        if target_uid == uid:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Себя исключить нельзя. Используй передачу главы или распускание клана."), ttl=10)
        if str(target_uid) not in [str(x) for x in c.get("members", [])]:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Пользователь не состоит в твоём клане."), ttl=15)
        c["members"] = [x for x in c.get("members", []) if str(x) != str(target_uid)]
        members.setdefault(str(target_uid), {}).pop(str(cid), None)
        save()
        return finish_command(m, "clan_kick", bot.send_message(cid, f"🚪 {get_user_mention(user_id=target_uid, first_name=target_name)} исключён(а) из клана <b>{html.escape(name)}</b>.", parse_mode="HTML"), ttl=20)

    if action in ("выйти", "leave"):
        name, c = my_clan()
        if not name:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Ты не состоишь в клане."), ttl=15)
        if int(c.get("owner", 0)) == uid:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Глава не может просто выйти. Передай главу командой <code>Клан передать @user</code> или распусти клан.", parse_mode="HTML"), ttl=20)
        c["members"] = [x for x in c.get("members", []) if x != str(uid)]
        members.setdefault(str(uid), {}).pop(str(cid), None)
        save()
        return finish_command(m, "clan_leave", bot.send_message(cid, f"🚪 Ты вышел(ла) из клана <b>{html.escape(name)}</b>.", parse_mode="HTML"), ttl=20)

    if action in ("участники", "состав", "members"):
        target = arg or my_clan()[0]
        if not target:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Укажи клан: <code>Клан участники Название</code>", parse_mode="HTML"), ttl=15)
        real = next((n for n in chat_clans if n.casefold() == target.casefold()), None)
        if not real:
            return finish_command(m, "clan_err", bot.send_message(cid, "⚠️ Такой клан не найден."), ttl=15)
        c=chat_clans[real]; lines=[]
        for i,mid in enumerate(c.get("members", []),1):
            u=db_get("users_data",{}).get(str(mid),{}); nm=u.get("name", f"ID:{mid}")
            prefix="👑 " if int(mid)==int(c.get("owner",0)) else "• "
            lines.append(f"{i}. {prefix}{get_user_mention(user_id=int(mid), first_name=nm)}")
        return finish_command(m, "clan_members", bot.send_message(cid, f"⚔️ <b>{html.escape(real)}</b>\n\n"+"\n".join(lines), parse_mode="HTML"), ttl=60)

    if action in ("распустить", "delete"):
        name,c=my_clan()
        if not name or int(c.get("owner",0)) != uid:
            return finish_command(m,"clan_err",bot.send_message(cid,"⚠️ Только глава клана может его распустить."),ttl=15)
        for mid in c.get("members",[]): members.setdefault(str(mid),{}).pop(str(cid),None)
        chat_clans.pop(name,None); save()
        return finish_command(m,"clan_delete",bot.send_message(cid,f"🗑 Клан <b>{html.escape(name)}</b> распущен.",parse_mode="HTML"),ttl=20)

    return finish_command(m,"clan_help",bot.send_message(cid,
        "⚔️ <b>Команды кланов</b>\n\n"
        "<code>Клан создать Название</code>\n<code>Клан вступить Название</code>\n"
        "<code>Клан участники</code>\n<code>Клан выйти</code>\n<code>Клан передать @user</code>\n<code>Клан кик @user</code>\n<code>Клан распустить</code>\n<code>Мой клан</code>", parse_mode="HTML"), ttl=30)

def _relationships_command(m, t_lower, t):
    cid, uid = m.chat.id, m.from_user.id
    if t_lower in ("мои отношения", "отношения", "моя семья"):
        rel = db_get("relationships", {}).get(str(uid), {})
        spouse = rel.get("spouse_id")
        if spouse:
            u = db_get("users_data", {}).get(str(spouse), {})
            return finish_command(m, "rel_info", bot.send_message(cid, f"💞 Твой партнёр: {get_user_mention(user_id=spouse, first_name=u.get('name','пользователь'))}", parse_mode="HTML"), ttl=20)
        return finish_command(m, "rel_info", bot.send_message(cid, "💔 Ты сейчас ни с кем не состоишь в браке."), ttl=15)
    if t_lower in ("развод", "развестись"):
        rels=db_get("relationships",{}); spouse=rels.get(str(uid),{}).get("spouse_id")
        if not spouse: return finish_command(m,"rel_err",bot.send_message(cid,"💔 Ты не состоишь в браке."),ttl=10)
        rels.pop(str(uid),None); rels.pop(str(spouse),None); db_set("relationships",rels)
        return finish_command(m,"divorce",bot.send_message(cid,"💔 Брак расторгнут."),ttl=15)
    if t_lower.startswith(("жениться", "выйти замуж", "брак ", "свадьба ", "+брак ")):
        target_uid,target_name,_=extract_target_and_args(m,t.split())
        if not target_uid: return finish_command(m,"marry_err",bot.send_message(cid,"💍 Ответь на сообщение пользователя или укажи @username."),ttl=10)
        if target_uid==uid: return finish_command(m,"marry_err",bot.send_message(cid,"💍 Нельзя заключить брак с самим собой."),ttl=10)
        rels=db_get("relationships",{})
        if rels.get(str(uid),{}).get("spouse_id") or rels.get(str(target_uid),{}).get("spouse_id"):
            return finish_command(m,"marry_err",bot.send_message(cid,"💍 Кто-то из вас уже состоит в браке."),ttl=10)
        pending=db_get("marriage_pending",{}); pid=str(max([int(x) for x in pending if str(x).isdigit()] or [0])+1)
        pending[pid]={"from":uid,"to":target_uid,"chat_id":cid,"from_name":m.from_user.first_name or "Пользователь","to_name":target_name or "Пользователь"}; db_set("marriage_pending",pending)
        kb=types.InlineKeyboardMarkup(row_width=2); kb.add(types.InlineKeyboardButton("💍 Согласиться",callback_data=f"marry:{pid}:yes"),types.InlineKeyboardButton("❌ Отказать",callback_data=f"marry:{pid}:no"))
        return finish_command(m,"marry_offer",bot.send_message(cid,f"💍 {get_user_mention(user_id=uid,first_name=m.from_user.first_name)} предлагает брак {get_user_mention(user_id=target_uid,first_name=target_name)}.",parse_mode="HTML",reply_markup=kb),ttl=30)
    return None

# ============================================================
# IRIS: репутация и закладки — функциональные модули
# ============================================================
def _reputation_command(m, t_lower, t):
    uid, cid = m.from_user.id, m.chat.id
    if m.chat.type not in ("group", "supergroup"):
        return None
    # Репутацию можно выдать только другому участнику, обычно ответом на сообщение.
    positive = t_lower.startswith(("+реп", "+респект", "репутация +", "респект +"))
    negative = t_lower.startswith(("-реп", "-респект", "репутация -", "респект -"))
    if t_lower in ("репутация", "реп", "респект", "моя репутация", "мой респект"):
        reps = db_get("user_reputation", {}).get(str(cid), {})
        row = reps.get(str(uid), {"value": 0, "given": 0, "received": 0})
        return finish_command(m, "rep_self", bot.send_message(cid, f"⭐ Репутация: <b>{int(row.get('value',0))}</b>\n👍 Получено: {int(row.get('received',0))} • Выдано: {int(row.get('given',0))}", parse_mode="HTML"), ttl=20)
    if t_lower in ("репутация список", "топ репутации", "топ респекта"):
        reps = db_get("user_reputation", {}).get(str(cid), {})
        users = db_get("users_data", {})
        rows=[]
        for suid,r in reps.items():
            rows.append((int(r.get("value",0)), int(suid), users.get(suid,{}).get("name","Участник")))
        rows.sort(reverse=True)
        lines=[f"{i}. {get_user_mention(user_id=u, first_name=n)} — <b>{v}</b>" for i,(v,u,n) in enumerate(rows[:10],1)]
        txt="⭐ <b>ТОП РЕПУТАЦИИ</b>\n\n"+("\n".join(lines) if lines else "Пока репутация никому не выдана.")
        return finish_command(m,"rep_top",bot.send_message(cid,txt,parse_mode="HTML"),ttl=30)
    if not (positive or negative): return None
    target_uid=None; target_name=None
    if m.reply_to_message and m.reply_to_message.from_user:
        target_uid=m.reply_to_message.from_user.id; target_name=m.reply_to_message.from_user.first_name
    else:
        target_uid,target_name,_=extract_target_and_args(m,t.split())
    if not target_uid:
        return finish_command(m,"rep_err",bot.send_message(cid,"⚠️ Ответь на сообщение пользователя или укажи @username."),ttl=10)
    if int(target_uid)==int(uid):
        return finish_command(m,"rep_err",bot.send_message(cid,"⚠️ Себе репутацию выдавать нельзя."),ttl=10)
    reps=db_get("user_reputation",{}); chat=reps.setdefault(str(cid),{})
    target=chat.setdefault(str(target_uid),{"value":0,"given":0,"received":0})
    giver=chat.setdefault(str(uid),{"value":0,"given":0,"received":0})
    delta=1 if positive else -1
    target["value"]=int(target.get("value",0))+delta
    target["received"]=int(target.get("received",0))+1
    giver["given"]=int(giver.get("given",0))+1
    db_set("user_reputation",reps)
    sign="+1" if delta>0 else "−1"
    return finish_command(m,"rep_change",bot.send_message(cid,f"⭐ {get_user_mention(user_id=target_uid, first_name=target_name)} получил репутацию {sign}. Теперь: <b>{target['value']}</b>",parse_mode="HTML"),ttl=20)

def _bookmarks_command(m, t_lower, t):
    uid=m.from_user.id; cid=m.chat.id
    if t_lower in ("закладки", "мои закладки", "список закладок"):
        data=db_get("user_bookmarks",{}).get(str(uid),[])
        if not data: txt="🔖 <b>Закладки</b>\n\nУ тебя пока нет закладок."
        else:
            lines=[]
            for i,b in enumerate(data,1):
                title=html.escape(b.get("title") or "Сообщение")
                lines.append(f"<b>{i}</b>. {title} — <code>{b.get('chat_id')}</code>/{b.get('message_id')}")
            txt="🔖 <b>Твои закладки</b>\n\n"+"\n".join(lines)
        return finish_command(m,"bookmarks_list",bot.send_message(cid,txt,parse_mode="HTML"),ttl=30)
    if re.fullmatch(r"(?:удалить|удали) закладку \d+",t_lower):
        idx=int(t_lower.split()[-1]); allb=db_get("user_bookmarks",{}); data=allb.get(str(uid),[])
        if idx<1 or idx>len(data): return finish_command(m,"bookmark_err",bot.send_message(cid,"⚠️ Такой закладки нет."),ttl=10)
        data.pop(idx-1); allb[str(uid)]=data; db_set("user_bookmarks",allb)
        return finish_command(m,"bookmark_del",bot.send_message(cid,"🗑 Закладка удалена."),ttl=15)
    if t_lower in ("закладка", "добавить закладку", "+закладка"):
        msg=m.reply_to_message
        if not msg:
            return finish_command(m,"bookmark_err",bot.send_message(cid,"⚠️ Для закладки ответь на сообщение."),ttl=10)
        allb=db_get("user_bookmarks",{}); data=allb.setdefault(str(uid),[])
        title=(msg.text or msg.caption or "Сообщение").replace("\n"," ")[:100]
        data.append({"chat_id":cid,"message_id":msg.message_id,"title":title,"created":time.time()})
        allb[str(uid)]=data[-100:]; db_set("user_bookmarks",allb)
        return finish_command(m,"bookmark_add",bot.send_message(cid,f"🔖 Закладка №{len(allb[str(uid)])} сохранена."),ttl=15)
    return None


# IRIS: награды — реальные персональные награды с хранением в БД

def _awards_command(m, t_lower, t):
    cid, uid = m.chat.id, m.from_user.id
    if t_lower in ("награды", "мои награды", "мои награды", "награды мои"):
        awards = db_get("user_awards", {}).get(str(uid), [])
        if not awards:
            msg = "🏅 <b>Твои награды</b>\n\nПока наград нет."
        else:
            lines=[]
            for i,a in enumerate(awards,1):
                title=html.escape(a.get("title") or "Награда")
                by=html.escape(a.get("by_name") or "Администратор")
                date=time.strftime("%d.%m.%Y", time.localtime(a.get("date", time.time())))
                lines.append(f"<b>{i}.</b> 🏅 {title} — <i>{by}, {date}</i>")
            msg="🏅 <b>Твои награды</b>\n\n"+"\n".join(lines)
        return finish_command(m,"awards_list",bot.send_message(cid,msg,parse_mode="HTML"),ttl=30)

    # Просмотр наград конкретного пользователя.
    if t_lower.startswith("награды ") or t_lower.startswith("награды:"):
        parts=t.split(maxsplit=2)
        target_uid,target_name,args=extract_target_and_args(m, parts)
        if target_uid is None:
            return finish_command(m,"awards_err",bot.send_message(cid,"⚠️ Укажи пользователя: <code>Награды @username</code> или ответь на его сообщение.",parse_mode="HTML"),ttl=15)
        awards=db_get("user_awards",{}).get(str(target_uid),[])
        if not awards:
            msg=f"🏅 <b>Награды {html.escape(target_name or 'пользователя')}</b>\n\nНаград пока нет."
        else:
            lines=[]
            for i,a in enumerate(awards,1):
                title=html.escape(a.get("title") or "Награда")
                by=html.escape(a.get("by_name") or "Администратор")
                date=time.strftime("%d.%m.%Y", time.localtime(a.get("date", time.time())))
                lines.append(f"<b>{i}.</b> 🏅 {title} — <i>{by}, {date}</i>")
            msg=f"🏅 <b>Награды {html.escape(target_name or 'пользователя')}</b>\n\n"+"\n".join(lines)
        return finish_command(m,"awards_user",bot.send_message(cid,msg,parse_mode="HTML"),ttl=30)

    # Выдача: Наградить @user текст / Награда @user текст.
    if t_lower.startswith("наградить ") or t_lower.startswith("награда ") or t_lower.startswith("+награда "):
        if get_admin_rank(cid,uid) < 3:
            return reply_no_rights(m)
        parts=t.split(maxsplit=2)
        target_uid,target_name,args=extract_target_and_args(m, parts)
        if target_uid is None:
            return finish_command(m,"award_err",bot.send_message(cid,"⚠️ Укажи пользователя через @username, ID или ответом на сообщение."),ttl=15)
        title=" ".join(args).strip() if args else ""
        if not title:
            return finish_command(m,"award_err",bot.send_message(cid,"⚠️ После пользователя укажи название награды."),ttl=15)
        awards=db_get("user_awards",{})
        rows=awards.setdefault(str(target_uid),[])
        rows.append({"title":title[:120],"by_uid":uid,"by_name":m.from_user.first_name or "Администратор","chat_id":cid,"date":time.time()})
        awards[str(target_uid)]=rows[-100:]
        db_set("user_awards",awards)
        mention=get_user_mention(user_id=target_uid, first_name=target_name)
        return finish_command(m,"award_add",bot.send_message(cid,f"🏅 {mention} получил(а) награду: <b>{html.escape(title[:120])}</b>",parse_mode="HTML"),ttl=20)

    # Снятие награды по номеру: -награда @user N / Снять награду @user N.
    if t_lower.startswith("-награда ") or t_lower.startswith("снять награду "):
        if get_admin_rank(cid,uid) < 3:
            return reply_no_rights(m)
        parts=t.split()
        target_uid,target_name,args=extract_target_and_args(m, parts)
        if target_uid is None or not args or not args[-1].isdigit():
            return finish_command(m,"award_err",bot.send_message(cid,"⚠️ Формат: <code>-Награда @user 1</code> или ответом на сообщение.",parse_mode="HTML"),ttl=15)
        idx=int(args[-1]); awards=db_get("user_awards",{}); rows=awards.get(str(target_uid),[])
        if idx<1 or idx>len(rows):
            return finish_command(m,"award_err",bot.send_message(cid,"⚠️ Такой награды нет."),ttl=15)
        removed=rows.pop(idx-1); awards[str(target_uid)]=rows; db_set("user_awards",awards)
        return finish_command(m,"award_del",bot.send_message(cid,f"🗑 Награда <b>{html.escape(removed.get('title','Награда'))}</b> снята.",parse_mode="HTML"),ttl=20)
    return None


# IRIS: круги — пользовательские сообщества внутри чата.
def _circles_store():
    return db_get("circles", {}) or {}

def _save_circles(x):
    db_set("circles", x)

def _circles_command(m, t_lower, t):
    cid, uid = m.chat.id, m.from_user.id
    circles = _circles_store()
    chat = circles.setdefault(str(cid), {})

    if t_lower in ("круги", "мои круги", "круги мои"):
        mine=[]
        for name,c in chat.items():
            if uid in [int(x) for x in c.get("members",[])]: mine.append(name)
        if t_lower == "круги":
            rows=[f"• <b>{html.escape(n)}</b> — {len(c.get('members',[]))} чел." for n,c in chat.items()]
            msg="⭕ <b>КРУГИ</b>\n\n"+("\n".join(rows) if rows else "Кругов пока нет.")
        else:
            msg="⭕ <b>МОИ КРУГИ</b>\n\n"+("\n".join(f"• {html.escape(n)}" for n in mine) if mine else "Ты пока не состоишь ни в одном круге.")
        return finish_command(m,"circles_list",bot.send_message(cid,msg,parse_mode="HTML"),ttl=30)

    if t_lower.startswith("круг создать ") or t_lower.startswith("+круг "):
        name=t.split(maxsplit=2)[2].strip() if len(t.split(maxsplit=2))>=3 else ""
        name=name[:40]
        if not name: return finish_command(m,"circle_err",bot.send_message(cid,"⚠️ Формат: <code>Круг создать Название</code>",parse_mode="HTML"),ttl=10)
        key=name.casefold()
        if key in {k.casefold() for k in chat}: return finish_command(m,"circle_err",bot.send_message(cid,"⚠️ Такой круг уже существует."),ttl=10)
        chat[name]={"owner":uid,"owner_name":m.from_user.first_name or "Участник","members":[uid],"created":time.time()}
        _save_circles(circles)
        return finish_command(m,"circle_create",bot.send_message(cid,f"⭕ Круг <b>{html.escape(name)}</b> создан. Ты его создатель.",parse_mode="HTML"),ttl=20)

    if t_lower.startswith("круг вступить ") or t_lower.startswith("+круг вступить "):
        name=t.split(maxsplit=2)[2].strip() if len(t.split(maxsplit=2))>=3 else ""
        found=next((k for k in chat if k.casefold()==name.casefold()),None)
        if not found: return finish_command(m,"circle_err",bot.send_message(cid,"⚠️ Такой круг не найден."),ttl=10)
        members=chat[found].setdefault("members",[])
        if uid in [int(x) for x in members]: return finish_command(m,"circle_err",bot.send_message(cid,"Ты уже состоишь в этом круге."),ttl=10)
        members.append(uid); _save_circles(circles)
        return finish_command(m,"circle_join",bot.send_message(cid,f"⭕ Ты вступил(а) в круг <b>{html.escape(found)}</b>.",parse_mode="HTML"),ttl=15)

    if t_lower in ("круг выйти","-круг") or t_lower.startswith("круг выйти "):
        name=t.split(maxsplit=2)[2].strip() if len(t.split(maxsplit=2))>=3 else ""
        found=next((k for k in chat if k.casefold()==name.casefold()),None) if name else next((k for k,c in chat.items() if uid in [int(x) for x in c.get('members',[])]),None)
        if not found: return finish_command(m,"circle_err",bot.send_message(cid,"⚠️ Ты не состоишь в указанном круге."),ttl=10)
        c=chat[found]
        if int(c.get("owner"))==uid: return finish_command(m,"circle_err",bot.send_message(cid,"⚠️ Создатель не может выйти. Передай круг или удали его."),ttl=10)
        c["members"]=[x for x in c.get("members",[]) if int(x)!=uid]; _save_circles(circles)
        return finish_command(m,"circle_leave",bot.send_message(cid,f"Ты вышел(а) из круга <b>{html.escape(found)}</b>.",parse_mode="HTML"),ttl=15)

    if t_lower.startswith("круг участники"):
        name=t.split(maxsplit=2)[2].strip() if len(t.split(maxsplit=2))>=3 else ""
        found=next((k for k in chat if k.casefold()==name.casefold()),None) if name else None
        if not found: return finish_command(m,"circle_err",bot.send_message(cid,"⚠️ Формат: <code>Круг участники Название</code>",parse_mode="HTML"),ttl=10)
        members=chat[found].get("members",[]); rows=[]
        for x in members:
            try: rows.append("• "+get_user_mention(user_id=int(x)))
            except Exception: rows.append(f"• <code>{x}</code>")
        msg=f"⭕ <b>{html.escape(found)}</b>\nСоздатель: {get_user_mention(user_id=int(chat[found]['owner']),first_name=chat[found].get('owner_name'))}\n\n"+"\n".join(rows)
        return finish_command(m,"circle_members",bot.send_message(cid,msg,parse_mode="HTML"),ttl=60)

    if t_lower.startswith("круг удалить ") or t_lower.startswith("-круг удалить "):
        name=t.split(maxsplit=2)[2].strip() if len(t.split(maxsplit=2))>=3 else ""
        found=next((k for k in chat if k.casefold()==name.casefold()),None)
        if not found: return finish_command(m,"circle_err",bot.send_message(cid,"⚠️ Такой круг не найден."),ttl=10)
        if int(chat[found].get("owner"))!=uid and get_admin_rank(cid,uid)<5: return reply_no_rights(m)
        chat.pop(found,None); _save_circles(circles)
        return finish_command(m,"circle_delete",bot.send_message(cid,f"🗑 Круг <b>{html.escape(found)}</b> удалён.",parse_mode="HTML"),ttl=15)

    if t_lower.startswith("круг помощь") or t_lower=="круг":
        msg=("⭕ <b>КРУГИ</b>\n\n<code>Круг создать Название</code>\n<code>Круг вступить Название</code>\n<code>Круг участники Название</code>\n<code>Круг выйти</code>\n<code>Круг удалить Название</code>\n<code>Мои круги</code>\n<code>Круги</code>")
        return finish_command(m,"circle_help",bot.send_message(cid,msg,parse_mode="HTML"),ttl=30)
    return None


def _giveaway_command(m, t_lower, t):
    """Functional local giveaways: create, join, status and draw."""
    cid, uid = m.chat.id, m.from_user.id
    if m.chat.type not in ("group", "supergroup"):
        return None
    store = db_get("giveaways", {}) or {}
    chat = store.setdefault(str(cid), {})
    if t_lower in ("розыгрыш", "розыгрыш помощь", "розыгрыши"):
        txt=("🎁 <b>РОЗЫГРЫШ</b>\n\n"
             "<code>Розыгрыш создать N приз</code> — создать розыгрыш на N победителей\n"
             "<code>Розыгрыш список</code> — активные розыгрыши\n"
             "<code>Розыгрыш участвовать ID</code> — вступить\n"
             "<code>Розыгрыш завершить ID</code> — выбрать победителей")
        return finish_command(m,"giveaway_help",bot.send_message(cid,txt,parse_mode="HTML"),ttl=40)
    if t_lower == "розыгрыш список":
        rows=[]
        for gid,g in chat.items():
            if g.get("status")!="active": continue
            rows.append(f"🎁 <b>#{gid}</b> — {html.escape(g['prize'])}; победителей: <b>{g['winners']}</b>; участников: <b>{len(g['participants'])}</b>")
        txt="🎁 <b>АКТИВНЫЕ РОЗЫГРЫШИ</b>\n\n"+("\n".join(rows) if rows else "Активных розыгрышей нет.")
        return finish_command(m,"giveaway_list",bot.send_message(cid,txt,parse_mode="HTML"),ttl=60)
    mm=re.match(r"розыгрыш\s+создать\s+(\d+)\s+(.+)$", t_lower, re.S)
    if mm:
        if get_admin_rank(cid,uid)<1: return reply_no_rights(m)
        winners=max(1,min(100,int(mm.group(1))))
        prize=t.split(None,3)[3].strip() if len(t.split(None,3))>=4 else mm.group(2).strip()
        gid=str(int(time.time()*1000))
        chat[gid]={"id":gid,"creator":uid,"prize":prize,"winners":winners,"participants":[uid],"status":"active","created":time.time()}
        db_set("giveaways",store)
        kb=types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("🎁 Участвовать",callback_data=f"giveaway:join:{cid}:{gid}"))
        msg=bot.send_message(cid,f"🎁 <b>РОЗЫГРЫШ #{gid}</b>\n\nПриз: <b>{html.escape(prize)}</b>\nПобедителей: <b>{winners}</b>\nУчастников: <b>1</b>",parse_mode="HTML",reply_markup=kb)
        chat[gid]["message_id"]=msg.message_id; db_set("giveaways",store)
        return finish_command(m,"giveaway_create",msg,ttl=None)
    mm=re.match(r"розыгрыш\s+участвовать\s+(\d+)$", t_lower)
    if mm:
        gid=mm.group(1); g=chat.get(gid)
        if not g or g.get("status")!="active": return finish_command(m,"giveaway_err",bot.send_message(cid,"⚠️ Розыгрыш не найден или уже завершён."),ttl=15)
        if uid not in g["participants"]: g["participants"].append(uid); db_set("giveaways",store)
        return finish_command(m,"giveaway_join",bot.send_message(cid,f"🎁 Ты участвуешь в розыгрыше <b>#{gid}</b>.",parse_mode="HTML"),ttl=15)
    mm=re.match(r"розыгрыш\s+завершить\s+(\d+)$", t_lower)
    if mm:
        if get_admin_rank(cid,uid)<1: return reply_no_rights(m)
        gid=mm.group(1); g=chat.get(gid)
        if not g or g.get("status")!="active": return finish_command(m,"giveaway_err",bot.send_message(cid,"⚠️ Активный розыгрыш не найден."),ttl=15)
        import random
        pool=list(dict.fromkeys(g.get("participants",[]))); random.shuffle(pool)
        winners=pool[:min(g["winners"],len(pool))]; g["status"]="finished"; g["finished"] = time.time(); g["winner_ids"]=winners; db_set("giveaways",store)
        if winners:
            mentions=", ".join(get_user_mention(user_id=x) for x in winners)
            txt=f"🏆 <b>РОЗЫГРЫШ #{gid} ЗАВЕРШЁН!</b>\n\nПриз: <b>{html.escape(g['prize'])}</b>\nПобедители: {mentions}"
        else: txt=f"🏆 <b>РОЗЫГРЫШ #{gid}</b> завершён, но участников не было."
        return finish_command(m,"giveaway_draw",bot.send_message(cid,txt,parse_mode="HTML"),ttl=60)
    return None

def _reports_command(m, t_lower, t):
    """Functional chat reports: submit, list and close moderator reports."""
    cid, uid = m.chat.id, m.from_user.id
    if m.chat.type not in ("group", "supergroup"):
        return None
    reports = db_get("chat_reports", {}) or {}
    chat = reports.setdefault(str(cid), {})

    if t_lower in ("репорты", "жалобы"):
        if get_admin_rank(cid, uid) < 1:
            return reply_no_rights(m)
        rows=[]
        for rid, r in sorted(chat.items(), key=lambda x: x[1].get("created",0), reverse=True):
            if r.get("status") != "open": continue
            rows.append(f"#{rid} — {get_user_mention(user_id=r['from_id'], first_name=r.get('from_name'))}: {html.escape(r.get('text',''))}")
        txt="🚨 <b>ЖАЛОБЫ</b>\n\n" + ("\n".join(rows) if rows else "Открытых жалоб нет.")
        return finish_command(m,"reports_list",bot.send_message(cid,txt,parse_mode="HTML"),ttl=60)

    if t_lower.startswith(("жалоба ", "репорт ")):
        reason=t.split(None,1)[1].strip() if len(t.split(None,1))>1 else ""
        if not reason and getattr(m,"reply_to_message",None): reason="Жалоба на сообщение выше"
        if not reason:
            return finish_command(m,"report_err",bot.send_message(cid,"⚠️ Напиши причину: <code>Жалоба причина</code> или ответь на сообщение." ,parse_mode="HTML"),ttl=15)
        target=getattr(m,"reply_to_message",None)
        target_user=getattr(target,"from_user",None) if target else None
        rid=str(int(time.time()*1000))
        chat[rid]={"from_id":uid,"from_name":m.from_user.first_name,"target_id":getattr(target_user,"id",None),"target_name":getattr(target_user,"first_name",None),"text":reason,"created":time.time(),"status":"open"}
        db_set("chat_reports",reports)
        mention=get_user_mention(user_id=target_user.id, first_name=target_user.first_name) if target_user else "не указан"
        msg=bot.send_message(cid,f"🚨 Жалоба <b>#{rid}</b> принята.\nНа: {mention}\nПричина: {html.escape(reason)}\n\nМодераторы могут посмотреть её командой <code>Репорты</code>.",parse_mode="HTML")
        return finish_command(m,"report_new",msg,ttl=30)

    if t_lower.startswith(("-репорт ", "закрыть репорт ", "закрыть жалобу ")):
        if get_admin_rank(cid, uid) < 1: return reply_no_rights(m)
        rid=t.split()[-1]
        if rid not in chat or chat[rid].get("status") != "open":
            return finish_command(m,"report_close_err",bot.send_message(cid,"⚠️ Открытая жалоба с таким ID не найдена."),ttl=15)
        chat[rid]["status"]="closed"; chat[rid]["closed_by"]=uid; chat[rid]["closed_at"]=time.time(); db_set("chat_reports",reports)
        return finish_command(m,"report_close",bot.send_message(cid,f"✅ Жалоба #{rid} закрыта."),ttl=15)

    if t_lower in ("репорт помощь", "жалобы помощь"):
        return finish_command(m,"report_help",bot.send_message(cid,"🚨 <b>Репорты</b>\n<code>Жалоба причина</code> — создать жалобу\n<code>Репорты</code> — список для модераторов\n<code>Закрыть жалобу ID</code> — закрыть жалобу",parse_mode="HTML"),ttl=30)
    return None

def text_handler(m):
    try:
        if m.text:
            fun_result = _entertainment_command(m, m.text.lower().strip())
            if fun_result is not None:
                return fun_result
        # Нормализация Iris-style префиксов до разбора команд.
        if m.text:
            raw = m.text.strip()
            first, *rest = raw.split(maxsplit=1)
            if first.startswith(("!", ".", "/")) and len(first) > 1 and not first.startswith("//"):
                raw = first[1:] + ((" " + rest[0]) if rest else "")
            elif first.lower() == "лиза":
                raw = rest[0].strip() if rest else "помощь"
            m.text = raw
            _tr=handle_trigger_command(m)
            if _tr is not None: return
        if not check_access(m): return
        if m.text and m.chat.type in ["group", "supergroup"]:
            _ac=_handle_advanced_cleanup(m)
            if _ac: return
        uid, cid, t = m.from_user.id, m.chat.id, (m.text or "").strip()
        # Iris-style command prefixes and обращение к Лизе:
        # !команда, .команда, /команда и «Лиза команда» работают одинаково.
        if t:
            first, *rest = t.split(maxsplit=1)
            if first.startswith(("!", ".", "/")) and len(first) > 1 and not first.startswith("//"):
                first = first[1:]
                t = first + ((" " + rest[0]) if rest else "")
            elif first.lower() == "лиза":
                t = rest[0].strip() if rest else "помощь"
        fname, uname = m.from_user.first_name, m.from_user.username
        boss = get_admin_rank(cid, uid) >= 5

        # Track activity even for commands; this is the basis for cleanup tools.
        if m.chat.type in ["group", "supergroup"] and not m.from_user.is_bot:
            try: _record_join(cid, m.from_user)
            except Exception: pass

        t_lower = t.lower()

        # Следующий блок Iris: темы модераторов, голосования и локальный антиспам.
        if (t_lower in ("темы", "!темы", "темы модераторов", "модераторы названия") or t_lower.startswith(("темы ", "!темы ", "модераторы названия\n", "+иконка модераторов ", "-иконка модераторов", "+админ иконка ", "-админ иконка"))):
            result=_topic_command(m,t_lower)
            if result is not None: return result
        if t_lower.startswith(("+гк ", "-гк ", "гб ")):
            result=_vote_command(m,t_lower)
            if result is not None: return result
        if t_lower in ("+ирис антиспам", "-ирис антиспам", "+ирис спам", "-ирис спам"):
            if get_admin_rank(cid,uid)<5: return reply_no_rights(m)
            enabled=t_lower.startswith("+"); set_chat_setting(cid,"iris_antispam",enabled)
            return finish_command(m,"iris_antispam",bot.send_message(cid,f"🛡 Локальный Ирис-антиспам {'включён' if enabled else 'выключен'}."),ttl=15)
        if t_lower.startswith("+антиспам "):
            if get_admin_rank(cid,uid)<5: return reply_no_rights(m)
            ids=get_v(cid,"antispam_ids",[]) or []; value=t_lower.split()[-1]
            if value.isdigit() and value not in [str(x) for x in ids]: ids.append(value)
            set_chat_setting(cid,"antispam_ids",ids)
            return finish_command(m,"antispam_add",bot.send_message(cid,"🛡 Пользователь добавлен в локальную базу антиспама."),ttl=15)

        # --- Дополнительные команды Iris: модерация и управление доступом ---
        # Эти команды обрабатываются до общего диспетчера, чтобы поддерживать
        # многострочные алиасы Iris и варианты с префиксами ! . /.
        if t_lower in ("!снимаю полномочия", "снимаю полномочия", "ухожу в отставку", "разжаловать меня"):
            if get_admin_rank(cid, uid) <= 0:
                return reply_no_rights(m)
            set_admin_rank(cid, uid, 0)
            save_moderation_record(cid, "resign", uid, fname, uid, fname)
            return finish_command(m, "resign", bot.send_message(cid, f"📉 {get_user_mention(user_id=uid, first_name=fname)} снял с себя полномочия модератора." , parse_mode="HTML"))

        if t_lower in ("восстановить создателя", "восстановить владельца", "восстановить права", "хозяин вернулся", "хв"):
            try:
                member = bot.get_chat_member(cid, uid)
                if member.status != "creator":
                    return reply_no_rights(m)
            except Exception:
                return reply_no_rights(m)
            set_admin_rank(cid, uid, 5)
            return finish_command(m, "restore_creator", bot.send_message(cid, "👑 Права создателя восстановлены."), ttl=15)

        if t_lower in ("снять вышедших", "разжаловать вышедших"):
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            admins = db_get("chat_admins", {}).get(str(cid), {})
            removed = 0
            for auid in list(admins):
                try:
                    member = bot.get_chat_member(cid, int(auid))
                    if member.status in ("left", "kicked"):
                        set_admin_rank(cid, int(auid), 0); removed += 1
                except Exception:
                    # Если Telegram больше не разрешает получить участника,
                    # не снимаем роль автоматически.
                    continue
            return finish_command(m, "remove_left_admins", bot.send_message(cid, f"🧹 Снято полномочий у вышедших модераторов: <b>{removed}</b>.", parse_mode="HTML"), ttl=15)

        if t_lower in ("снять всех", "разжаловать всех", "!снять всех", "!разжаловать всех"):
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            try:
                creator_id = bot.get_chat(cid).get("id")
            except Exception:
                creator_id = uid
            admins = db_get("chat_admins", {}).get(str(cid), {})
            removed = 0
            for auid in list(admins):
                if int(auid) == uid:
                    continue
                if int(auid) == creator_id:
                    continue
                set_admin_rank(cid, int(auid), 0); removed += 1
            return finish_command(m, "remove_all_admins", bot.send_message(cid, f"🧹 Снято полномочий: <b>{removed}</b>.", parse_mode="HTML"), ttl=15)

        # Полный набор сокращённых способов назначения ранга из Iris.
        if t_lower.startswith("!!модер ") or t_lower.startswith("!!!модер ") or t_lower.startswith("!!!!модер "):
            bangs = len(t_lower.split()[0]) - 1
            rank = min(5, bangs)
            t = "+модер " + str(rank) + t[len(t_lower.split()[0]):]
            t_lower = t.lower()
            parts = t.split()
            cmd = parts[0].lower()
        if t_lower.startswith("+админ "):
            parts = t.split()
            if len(parts) > 1 and parts[1].isdigit():
                pass

        # Настройки срока варнов/мутов/банов, как в Iris.
        if t_lower.startswith("варны лимит "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            try: value = max(1, int(t_lower.split()[-1]))
            except Exception: return finish_command(m, "warn_limit_err", bot.send_message(cid, "⚠️ Укажи число предупреждений."), ttl=10)
            set_chat_setting(cid, "max_warns", value)
            return finish_command(m, "warn_limit", bot.send_message(cid, f"⚠️ Лимит предупреждений: <b>{value}</b>.", parse_mode="HTML"), ttl=15)
        if t_lower.startswith("варны чс "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            period = _parse_period_words(t_lower)
            if not period: return finish_command(m, "warn_ban_period_err", bot.send_message(cid, "⚠️ Формат: Варны ЧС 7 дней."), ttl=10)
            set_chat_setting(cid, "warn_ban_period", period)
            return finish_command(m, "warn_ban_period", bot.send_message(cid, f"🚫 Срок наказания по лимиту варнов: <b>{format_seconds_human(period)}</b>.", parse_mode="HTML"), ttl=15)
        if t_lower.startswith("варны период "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            period = _parse_period_words(t_lower)
            if not period: return finish_command(m, "warn_period_err", bot.send_message(cid, "⚠️ Формат: Варны период 7 дней."), ttl=10)
            set_chat_setting(cid, "warn_period", period)
            return finish_command(m, "warn_period", bot.send_message(cid, f"⏳ Срок хранения варна: <b>{format_seconds_human(period)}</b>.", parse_mode="HTML"), ttl=15)
        if t_lower.startswith("мут период "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            period = _parse_period_words(t_lower)
            if not period or period < 60: return finish_command(m, "mute_period_err", bot.send_message(cid, "⚠️ Минимальный срок мута по умолчанию — 1 минута."), ttl=10)
            set_chat_setting(cid, "mute_period", period)
            return finish_command(m, "mute_period", bot.send_message(cid, f"🔇 Срок мута по умолчанию: <b>{format_seconds_human(period)}</b>.", parse_mode="HTML"), ttl=15)
        if t_lower.startswith("бан период "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            period = _parse_period_words(t_lower)
            if not period: return finish_command(m, "ban_period_err", bot.send_message(cid, "⚠️ Формат: Бан период 30 дней."), ttl=10)
            set_chat_setting(cid, "ban_period", period)
            return finish_command(m, "ban_period", bot.send_message(cid, f"🚫 Срок бана по умолчанию: <b>{format_seconds_human(period)}</b>.", parse_mode="HTML"), ttl=15)

        # Управление уведомлениями о доступности команд.
        if t_lower in ("+команды", "-команды"):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            enabled = t_lower.startswith("+")
            set_chat_setting(cid, "command_notifications", enabled)
            return finish_command(m, "command_notifications", bot.send_message(cid, f"🔔 Оповещения о доступности команд {'включены' if enabled else 'выключены'}."), ttl=10)

        # Сброс глобального/раздельного ДК.
        if t_lower == "сброс команд":
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            data = db_get("command_access", {}); data.pop(str(cid), None); db_set("command_access", data)
            return finish_command(m, "reset_dk", bot.send_message(cid, "♻️ Настройки доступа команд полностью сброшены."), ttl=15)
        if t_lower.startswith("сброс дк "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            section = t_lower.split(maxsplit=2)[2]
            data = db_get("command_access", {}); chat = data.get(str(cid), {})
            for key in list(chat):
                if key == section or key.startswith(section + " "):
                    chat.pop(key, None)
            data[str(cid)] = chat; db_set("command_access", data)
            return finish_command(m, "reset_dk_section", bot.send_message(cid, f"♻️ Настройки ДК раздела <code>{html.escape(section)}</code> сброшены.", parse_mode="HTML"), ttl=15)

        # Личные исключения ДК: просмотр и сброс.
        if t_lower == "все лдк":
            if not command_allowed_by_dk(cid, uid, "личный дк"): return reply_no_rights(m)
            data = db_get("personal_command_access", {}).get(str(cid), {})
            rows=[]
            users=db_get("users_data", {})
            for auid, commands in data.items():
                enabled=[c for c,v in commands.items() if v]
                disabled=[c for c,v in commands.items() if not v]
                if enabled or disabled:
                    name=users.get(str(auid),{}).get("name", f"ID:{auid}")
                    rows.append(f"• {get_user_mention(user_id=int(auid), first_name=name)}: +{', '.join(enabled) or '—'} / -{', '.join(disabled) or '—'}")
            txt="👤 <b>ЛИЧНЫЕ ДОСТУПЫ</b>\n\n"+("\n".join(rows) or "Исключений нет.")
            return finish_command(m, "all_ldk", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)
        if t_lower.startswith("сброс всех лдк"):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            data=db_get("personal_command_access", {}); data.pop(str(cid), None); db_set("personal_command_access", data)
            return finish_command(m, "reset_all_ldk", bot.send_message(cid, "♻️ Все личные доступы ДК сброшены."), ttl=15)
        if t_lower.startswith("сброс лдк "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            target_uid, target_name, _ = extract_target_and_args(m, parts)
            if not target_uid: return finish_command(m, "reset_ldk_err", bot.send_message(cid, "⚠️ Укажи пользователя ответом, @username или ID."), ttl=10)
            data=db_get("personal_command_access", {}); data.setdefault(str(cid), {}).pop(str(target_uid), None); db_set("personal_command_access", data)
            return finish_command(m, "reset_ldk", bot.send_message(cid, f"♻️ Личные доступы {get_user_mention(user_id=target_uid, first_name=target_name)} сброшены.", parse_mode="HTML"), ttl=15)

        # --- Настройка чата / правила / приветствие ---
        if t_lower in ("правила", "правила помощь"):
            if t_lower == "правила помощь":
                txt = ("📜 <b>ЛИЗА — ПРАВИЛА</b>\n\n"
                       "<code>+Правила</code>\nТекст правил со следующей строки\n\n"
                       "<code>Правила</code> — показать\n"
                       "<code>-Правила</code> или <code>Правила удалить</code> — удалить.")
                return finish_command(m, "rules_help", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)
            rules = _get_chat_setting(cid, "rules", "")
            if rules:
                try:
                    link = _get_chat_setting(cid, "chat_link", "")
                    rules = rules.replace("{ссылка}", link or "ссылка не установлена")
                except Exception:
                    pass
            txt = "📜 <b>ПРАВИЛА ЛИЗЫ</b>\n\n" + (html.escape(rules) if rules else "Правила ещё не установлены.")
            return finish_command(m, "rules", bot.send_message(cid, txt, parse_mode="HTML"), ttl=180)
        if t_lower == "приветствие":
            welcome = _get_chat_setting(cid, "welcome", "")
            txt = "👋 <b>ПРИВЕТСТВИЕ VIBE</b>\n\n" + (html.escape(welcome) if welcome else "Приветствие ещё не установлено.")
            return finish_command(m, "welcome_show", bot.send_message(cid, txt, parse_mode="HTML"), ttl=180)
        if t_lower.startswith("+правила"):
            if not _cleanup_dk_allowed(cid, uid, "правила"): return reply_no_rights(m)
            body = t.split("\n", 1)[1].strip() if "\n" in t else t[len("+правила"):].strip()
            if not body: return finish_command(m, "rules_err", bot.send_message(cid, "⚠️ После +правила укажи текст."), ttl=10)
            _set_chat_rules(cid, body)
            return finish_command(m, "rules_set", bot.send_message(cid, "✅ <b>Правила VIBE обновлены.</b>\nТеперь участники могут посмотреть их командой <code>правила</code>.", parse_mode="HTML"))
        if t_lower.startswith("+приветствие"):
            if not _cleanup_dk_allowed(cid, uid, "приветствие"): return reply_no_rights(m)
            body = t.split("\n", 1)[1].strip() if "\n" in t else t[len("+приветствие"):].strip()
            if not body: return finish_command(m, "welcome_err", bot.send_message(cid, "⚠️ После +приветствие укажи текст."), ttl=10)
            _save_welcome(cid, body)
            return finish_command(m, "welcome_set", bot.send_message(cid, "✅ Приветствие VIBE сохранено."))
        if t_lower in ("-правила", "правила удалить"):
            if not _cleanup_dk_allowed(cid, uid, "правила"): return reply_no_rights(m)
            _set_chat_rules(cid, "")
            return finish_command(m, "rules_off", bot.send_message(cid, "🗑 Правила Лизы удалены."))
        if t_lower in ("-приветствие", "приветствие удалить"):
            if not _cleanup_dk_allowed(cid, uid, "приветствие"): return reply_no_rights(m)
            _save_welcome(cid, "")
            return finish_command(m, "welcome_off", bot.send_message(cid, "🗑 Приветствие Лизы удалено."))
        if t_lower in ("приветствие помощь", "приветствие?"):
            txt = ("👋 <b>ЛИЗА — ПРИВЕТСТВИЕ</b>\n\n"
                   "<code>+Приветствие</code>\nТекст приветствия со следующей строки\n\n"
                   "<code>Приветствие</code> — показать\n"
                   "<code>Приветствие удалить</code> — удалить\n"
                   "<code>+Автоудаление приветствий 10 минут</code> — автоудаление.")
            return finish_command(m, "welcome_help", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)
        if t_lower.startswith("+автоудаление приветствий"):
            if not _cleanup_dk_allowed(cid, uid, "приветствие"): return reply_no_rights(m)
            period = _parse_period_words(t_lower) or 300
            period = max(5, min(period, 86400))
            set_chat_setting(cid, "welcome_delete_after", period)
            return finish_command(m, "welcome_delete_on", bot.send_message(cid, f"🧹 Автоудаление приветствий: <b>{period//60} мин.</b>", parse_mode="HTML"), ttl=10)
        if t_lower == "-автоудаление приветствий":
            if not _cleanup_dk_allowed(cid, uid, "приветствие"): return reply_no_rights(m)
            set_chat_setting(cid, "welcome_delete_after", 0)
            return finish_command(m, "welcome_delete_off", bot.send_message(cid, "🧹 Автоудаление приветствий выключено."), ttl=10)
        if t_lower in ("приветствуй", "поприветствуй") or t_lower.startswith(("приветствуй ", "поприветствуй ")):
            welcome = _get_chat_setting(cid, "welcome", "")
            if not welcome:
                return finish_command(m, "welcome_missing", bot.send_message(cid, "⚠️ Сначала установите приветствие: <code>+Приветствие</code>.", parse_mode="HTML"), ttl=15)
            target = m.reply_to_message.from_user if m.reply_to_message else None
            if target is None:
                return finish_command(m, "welcome_target", bot.send_message(cid, "⚠️ Используйте команду ответом на сообщение пользователя.", parse_mode="HTML"), ttl=15)
            rendered = _render_template(welcome, target)
            msg = bot.send_message(cid, rendered, parse_mode="HTML")
            return finish_command(m, "welcome_manual", msg, ttl=180)
        if t_lower in ("приветствуй всех снова", "приветствуй снова", "приветствуй всех по новой"):
            with state_lock:
                data = db_get("chat_activity", {})
                chat = data.setdefault(str(cid), {})
                for row in chat.values(): row["greeted"] = False
                db_set("chat_activity", data)
            return finish_command(m, "welcome_reset", bot.send_message(cid, "♻️ История приветствий Лизы сброшена."))

        # --- Закреп / откреп ---
        if t_lower.startswith(("!закреп ", "закреп ", "/pin ")) or t_lower in ["!закреп", "закреп", "!пин", "пин", "/pin"]:
            if not _cleanup_dk_allowed(cid, uid, "закреп"): return reply_no_rights(m)
            target = m.reply_to_message
            if target is None and len(t.split()) > 1 and t.split()[1].isdigit():
                try:
                    target = bot.forward_message(cid, cid, int(t.split()[1]))
                    try: bot.delete_message(cid, target.message_id)
                    except: pass
                    target_mid = int(t.split()[1])
                except Exception:
                    target_mid = None
            else:
                target_mid = target.message_id if target else None
            if not target_mid: return finish_command(m, "pin_err", bot.send_message(cid, "⚠️ Используй команду ответом на сообщение или укажи ID сообщения."), ttl=10)
            try:
                bot.pin_chat_message(cid, target_mid, disable_notification=True)
                return finish_command(m, "pin", bot.send_message(cid, "📌 <b>ЛИЗА-ПИН</b>\nСообщение закреплено.", parse_mode="HTML"), ttl=10)
            except Exception as e:
                logging.error(f"[PIN] {e}")
                return finish_command(m, "pin_err", bot.send_message(cid, "⚠️ Не удалось закрепить сообщение. Проверь права бота."), ttl=15)
        if t_lower.startswith(("!открепить ", "открепить ", "/unpin ")) or t_lower in ["!открепить", "открепить", "!анпин", "анпин", "/unpin"]:
            if not _cleanup_dk_allowed(cid, uid, "открепить"): return reply_no_rights(m)
            try:
                bot.unpin_chat_message(cid)
                return finish_command(m, "unpin", bot.send_message(cid, "📌 Закрепление снято."), ttl=10)
            except Exception:
                return finish_command(m, "unpin_err", bot.send_message(cid, "⚠️ Не удалось снять закрепление."), ttl=15)

        # --- Название и описание ---
        if t_lower.startswith("!название ") or t_lower.startswith("название "):
            if not _cleanup_dk_allowed(cid, uid, "название"): return reply_no_rights(m)
            new_title = t.split(" ", 1)[1].strip()
            try:
                bot.set_chat_title(cid, new_title)
                return finish_command(m, "title", bot.send_message(cid, f"✏️ Название VIBE изменено на <b>{html.escape(new_title)}</b>.", parse_mode="HTML"), ttl=15)
            except Exception:
                return finish_command(m, "title_err", bot.send_message(cid, "⚠️ Не удалось изменить название чата."), ttl=15)
        if t_lower.startswith("+описание чата"):
            if not _cleanup_dk_allowed(cid, uid, "описание"): return reply_no_rights(m)
            body = t.split("\n",1)[1].strip() if "\n" in t else t[len("+описание чата"):].strip()
            try:
                bot.set_chat_description(cid, body)
                return finish_command(m, "desc", bot.send_message(cid, "📝 Описание VIBE обновлено."), ttl=15)
            except Exception:
                return finish_command(m, "desc_err", bot.send_message(cid, "⚠️ Не удалось изменить описание."), ttl=15)
        if t_lower == "-описание чата":
            if not _cleanup_dk_allowed(cid, uid, "описание"): return reply_no_rights(m)
            try: bot.set_chat_description(cid, "")
            except Exception: pass
            return finish_command(m, "desc_off", bot.send_message(cid, "🗑 Описание VIBE удалено."), ttl=15)

        # --- Чат-ссылка в стиле Iris ---
        if t_lower in ("+чат ссылка по заявкам", "чат ссылка по заявкам"):
            if not _cleanup_dk_allowed(cid, uid, "чат ссылка"): return reply_no_rights(m)
            try:
                link = bot.create_chat_invite_link(cid, name="Лиза — заявки", creates_join_request=True)
                set_chat_setting(cid, "chat_link", link.invite_link)
                return finish_command(m, "chat_link_requests", bot.send_message(cid, f"🔗 <b>Ссылка по заявкам Лизы:</b>\n{html.escape(link.invite_link)}", parse_mode="HTML"), ttl=120)
            except Exception:
                return finish_command(m, "chat_link_err", bot.send_message(cid, "⚠️ Не удалось создать ссылку по заявкам. Проверь права Лизы."), ttl=15)
        if t_lower in ("+чат ссылка", "чат ссылка"):
            if not _cleanup_dk_allowed(cid, uid, "чат ссылка"): return reply_no_rights(m)
            try:
                link = bot.create_chat_invite_link(cid, name="Лиза")
                set_chat_setting(cid, "chat_link", link.invite_link)
                return finish_command(m, "chat_link", bot.send_message(cid, f"🔗 <b>Ссылка Лизы:</b>\n{html.escape(link.invite_link)}", parse_mode="HTML"), ttl=120)
            except Exception:
                return finish_command(m, "chat_link_err", bot.send_message(cid, "⚠️ Не удалось создать ссылку. Проверь права Лизы."), ttl=15)
        if t_lower == "-чат ссылка":
            if not _cleanup_dk_allowed(cid, uid, "чат ссылка"): return reply_no_rights(m)
            link = _get_chat_setting(cid, "chat_link", "")
            if link:
                try: bot.revoke_chat_invite_link(cid, link)
                except Exception: pass
            set_chat_setting(cid, "chat_link", "")
            return finish_command(m, "chat_link_off", bot.send_message(cid, "🗑 Ссылка чата удалена из настроек Лизы."), ttl=10)
        if t_lower in ("!сброс ссылок", "сброс ссылок"):
            if not _cleanup_dk_allowed(cid, uid, "чат ссылка"): return reply_no_rights(m)
            links = get_v(cid, "chat_links", []) or []
            if isinstance(links, str): links = [links] if links else []
            for link in links:
                try: bot.revoke_chat_invite_link(cid, link)
                except Exception: pass
            set_chat_setting(cid, "chat_links", [])
            set_chat_setting(cid, "chat_link", "")
            return finish_command(m, "chat_links_reset", bot.send_message(cid, "🗑 Ссылки Лизы сброшены."), ttl=10)
        if t_lower in ("базовая настройка", "настройки помощь", "помощь настройки"):
            txt = ("⚙️ <b>БАЗОВАЯ НАСТРОЙКА ЛИЗЫ</b>\n\n"
                   "<code>+Правила</code> / <code>+Приветствие</code>\n"
                   "<code>+Чат ссылка</code>\n"
                   "<code>+модер 1-5</code>\n"
                   "<code>ДК команда ранг</code>\n"
                   "<code>+Триггер событие ранг</code>\n"
                   "<code>+Автокик молчунов 7 дней</code>\n"
                   "<code>Кик неактив 2 месяца</code>\n"
                   "<code>+Ссылки</code> / <code>-Ссылки</code>\n\n"
                   "Все команды поддерживают !, . и /.")
            return finish_command(m, "setup_help", bot.send_message(cid, txt, parse_mode="HTML"), ttl=180)

        # --- Дополнительные настройки чата ---
        if t_lower.startswith("+автокик") or t_lower=="-автокик" or t_lower.startswith("-автокик"):
            if not _cleanup_dk_allowed(cid, uid, "автокик"): return reply_no_rights(m)
            if t_lower=="-автокик": set_chat_setting(cid,"autokick",{}); return finish_command(m,"autokick_off",bot.send_message(cid,"🧹 Автокик после выхода выключен."),ttl=10)
            if t_lower.startswith("-автокик молчунов"): set_chat_setting(cid,"autokick_silent",0); return finish_command(m,"autokick_silent_off",bot.send_message(cid,"🧹 Автокик молчунов выключен."),ttl=10)
            nums=re.findall(r"\d+",t_lower); exits=int(nums[0]) if nums else 1; period=_parse_period_words(t_lower) or 3600; action="ban" if " бан" in t_lower else "kick"
            set_chat_setting(cid,"autokick",{"exits":max(1,exits),"window":period,"action":action}); return finish_command(m,"autokick_on",bot.send_message(cid,f"🛡 Автокик включён: <b>{exits}</b> выход(а) за <b>{period//60}</b> мин → <b>{action}</b>.",parse_mode="HTML"),ttl=15)
        if t_lower.startswith("+автокик молчунов"):
            if not _cleanup_dk_allowed(cid,uid,"автокик"): return reply_no_rights(m)
            period=_parse_period_words(t_lower) or 7*86400; set_chat_setting(cid,"autokick_silent",period); return finish_command(m,"autokick_silent_on",bot.send_message(cid,f"🤫 Автокик молчунов включён: <b>{period//86400}</b> дн.",parse_mode="HTML"),ttl=15)
        if t_lower in ("-входы","+входы","-выходы","+выходы","-входы-выходы","+входы-выходы") or t_lower.startswith("+выходы "):
            code="входы" if "входы" in t_lower and "выходы" not in t_lower else ("выходы" if "выходы" in t_lower and "входы" not in t_lower else "входы-выходы")
            if not _cleanup_dk_allowed(cid,uid,code): return reply_no_rights(m)
            if t_lower.startswith("+выходы "):
                nums=re.findall(r"\d+",t_lower); set_chat_setting(cid,"leave_notify_threshold",int(nums[0]) if nums else 0); set_chat_setting(cid,"notify_leaves",True); return finish_command(m,"leave_threshold",bot.send_message(cid,"👋 Порог уведомлений о выходе обновлён."),ttl=10)
            val=t_lower.startswith("+"); set_chat_setting(cid,"notify_joins",val if "входы" in t_lower else get_v(cid,"notify_joins",False)); set_chat_setting(cid,"notify_leaves",val if "выходы" in t_lower else get_v(cid,"notify_leaves",False)); return finish_command(m,"joinleave",bot.send_message(cid,"🔔 Уведомления о входах/выходах обновлены."),ttl=10)
        if t_lower.startswith("+минрег ") or t_lower=="-минрег" or t_lower=="минрег":
            if not _cleanup_dk_allowed(cid,uid,"минрег"): return reply_no_rights(m)
            if t_lower=="-минрег": set_chat_setting(cid,"minreg_days",0); return finish_command(m,"minreg_off",bot.send_message(cid,"🛡 Минрег отключён."),ttl=10)
            if t_lower=="минрег": return finish_command(m,"minreg_show",bot.send_message(cid,f"🛡 Минрег: <b>{get_v(cid,'minreg_days',0)}</b> дн.",parse_mode="HTML"),ttl=30)
            nums=re.findall(r"\d+",t_lower); days=int(nums[0]) if nums else 0; set_chat_setting(cid,"minreg_days",days); return finish_command(m,"minreg_on",bot.send_message(cid,f"🛡 Минимальная регистрация: <b>{days}</b> дн.",parse_mode="HTML"),ttl=10)
        if t_lower in ("+каналы","-каналы","+инлайны","-инлайны"):
            if not _cleanup_dk_allowed(cid,uid,"каналы" if "каналы" in t_lower else "инлайны"): return reply_no_rights(m)
            key="allow_channel_posts" if "каналы" in t_lower else "inline_notifications"; set_chat_setting(cid,key,t_lower.startswith("+")); return finish_command(m,"setting",bot.send_message(cid,"⚙️ Настройка сохранена."),ttl=10)
        if t_lower in ("+чат ссылка","-чат ссылка","чат-ссылка") or t_lower.startswith("+чат ссылка"):
            if not _cleanup_dk_allowed(cid,uid,"чат ссылка"): return reply_no_rights(m)
            if t_lower=="-чат ссылка":
                try: bot.revoke_chat_invite_link(cid, get_v(cid,"chat_link",""))
                except: pass
                set_chat_setting(cid,"chat_link",""); return finish_command(m,"chatlink_off",bot.send_message(cid,"🔗 Ссылка VIBE удалена."),ttl=10)
            try:
                link=bot.create_chat_invite_link(cid, creates_join_request=("по заявкам" in t_lower)).invite_link
                set_chat_setting(cid,"chat_link",link); return finish_command(m,"chatlink",bot.send_message(cid,f"🔗 <b>Ссылка на VIBE:</b> {html.escape(link)}",parse_mode="HTML"),ttl=60)
            except Exception:
                return finish_command(m,"chatlink_err",bot.send_message(cid,"⚠️ Не удалось создать ссылку. Проверь права бота."),ttl=15)
        if t_lower=="чат-ссылка":
            link=get_v(cid,"chat_link",""); return finish_command(m,"chatlink_show",bot.send_message(cid,"🔗 "+(html.escape(link) if link else "Ссылка ещё не создана."),parse_mode="HTML"),ttl=60)

        # --- Открытие/закрытие чата для обычных участников ---
        if t_lower in ["+чат", "-чат"]:
            if not _cleanup_dk_allowed(cid, uid, "чат"): return reply_no_rights(m)
            try:
                perms = types.ChatPermissions(can_send_messages=(t_lower == "+чат"), can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True)
                bot.set_chat_permissions(cid, perms)
                txt = "🔓 <b>VIBE ОТКРЫТ</b>\nУчастники снова могут писать." if t_lower == "+чат" else "🔒 <b>VIBE НА ПАУЗЕ</b>\nОбычным участникам временно запрещено писать."
                return finish_command(m, "chat_lock", bot.send_message(cid, txt, parse_mode="HTML"), ttl=15)
            except Exception:
                return finish_command(m, "chat_lock_err", bot.send_message(cid, "⚠️ Не удалось изменить режим чата. Проверь права бота."), ttl=15)

        # --- IRIS: форумные топики, короткие списки и анти-рейд ---
        if t_lower in ("антирейд", "антирейд помощь") or t_lower.startswith(("+антирейд", "-антирейд")):
            if not _cleanup_dk_allowed(cid, uid, "антирейд"):
                return reply_no_rights(m)
            if t_lower == "антирейд помощь":
                return finish_command(m, "antiraid_help", bot.send_message(cid, "🛡 <b>АНТИРЕЙД</b>\n\n<code>+Антирейд 5 60</code> — 5 входов за 60 секунд.\n<code>-Антирейд</code> — выключить.\n<code>Антирейд</code> — показать настройки.", parse_mode="HTML"), ttl=60)
            if t_lower == "-антирейд":
                set_chat_setting(cid, "antiraid", {"enabled": False})
                return finish_command(m, "antiraid_off", bot.send_message(cid, "🛡 Антирейд выключен."), ttl=15)
            if t_lower == "антирейд":
                cfg=get_v(cid,"antiraid",{}) or {}
                state="включён" if cfg.get("enabled") else "выключен"
                return finish_command(m,"antiraid_show",bot.send_message(cid,f"🛡 Антирейд: <b>{state}</b>\nПорог: <b>{int(cfg.get('threshold',5))}</b>\nОкно: <b>{int(cfg.get('window',60))} сек.</b>",parse_mode="HTML"),ttl=30)
            nums=[int(x) for x in re.findall(r"\d+", t_lower)]
            threshold=max(2,min(100,nums[0] if nums else 5)); window=max(10,min(3600,nums[1] if len(nums)>1 else 60))
            set_chat_setting(cid,"antiraid",{"enabled":True,"threshold":threshold,"window":window})
            return finish_command(m,"antiraid_on",bot.send_message(cid,f"🛡 Антирейд включён: <b>{threshold}</b> входов за <b>{window} сек.</b>",parse_mode="HTML"),ttl=15)

        if t_lower in ("+короткие списки", "-короткие списки", "короткие списки"):
            if not _cleanup_dk_allowed(cid, uid, "короткие списки"):
                return reply_no_rights(m)
            if t_lower == "короткие списки":
                enabled=bool(get_v(cid,"short_lists",False))
                return finish_command(m,"short_lists_show",bot.send_message(cid,f"📋 Короткие списки: <b>{'включены' if enabled else 'выключены'}</b>.",parse_mode="HTML"),ttl=20)
            enabled=t_lower.startswith("+")
            set_chat_setting(cid,"short_lists",enabled)
            return finish_command(m,"short_lists_set",bot.send_message(cid,f"📋 Короткие списки {'включены' if enabled else 'выключены'}.",parse_mode="HTML"),ttl=15)

        if t_lower.startswith("топик ") or t_lower.startswith("ветка ") or t_lower in ("топик название", "ветка название"):
            if not _cleanup_dk_allowed(cid, uid, "топик"):
                return reply_no_rights(m)
            words=t.split(None,2)
            if len(words) >= 3 and words[1].lower() == "название":
                title=words[2].strip()
            elif len(words) >= 2 and words[0].lower() in ("топик","ветка") and words[1].lower() != "название":
                title=" ".join(words[1:]).strip()
            else:
                title=""
            if not title or title.lower()=="название":
                return finish_command(m,"topic_name_err",bot.send_message(cid,"⚠️ Укажи новое название: <code>Топик название Новое имя</code>.",parse_mode="HTML"),ttl=15)
            if not title:
                return finish_command(m,"topic_name_err",bot.send_message(cid,"⚠️ Укажи название топика."),ttl=15)
            thread_id=getattr(m,"message_thread_id",None)
            if not thread_id:
                return finish_command(m,"topic_err",bot.send_message(cid,"⚠️ Эту команду нужно отправлять внутри форумного топика."),ttl=15)
            try:
                bot.edit_forum_topic(cid, thread_id, name=title)
                return finish_command(m,"topic_name",bot.send_message(cid,"🧵 Название топика изменено."),ttl=15)
            except Exception as e:
                logging.warning(f"[TOPIC] {e}")
                return finish_command(m,"topic_err",bot.send_message(cid,"⚠️ Не удалось изменить топик. Нужны права управления темами."),ttl=15)

        # --- Чистка сообщений ---
        if t_lower.startswith("-смс") or t_lower.startswith("!пург") or t_lower.startswith("пург"):
            code = "-смс" if t_lower.startswith("-смс") else "пург"
            if not _cleanup_dk_allowed(cid, uid, code): return reply_no_rights(m)
            quiet = "тихо" in parts[1:]
            nums = [int(x) for x in parts[1:] if x.isdigit()]
            target_count = nums[0] if nums else 1
            anchor = m.reply_to_message.message_id if (code == "пург" and m.reply_to_message) else m.message_id
            if code == "пург" and not m.reply_to_message:
                return finish_command(m, "purge_err", bot.send_message(cid, "⚠️ <code>Пург</code> нужно использовать ответом на сообщение.", parse_mode="HTML"), ttl=10)
            deleted = _cleanup_messages(cid, anchor, target_count + 1)
            if quiet: return
            txt = f"🧹 <b>VIBE-ЧИСТКА</b>\nУдалено сообщений: <b>{deleted}</b>."
            return finish_command(m, "cleanup", bot.send_message(cid, txt, parse_mode="HTML"), ttl=10)

        parts = t.split()
        cmd = parts[0].lower() if parts else ""
        
        # --- СИСТЕМА УВАЖЕНИЯ ---
        if t == "+" and m.reply_to_message:
            target_uid = m.reply_to_message.from_user.id
            if target_uid == uid:
                auto_del(bot.reply_to(m, "🙅 Себе уважение оказывать нельзя! 😉"), 5)
                try: bot.delete_message(cid, m.message_id)
                except: pass
                return
            if m.reply_to_message.from_user.is_bot:
                try: bot.delete_message(cid, m.message_id)
                except: pass
                return 
                
            with state_lock:
                users = db_get("users_data", {})
                u_giver = users.setdefault(str(uid), {"xp": 0, "msgs": 0, "name": fname, "uname": uname, "first_seen": time.time(), "respects": 0, "given_respects": 0, "respect_reset": 0})
                u_target = users.setdefault(str(target_uid), {"xp": 0, "msgs": 0, "name": m.reply_to_message.from_user.first_name, "uname": m.reply_to_message.from_user.username, "first_seen": time.time(), "respects": 0, "given_respects": 0, "respect_reset": 0})
                
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

        # Текстовый аналог /settings.
        if t_lower in ("настройки", "настройка"):
            if m.chat.type == "private":
                return reply_no_rights(m)
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            msg = bot.send_message(cid, "🎛 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ЛИЗОЙ</b> ✨\n\nНастрой характер и функции бота под свой чат:",
                                   reply_markup=main_kb(cid, False), parse_mode="HTML")
            return finish_command(m, "settings", msg)

        # --- ДОСТУП КОМАНД (IRIS-STYLE) ---
        if cmd == "мой" and len(parts) >= 3 and parts[1].lower() == "доступ" and parts[2].lower() in ["команд", "команды"]:
            cmd = "мой доступ команд"
        elif cmd in ["доступ", "дк", "мой", "мдк", "мой доступ", "мой дк"] and len(parts) > 1:
            if parts[1].lower() in ["команд", "команды"]:
                cmd = "доступ команд" if cmd not in ["мой", "мдк", "мой доступ", "мой дк"] else "мой доступ команд"
        if cmd in ["доступ команд", "/дк", "!дк", ".дк", "дк", "мой доступ команд", "мой дк", "мдк"]:
            if cmd in ["мой доступ команд", "мой дк", "мдк"]:
                if not command_allowed_by_dk(cid, uid, "мой доступ команд"):
                    return reply_no_rights(m)
                return finish_command(m, "my_dk", bot.send_message(cid, format_dk_list(cid, uid), parse_mode="HTML"), ttl=120)
            if len(parts) == 1:
                if not command_allowed_by_dk(cid, uid, "доступ команд"):
                    return reply_no_rights(m)
                return finish_command(m, "dk", bot.send_message(cid, format_dk_list(cid), parse_mode="HTML"), ttl=120)
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            sub = parts[1].lower()
            if sub in ["лог"]:
                rows = db_get("dk_log", {}).get(str(cid), [])[-30:]
                txt = "⚙️ <b>VIBE-ЛОГ ДК</b>\n\n"
                for r in reversed(rows):
                    dt = datetime.fromtimestamp(r["date"], KYIV_TZ).strftime("%d.%m %H:%M")
                    txt += f"• {dt} — {html.escape(r['name'])}: <code>{html.escape(r['command'])}</code> {r['old']} → {r['new']}\n"
                return finish_command(m, "dk_log", bot.send_message(cid, txt + ("\nЛог пуст." if not rows else ""), parse_mode="HTML"), ttl=120)
            if len(parts) >= 3:
                code = DK_ALIASES.get(sub, sub)
                try: new_rank = int(parts[2])
                except Exception:
                    return finish_command(m, "dk_err", bot.send_message(cid, "⚠️ Формат: <code>дк команда ранг</code>. Ранг: 0–6." , parse_mode="HTML"), ttl=15)
                if new_rank < 0 or new_rank > 6:
                    return finish_command(m, "dk_err", bot.send_message(cid, "⚠️ Ранг должен быть от 0 до 6."), ttl=10)
                old = get_command_threshold(cid, code)
                set_command_threshold(cid, code, new_rank)
                save_dk_log(cid, uid, fname, code, old, new_rank)
                return finish_command(m, "dk_ok", bot.send_message(cid, f"⚙️ <b>ДК обновлён</b>\n\nКоманда: <code>{html.escape(code)}</code>\nБыло: <b>{old}</b>\nСтало: <b>{new_rank}</b>\n\n0 — всем • 1–5 — с ранга • 6 — выключено", parse_mode="HTML"))
            return finish_command(m, "dk", bot.send_message(cid, format_dk_list(cid), parse_mode="HTML"), ttl=120)

        # Включение/отключение команды по рангу: +дк команда [ранг] / -дк команда [ранг]
        if cmd in ["+дк", "-дк"]:
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            if len(parts) < 2:
                return finish_command(m, "dk_err", bot.send_message(cid, "⚠️ Формат: +дк <команда> <0-6> или -дк <команда> <0-6>"), ttl=15)
            code = DK_ALIASES.get(parts[1].lower(), parts[1].lower())
            new_rank = 0 if cmd == "+дк" else 6
            if len(parts) >= 3 and parts[2].isdigit():
                new_rank = int(parts[2])
            if not 0 <= new_rank <= 6:
                return finish_command(m, "dk_err", bot.send_message(cid, "⚠️ Ранг должен быть от 0 до 6."), ttl=10)
            old = get_command_threshold(cid, code)
            set_command_threshold(cid, code, new_rank)
            save_dk_log(cid, uid, fname, code, old, new_rank)
            return finish_command(m, "dk_ok", bot.send_message(cid, f"⚙️ <b>ДК обновлён</b>\n<code>{html.escape(code)}</code>: <b>{old}</b> → <b>{new_rank}</b>\n\n0 — всем • 1–5 — с ранга • 6 — выключено", parse_mode="HTML"))

        # Личный доступ: +лдк команда @user / -лдк команда @user
        if cmd in ["+лдк", "-лдк"]:
            if not command_allowed_by_dk(cid, uid, "личный дк"):
                return reply_no_rights(m)
            target_uid, target_name, _ = extract_target_and_args(m, parts)
            if not target_uid or len(parts) < 2:
                return finish_command(m, "ldk_err", bot.send_message(cid, "⚠️ Формат: +лдк <команда> <пользователь> или -лдк <команда> <пользователь>"), ttl=15)
            code = DK_ALIASES.get(parts[1].lower(), parts[1].lower())
            enabled = cmd == "+лдк"
            set_personal_dk(cid, target_uid, code, enabled)
            return finish_command(m, "ldk", bot.send_message(cid, f"👤 <b>Личный доступ</b>\n{get_user_mention(user_id=target_uid, first_name=target_name)}: <code>{html.escape(code)}</code> — {'разрешено' if enabled else 'запрещено'}.", parse_mode="HTML"))

        # Проверка ДК для известных команд выполняется до их обработчика.
        dk_code = DK_ALIASES.get(cmd, DK_COMMANDS.get(cmd))
        if dk_code and not command_allowed_by_dk(cid, uid, dk_code):
            return reply_no_rights(m)

        # --- СИСТЕМА АДМИНИСТРИРОВАНИЯ ---
        if cmd in ["+модер", "!модер", "+админ", "повысить", "понизить", "разжаловать", "снять", "снять всех", "пред", "варн", "/warn", "предупреждение", "варны", "мои варны", "варнлист", "снять варны", "снять все варны", "снять варн", "/unwarn", "мут", "/mute", "муты", "проверить мут", "снять мут", "/unmute", "бан", "/ban", "банлист", "разбан", "вернуть", "снятьбан", "/unban", "причина", "кик", "/kick", "кик тихо", "амнистия", "кто", "кто админ", "админы", "кто назначил", "созвать модеров", "позвать модеров", "модер лог", "мой модер лог", "права", "/rank_perms", "rank_perms", "дк", "/дк", "!дк", ".дк", "доступ", "мой дк", "мдк", "мой доступ команд", "доступ команд", "+дк", "-дк", "+лдк", "-лдк", "лог дк", "твой модер лог", "модер лог от"]:
            if cmd == "кто" and len(parts) > 1 and parts[1].lower() == "админ": cmd = "кто админ"
            if cmd == "снять" and len(parts) > 1 and parts[1].lower() == "мут": cmd = "снять мут"
            elif cmd == "снять" and len(parts) > 1 and parts[1].lower() == "пред": cmd = "снять пред"
            elif cmd == "варн": cmd = "пред"
            elif cmd == "предупреждение": cmd = "пред"
            elif cmd == "разбан" or cmd == "вернуть": cmd = "снятьбан"
            elif cmd == "проверить" and len(parts) > 1 and parts[1].lower() == "мут": cmd = "проверить мут"
            elif cmd == "муты": cmd = "муты"
            elif cmd == "банлист": cmd = "банлист"
            elif cmd == "варнлист": cmd = "варнлист"
            elif cmd == "мои" and len(parts) > 1 and parts[1].lower() == "варны": cmd = "мои варны"
            elif cmd == "модер" and len(parts) > 1 and parts[1].lower() == "лог":
                cmd = "модер лог от" if len(parts) > 2 and parts[2].lower() == "от" else "модер лог"
            elif cmd == "твой" and len(parts) > 2 and parts[1].lower() == "модер" and parts[2].lower() == "лог": cmd = "твой модер лог"
            elif cmd == "кто" and len(parts) > 1 and parts[1].lower() == "назначил": cmd = "кто назначил"
            elif cmd in ["дк", "/дк", "!дк", ".дк"]: cmd = "доступ команд"
            elif cmd in ["мой дк", "мдк", "мой доступ команд"]: cmd = "мой доступ команд"
            elif cmd == "лог" and len(parts) > 1 and parts[1].lower() == "дк": cmd = "лог дк"
            
            if cmd in ["+модер", "!модер", "+админ"]:
                # Iris-compatible rank assignment: +модер [1-5] target
                rank = 1
                if len(parts) > 1 and parts[1].isdigit():
                    rank = max(1, min(5, int(parts[1])))
                target_uid, target_name, _ = extract_target_and_args(m, parts)
                if not target_uid:
                    return finish_command(m, "no_target", bot.send_message(cid, "⚠️ Укажи пользователя ответом, @username или ID."), ttl=10)
                caller_rank = get_admin_rank(cid, uid)
                if (caller_rank <= rank or not has_permission(cid, uid, "can_promote")):
                    return reply_no_rights(m)
                if target_uid == BOT_ID or (get_admin_rank(cid, target_uid) >= caller_rank):
                    return reply_no_rights(m)
                set_admin_rank(cid, target_uid, rank)
                save_moderation_record(cid, "rank", target_uid, target_name, uid, fname, "", rank)
                target = get_user_mention(user_id=target_uid, first_name=target_name)
                actor = get_user_mention(user_id=uid, first_name=fname)
                txt = f"👑 <b>VIBE-ПОВЫШЕНИЕ</b>\n{target} получает статус:\n<b>{moderation_rank_name(rank)}</b>\n🛡 Назначил: {actor}"
                return finish_command(m, "admin_change", bot.send_message(cid, txt, parse_mode="HTML"))

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

            elif cmd == "кто назначил":
                if not command_allowed_by_dk(cid, uid, "кто назначил"):
                    return reply_no_rights(m)
                target_uid, target_name, _ = extract_target_and_args(m, parts)
                if not target_uid:
                    return finish_command(m, "who_assigned_err", bot.send_message(cid, "⚠️ Укажи пользователя ответом, @username или ID."), ttl=10)
                rows = [r for r in db_get("moderation_log", {}).get(str(cid), []) if r.get("action") == "rank" and r.get("target_uid") == target_uid]
                rows.sort(key=lambda x: x.get("date", 0), reverse=True)
                if rows:
                    r = rows[0]
                    actor = get_user_mention(user_id=r.get("actor_uid"), first_name=r.get("actor_name", "Модератор"))
                    txt = f"👑 <b>КТО НАЗНАЧИЛ</b>\n\n{get_user_mention(user_id=target_uid, first_name=target_name)}\n🛡 Назначил: {actor}\n🏷 Ранг: <b>{moderation_rank_name(r.get('duration', 0))}</b>"
                else:
                    txt = f"ℹ️ История назначения {get_user_mention(user_id=target_uid, first_name=target_name)} не найдена."
                return finish_command(m, "who_assigned", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

            elif cmd in ["модер лог", "мой модер лог", "твой модер лог", "модер лог от"]:
                rows = db_get("moderation_log", {}).get(str(cid), [])
                if cmd == "мой модер лог":
                    rows = [r for r in rows if r.get("actor_uid") == uid]
                elif cmd == "твой модер лог":
                    target_uid, target_name, _ = extract_target_and_args(m, parts)
                    if not target_uid:
                        target_uid, target_name = uid, fname
                    rows = [r for r in rows if r.get("target_uid") == target_uid and r.get("action") == "rank"]
                elif cmd == "модер лог от":
                    target_uid, target_name, _ = extract_target_and_args(m, parts)
                    if not target_uid:
                        return finish_command(m, "modlog_err", bot.send_message(cid, "⚠️ Укажи модератора ответом или через @username/ID."), ttl=10)
                    rows = [r for r in rows if r.get("actor_uid") == target_uid]
                rows = rows[-30:][::-1]
                txt = "📜 <b>VIBE-МОДЕР ЛОГ</b>\n\n"
                for r in rows:
                    dt = datetime.fromtimestamp(r.get("date", time.time()), KYIV_TZ).strftime("%d.%m %H:%M")
                    action = html.escape(str(r.get("action", "—")))
                    target = html.escape(str(r.get("target_name", "—")))
                    actor = html.escape(str(r.get("actor_name", "—")))
                    txt += f"• {dt} — <b>{action}</b>: {target} ← {actor}\n"
                if not rows: txt += "Лог пока пуст."
                return finish_command(m, "modlog", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)

            elif cmd in ["варнлист", "мои варны"]:
                if not has_permission(cid, uid, "can_warn") and cmd == "варнлист":
                    return reply_no_rights(m)
                target_uid, target_name, _ = extract_target_and_args(m, parts)
                if cmd == "мои варны":
                    target_uid, target_name = uid, fname
                db = db_get("chat_warns", {}).get(str(cid), {})
                rows = []
                for a_uid, data in db.items():
                    if data.get("count", 0) > 0:
                        name = db_get("users_data", {}).get(str(a_uid), {}).get("name", f"ID:{a_uid}")
                        rows.append((data.get("count", 0), get_user_mention(user_id=int(a_uid), first_name=name)))
                if cmd == "мои варны":
                    data = db.get(str(target_uid), {"count": 0})
                    txt = f"⚠️ <b>Твои предупреждения</b>: {data.get('count', 0)}/{get_v(cid, 'max_warns', 3)}"
                else:
                    rows.sort(reverse=True)
                    txt = "⚠️ <b>VIBE-ВАРНЛИСТ</b>\n\n" + ("\n".join(f"• {u} — {n}" for n,u in rows[:50]) or "Пусто — чат чист.")
                return finish_command(m, "warnlist", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)

            elif cmd in ["снять варны", "снять все варны", "снять варн"]:
                if not has_permission(cid, uid, "can_warn"):
                    return reply_no_rights(m)
                target_uid, target_name, args = extract_target_and_args(m, parts)
                if not target_uid:
                    return finish_command(m, "no_target", bot.send_message(cid, "⚠️ Укажи пользователя ответом, @username или ID."), ttl=10)
                amount = None
                if cmd == "снять варны" and args and args[0].isdigit():
                    amount = int(args[0])
                removed, left = clear_warns(cid, target_uid, amount)
                target = get_user_mention(user_id=target_uid, first_name=target_name)
                txt = format_moderation_message("unwarn", get_user_mention(user_id=uid, first_name=fname), target, extra=f"🧹 Снято: {removed}\n⚠️ Осталось: {left}/{get_v(cid, 'max_warns', 3)}")
                save_moderation_record(cid, "unwarn", target_uid, target_name, uid, fname)
                return finish_command(m, "unwarn", bot.send_message(cid, txt, parse_mode="HTML"))

            elif cmd in ["муты", "проверить мут"]:
                if not has_permission(cid, uid, "can_mute"):
                    return reply_no_rights(m)
                if cmd == "проверить мут":
                    target_uid, target_name, _ = extract_target_and_args(m, parts)
                    if not target_uid:
                        target_uid, target_name = uid, fname
                    try:
                        member = bot.get_chat_member(cid, target_uid)
                        until = getattr(member, "until_date", None)
                        muted = getattr(member, "status", "") == "restricted" and until and until > int(time.time())
                        if muted:
                            left = format_seconds_human(until - int(time.time()))
                            txt = f"🔇 <b>VIBE-ПРОВЕРКА</b>\n{get_user_mention(user_id=target_uid, first_name=target_name)} сейчас в муте.\n⏱ Осталось: {left}"
                        else:
                            txt = f"🔊 <b>VIBE-ПРОВЕРКА</b>\n{get_user_mention(user_id=target_uid, first_name=target_name)} может говорить."
                    except Exception:
                        txt = "⚠️ Не удалось проверить ограничения пользователя."
                    return finish_command(m, "check_mute", bot.send_message(cid, txt, parse_mode="HTML"), ttl=30)
                db = db_get("chat_mutes", {}).get(str(cid), {})
                rows=[]
                now=time.time()
                for a_uid, until in db.items():
                    if until == 0 or until > now:
                        name=db_get("users_data", {}).get(str(a_uid), {}).get("name", f"ID:{a_uid}")
                        rows.append(f"• {get_user_mention(user_id=int(a_uid), first_name=name)} — {'навсегда' if until==0 else format_seconds_human(int(until-now))}")
                txt="🔇 <b>VIBE-МУТЫ</b>\n\n"+("\n".join(rows) or "Никто не замьючен.")
                return finish_command(m, "mutes", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)

            elif cmd == "банлист":
                if not has_permission(cid, uid, "can_ban"):
                    return reply_no_rights(m)
                db = db_get("chat_bans", {}).get(str(cid), {})
                now=time.time(); rows=[]
                for a_uid, data in db.items():
                    until=data.get("until", 0) if isinstance(data, dict) else data
                    if until == 0 or until > now:
                        name=(data.get("name") if isinstance(data,dict) else None) or db_get("users_data", {}).get(str(a_uid),{}).get("name",f"ID:{a_uid}")
                        rows.append(f"• {get_user_mention(user_id=int(a_uid), first_name=name)} — {'навсегда' if until==0 else format_seconds_human(int(until-now))}")
                txt="🚫 <b>VIBE-БАНЛИСТ</b>\n\n"+("\n".join(rows) or "Банлист пуст.")
                return finish_command(m, "banlist", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)

            elif cmd == "причина":
                if not has_permission(cid, uid, "can_ban"):
                    return reply_no_rights(m)
                target_uid, target_name, _ = extract_target_and_args(m, parts)
                if not target_uid:
                    return finish_command(m, "no_target", bot.send_message(cid, "⚠️ Укажи пользователя ответом, @username или ID."), ttl=10)
                db=db_get("chat_bans", {}).get(str(cid), {}).get(str(target_uid))
                if not db:
                    return finish_command(m, "ban_reason", bot.send_message(cid, "ℹ️ По этому пользователю нет сохранённой информации о бане."), ttl=20)
                actor=get_user_mention(user_id=db.get("by_uid"), first_name=db.get("by_name","Модератор")) if db.get("by_uid") else "неизвестен"
                txt=f"🚫 <b>Информация о бане</b>\n👤 {get_user_mention(user_id=target_uid, first_name=target_name)}\n📝 Причина: {html.escape(db.get('reason','Не указана'))}\n🛡 Модератор: {actor}"
                return finish_command(m, "ban_reason", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

            elif cmd == "амнистия":
                if not has_permission(cid, uid, "can_ban"):
                    return reply_no_rights(m)
                try:
                    for a_uid in list(db_get("chat_bans", {}).get(str(cid), {}).keys()):
                        bot.unban_chat_member(cid, int(a_uid), only_if_banned=True)
                    bans=db_get("chat_bans", {}); bans[str(cid)]={}; db_set("chat_bans", bans)
                    txt=format_moderation_message("unban", get_user_mention(user_id=uid, first_name=fname), "всех заблокированных", extra="♻️ Банлист очищен.")
                    return finish_command(m, "amnesty", bot.send_message(cid, txt, parse_mode="HTML"))
                except Exception:
                    return finish_command(m, "amnesty_err", bot.send_message(cid, "⚠️ Не удалось завершить амнистию. Проверь права бота."), ttl=15)

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
                if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
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
                        return finish_command(m, "no_target", bot.send_message(cid, "⚠️ Укажите пользователя (ответом на сообщение или через @юзернейм/ID)."), ttl=10)
                        
                target_cur_rank = get_admin_rank(cid, target_uid)
                # Отдельно берём именно СОХРАНЁННЫЙ в базе ранг для текста
                # "повышен/понижен". get_admin_rank может вернуть 5 для
                # создателя чата (creator-фолбэк), даже если в базе у него
                # другой ранг — из-за этого сравнение new_rank>target_cur_rank
                # ломалось (всегда "понижен").
                with state_lock:
                    target_stored_rank = db_get("chat_admins", {}).get(str(cid), {}).get(str(target_uid), 0)
                
                if cmd not in ["варны", "/warns"]:
                    if target_uid == BOT_ID or (target_uid == uid and cmd not in ["снять мут", "/unmute", "снятьбан", "/unban"]):
                        msg = bot.send_message(cid, "⛔ Это действие нельзя применить к данной цели.")
                        return finish_command(m, "invalid_target", msg, ttl=5)
                        
                    if get_admin_rank(cid, uid) < 5:
                        if get_admin_rank(cid, target_uid) >= 5 or caller_rank <= target_cur_rank:
                            return reply_no_rights(m)

                if cmd in ["повысить", "понизить", "разжаловать"]:
                    if cmd == "разжаловать": new_rank = 0
                    else:
                        if args and args[0].isdigit(): new_rank = int(args[0])
                        else: new_rank = target_cur_rank + 1 if cmd == "повысить" else target_cur_rank - 1
                            
                    if new_rank > 5: new_rank = 5
                    if new_rank < 0: new_rank = 0
                    
                    if get_admin_rank(cid, uid) < 5:
                        if caller_rank <= new_rank and new_rank != 0: return reply_no_rights(m)
                            
                    set_admin_rank(cid, target_uid, new_rank)
                    save_moderation_record(cid, "rank", target_uid, target_name, uid, fname, "", new_rank)
                    if new_rank == 0: txt = f"📉 {get_user_mention(user_id=target_uid, first_name=target_name)} разжалован до обычного участника."
                    else:
                        rank_name = ADMIN_RANKS.get(new_rank, f"{new_rank} РАНГ")
                        act = "повышен" if new_rank >= target_stored_rank else "понижен"
                        icon = "📈" if act == "повышен" else "📉"
                        txt = f"{icon} {get_user_mention(user_id=target_uid, first_name=target_name)} {act} до должности:\n<b>{rank_name}</b>"
                    return finish_command(m, "admin_change", bot.send_message(cid, txt, parse_mode="HTML"))
                    
                elif cmd in ["бан", "/ban"]:
                    dur_secs, parsed, consumed = parse_duration_from_args(args)
                    if parsed:
                        reason = " ".join(args[consumed:]) if len(args) > consumed else "Не указана"
                        time_str = format_seconds_human(dur_secs) if dur_secs > 0 else "навсегда"
                    else:
                        reason = " ".join(args) if args else "Не указана"
                        dur_secs = int(get_v(cid, "ban_period", 0) or 0)
                        time_str = format_seconds_human(dur_secs) if dur_secs > 0 else "навсегда"
                        
                    until = int(time.time() + dur_secs) if dur_secs > 0 else 0
                    try:
                        bot.ban_chat_member(cid, target_uid, until_date=until)
                        with state_lock:
                            bans = db_get("chat_bans", {}); chat_bans = bans.setdefault(str(cid), {})
                            chat_bans[str(target_uid)] = {"until": until, "reason": reason, "by_uid": uid, "by_name": fname, "name": target_name, "date": time.time()}
                            db_set("chat_bans", bans)
                        mention_admin = get_user_mention(user_id=uid, first_name=fname)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = format_moderation_message("ban", mention_admin, mention_target, time_str, reason)
                        log_moderation_action(m.chat.title or str(cid), txt)
                        save_moderation_record(cid, "ban", target_uid, target_name, uid, fname, reason, dur_secs)
                        with state_lock:
                            history=db_get("ban_history", {}); rows=history.setdefault(str(cid), [])
                            rows.append({"target_uid":target_uid,"target_name":target_name,"by_uid":uid,"by_name":fname,"reason":reason,"duration":dur_secs,"date":time.time()})
                            db_set("ban_history", rows[-500:])
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "ban", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["снятьбан", "/unban"]:
                    try:
                        bot.unban_chat_member(cid, target_uid, only_if_banned=True)
                        with state_lock:
                            bans = db_get("chat_bans", {}); bans.setdefault(str(cid), {}).pop(str(target_uid), None); db_set("chat_bans", bans)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = f"✅ Снят бан с {mention_target}"
                        log_moderation_action(m.chat.title or str(cid), txt)
                        save_moderation_record(cid, "unban", target_uid, target_name, uid, fname, "", 0)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "unban", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["кик", "/kick"]:
                    silent = bool(args and args[0].lower() == "тихо")
                    reason = " ".join(args[1:] if silent else args) or "Не указана"
                    try:
                        bot.ban_chat_member(cid, target_uid, until_date=0)
                        bot.unban_chat_member(cid, target_uid, only_if_banned=True)
                        mention_admin = get_user_mention(user_id=uid, first_name=fname)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = format_moderation_message("kick", mention_admin, mention_target, reason=reason)
                        log_moderation_action(m.chat.title or str(cid), txt)
                        save_moderation_record(cid, "kick", target_uid, target_name, uid, fname, reason, 0)
                        if silent:
                            return finish_command(m, "kick_silent", None)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "kick", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["мут", "/mute"]:
                    dur_secs, parsed, consumed = parse_duration_from_args(args)
                    if parsed:
                        reason = " ".join(args[consumed:]) if len(args) > consumed else "Не указана"
                        time_str = format_seconds_human(dur_secs) if dur_secs > 0 else "навсегда"
                    else:
                        reason = " ".join(args) if args else "Не указана"
                        dur_secs = int(get_v(cid, "mute_period", 7 * 86400) or 0)
                        time_str = format_seconds_human(dur_secs) if dur_secs > 0 else "навсегда"
                        
                    until = int(time.time() + dur_secs) if dur_secs > 0 else 0
                    try:
                        bot.restrict_chat_member(cid, target_uid, until_date=until, permissions=ChatPermissions(can_send_messages=False))
                        with state_lock:
                            mutes = db_get("chat_mutes", {}); chat_mutes = mutes.setdefault(str(cid), {})
                            chat_mutes[str(target_uid)] = until
                            db_set("chat_mutes", mutes)
                        mention_admin = get_user_mention(user_id=uid, first_name=fname)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = format_moderation_message("mute", mention_admin, mention_target, time_str, reason)
                        log_moderation_action(m.chat.title or str(cid), txt)
                        save_moderation_record(cid, "mute", target_uid, target_name, uid, fname, reason, dur_secs)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "mute", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["снять мут", "/unmute"]:
                    try:
                        bot.restrict_chat_member(cid, target_uid, permissions=ChatPermissions(
                            can_send_messages=True, can_send_media_messages=True, 
                            can_send_other_messages=True, can_add_web_page_previews=True))
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        with state_lock:
                            mutes = db_get("chat_mutes", {}); mutes.setdefault(str(cid), {}).pop(str(target_uid), None); db_set("chat_mutes", mutes)
                        txt = format_moderation_message("unmute", get_user_mention(user_id=uid, first_name=fname), mention_target)
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

                elif cmd in ["снять пред", "/unwarn"]:
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
                            txt = "🤷‍♀️ У пользователя нет предупреждений."
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

        tg_result = _telegram_admin_command(m, t_lower, t)
        if tg_result is not None:
            return tg_result

        giveaway_result = _giveaway_command(m, t_lower, t)
        if giveaway_result is not None:
            return giveaway_result

        report_result = _reports_command(m, t_lower, t)
        if report_result is not None:
            return report_result

        economy_result = _economy_command(m, t_lower, t)
        if economy_result is not None:
            return economy_result

        vip_result = _vip_catalog_command(m, t_lower, t)
        if vip_result is not None:
            return vip_result

        clan_result = _clans_command(m, t_lower, t)
        if clan_result is not None:
            return clan_result

        circle_result = _circles_command(m, t_lower, t)
        if circle_result is not None:
            return circle_result

        rep_result = _reputation_command(m, t_lower, t)
        if rep_result is not None:
            return rep_result
        relationship_result = _relationships_command(m, t_lower, t)
        if relationship_result is not None:
            return relationship_result
        awards_result = _awards_command(m, t_lower, t)
        if awards_result is not None:
            return awards_result

        bookmark_result = _bookmarks_command(m, t_lower, t)
        if bookmark_result is not None:
            return bookmark_result

        note_result = _notes_timers_command(m, t_lower, t)
        if note_result is not None:
            return note_result

        # --- IRIS: ники, баны и служебная статистика ---
        if t_lower == "ники" or t_lower.startswith("ники "):
            profiles = db_get("chat_profiles", {}).get(str(cid), {})
            page = 1
            parts_n = t_lower.split()
            if len(parts_n) > 1 and parts_n[1].isdigit(): page = max(1, int(parts_n[1]))
            rows=[]
            for suid, prof in profiles.items():
                nick=(prof or {}).get("nickname")
                if nick: rows.append((nick.lower(), int(suid), nick))
            rows.sort()
            per=20; start=(page-1)*per; chunk=rows[start:start+per]
            lines=[f"{start+i}. {get_user_mention(user_id=suid)} — <b>{html.escape(nick)}</b>" for i,( _,suid,nick) in enumerate(chunk,1)]
            total_pages=max(1,(len(rows)+per-1)//per)
            txt=f"🏷 <b>НИКИ</b> · страница {page}/{total_pages}\n\n" + ("\n".join(lines) if lines else "Ников пока нет.")
            return finish_command(m,"nicks_list",bot.send_message(cid,txt,parse_mode="HTML"),ttl=60)

        if t_lower == "!сброс ников":
            if get_admin_rank(cid,uid) < 5: return reply_no_rights(m)
            profiles=db_get("chat_profiles",{}); chat_profiles=profiles.setdefault(str(cid),{})
            removed=0
            for prof in chat_profiles.values():
                if isinstance(prof,dict) and prof.pop("nickname",None) is not None: removed+=1
            db_set("chat_profiles",profiles)
            return finish_command(m,"nicks_reset",bot.send_message(cid,f"🧹 Ники сброшены. Удалено: <b>{removed}</b>.",parse_mode="HTML"),ttl=20)

        if t_lower in ("мои баны", "мой бан"):
            history=db_get("ban_history",{}).get(str(cid),[]) or []
            mine=[v for v in history if int((v or {}).get("by_uid",0) or 0)==uid]
            mine.sort(key=lambda x: float((x or {}).get("date",0) or 0), reverse=True)
            lines=[]
            for i,v in enumerate(mine[:20],1):
                reason=html.escape(str(v.get("reason") or "без причины"))
                name=html.escape(str(v.get("target_name") or "пользователь"))
                dt=datetime.fromtimestamp(float(v.get("date",0) or 0),KYIV_TZ).strftime("%d.%m.%Y %H:%M")
                lines.append(f"{i}. <b>{name}</b> · {reason} · {dt}")
            return finish_command(m,"my_bans",bot.send_message(cid,"🔨 <b>МОИ БАНЫ</b>\n\n"+("\n".join(lines) if lines else "История банов пока пуста."),parse_mode="HTML"),ttl=45)

        if t_lower == "мой спам":
            spam=db_get("chat_spam",{}).get(str(cid),{})
            row=spam.get(str(uid),{}) if isinstance(spam,dict) else {}
            count=int(row.get("count",0) or 0) if isinstance(row,dict) else 0
            if not count:
                records=db_get("moderation_log",{}).get(str(cid),[]) or []
                count=sum(1 for r in records if int((r or {}).get("target_uid",0) or 0)==uid and str((r or {}).get("action","")).lower() in ("spam","antispam","спам"))
            return finish_command(m,"my_spam",bot.send_message(cid,f"🚫 <b>МОЙ СПАМ</b>\n\nЗафиксировано нарушений: <b>{count}</b>.",parse_mode="HTML"),ttl=30)

        # --- IRIS: расширенная статистика ---
        if t_lower in ("+стата", "+статистика"):
            set_v(cid, "stats_enabled", True)
            return finish_command(m, "stats_on", bot.send_message(cid, "📊 Статистика Лизы включена для этого чата."), ttl=15)
        if t_lower in ("-стата", "-статистика"):
            set_v(cid, "stats_enabled", False)
            return finish_command(m, "stats_off", bot.send_message(cid, "📊 Статистика Лизы выключена. История не удалена."), ttl=15)
        if t_lower == "!актив ириса":
            activity = db_get("chat_activity", {}).get(str(cid), {})
            active = sum(1 for row in activity.values() if time.time() - float(row.get("last_seen", 0) or 0) <= 15*60)
            total = len(activity)
            txt = f"📊 <b>АКТИВ ИРИСА</b>\n\n👥 Участников в статистике: <b>{total}</b>\n🟢 Активны за 15 минут: <b>{active}</b>"
            return finish_command(m, "iris_active", bot.send_message(cid, txt, parse_mode="HTML"), ttl=30)
        if t_lower == "чат инфо":
            try: member_count = bot.get_chat_member_count(cid)
            except Exception: member_count = len(db_get("chat_activity", {}).get(str(cid), {}))
            txt = f"ℹ️ <b>ИНФОРМАЦИЯ О ЧАТЕ</b>\n\nНазвание: <b>{html.escape(m.chat.title or 'Без названия')}</b>\nID: <code>{cid}</code>\nУчастников: <b>{member_count}</b>"
            return finish_command(m, "chat_info", bot.send_message(cid, txt, parse_mode="HTML"), ttl=45)
        if t_lower.startswith("чат стата"):
            parts2=t_lower.split()
            days=1
            if len(parts2)>2 and parts2[2].isdigit(): days=max(1,min(365,int(parts2[2])))
            since=time.time()-days*86400
            rows=[]
            for suid,row in db_get("chat_activity", {}).get(str(cid), {}).items():
                daily=row.get("daily",{}) or {}
                count=sum(int(v or 0) for k,v in daily.items() if datetime.strptime(k,"%Y-%m-%d").replace(tzinfo=KYIV_TZ).timestamp()>=since) if daily else 0
                if count: rows.append((count,int(suid),row.get("name","Участник")))
            rows.sort(reverse=True)
            total=sum(x[0] for x in rows)
            lines=[f"{i}. {get_user_mention(user_id=u, first_name=n)} — <b>{c}</b>" for i,(c,u,n) in enumerate(rows[:15],1)]
            txt=f"📈 <b>СТАТИСТИКА ЧАТА ЗА {days} ДН.</b>\n\nСообщений: <b>{total}</b>\n" + ("\n".join(lines) if lines else "Нет данных за этот период.")
            return finish_command(m,"chat_stats_period",bot.send_message(cid,txt,parse_mode="HTML"),ttl=90)
        if t_lower in ("стата по часам", "стата по часам я", "чат стата по часам") or t_lower.startswith("стата по часам "):
            target_uid, target_name, _ = extract_target_and_args(m, parts)
            target_uid=target_uid or uid
            row=db_get("chat_activity", {}).get(str(cid), {}).get(str(target_uid),{})
            hourly=row.get("hourly",{}) or {}
            vals=[(int(hourly.get(f"{h:02d}",0) or 0),h) for h in range(24)]
            vals.sort(reverse=True)
            lines=[f"{h:02d}:00–{h:02d}:59 — <b>{c}</b>" for c,h in vals if c][:10]
            title="ТВОЯ СТАТИСТИКА ПО ЧАСАМ" if target_uid==uid else f"СТАТИСТИКА ПО ЧАСАМ — {html.escape(target_name or 'пользователь')}"
            txt=f"🕐 <b>{title}</b>\n\n" + ("\n".join(lines) if lines else "Пока недостаточно данных.")
            return finish_command(m,"hour_stats",bot.send_message(cid,txt,parse_mode="HTML"),ttl=60)
        if t_lower in ("стата сутки", "стата день", "стата за сутки") or t_lower in ("стата неделя", "стата за неделю") or t_lower in ("стата месяц", "стата за месяц") or t_lower in ("стата вся", "стата всё"):
            period=1 if "сут" in t_lower or "день" in t_lower else 7 if "нед" in t_lower else 30 if "меся" in t_lower else 36500
            row=db_get("chat_activity", {}).get(str(cid), {}).get(str(uid),{})
            daily=row.get("daily",{}) or {}
            cutoff=datetime.fromtimestamp(time.time()-period*86400,KYIV_TZ).date()
            count=sum(int(v or 0) for k,v in daily.items() if datetime.strptime(k,"%Y-%m-%d").date()>=cutoff)
            label="сутки" if period==1 else "неделю" if period==7 else "месяц" if period==30 else "всё время"
            return finish_command(m,"my_period_stats",bot.send_message(cid,f"📊 {get_user_mention(m.from_user)} за <b>{label}</b>: <b>{count}</b> сообщений.",parse_mode="HTML"),ttl=30)
        if t_lower in ("олды", "новички"):
            activity=db_get("chat_activity", {}).get(str(cid), {})
            rows=[]
            for suid,row in activity.items():
                first=float(row.get("first_seen",time.time()) or time.time())
                rows.append((first,int(suid),row.get("name","Участник")))
            rows.sort(reverse=(t_lower=="новички"))
            title="НОВИЧКИ" if t_lower=="новички" else "ОЛДЫ"
            lines=[f"{i}. {get_user_mention(user_id=u,first_name=n)} — {datetime.fromtimestamp(ts,KYIV_TZ).strftime('%d.%m.%Y')}" for i,(ts,u,n) in enumerate(rows[:15],1)]
            return finish_command(m,"olds_new",bot.send_message(cid,f"👥 <b>{title}</b>\n\n"+("\n".join(lines) if lines else "Нет данных."),parse_mode="HTML"),ttl=60)
        if t_lower == "!население":
            activity=db_get("chat_activity", {}).get(str(cid), {})
            return finish_command(m,"population",bot.send_message(cid,f"👥 В локальной статистике чата: <b>{len(activity)}</b> участников.",parse_mode="HTML"),ttl=30)

        # --- IRIS: дополнительные статистические списки ---
        if t_lower in ("олды", "новички"):
            activity = db_get("chat_activity", {}).get(str(cid), {})
            rows=[]; now=time.time()
            for suid,row in activity.items():
                first=float(row.get("first_seen", now) or now)
                rows.append((first,int(suid),row.get("name","Участник")))
            # Старожилы — самые ранние, новички — самые поздние регистрации.
            rows.sort(key=lambda x:x[0], reverse=(t_lower=="новички"))
            title="НОВИЧКИ" if t_lower=="новички" else "ОЛДЫ"
            lines=[]
            for i,(first,suid,name) in enumerate(rows[:20],1):
                dt=datetime.fromtimestamp(first,KYIV_TZ).strftime("%d.%m.%Y")
                lines.append(f"{i}. {get_user_mention(user_id=suid,first_name=name)} — с <b>{dt}</b>")
            return finish_command(m,"olds_new",bot.send_message(cid,f"👥 <b>{title}</b>\n\n"+("\n".join(lines) if lines else "Нет данных."),parse_mode="HTML"),ttl=60)

        if t_lower in ("кто вип", "кто не вип", "кто випы", "кто не випы"):
            activity=db_get("chat_activity",{}).get(str(cid),{})
            vips=db_get("user_vip",{}) or {}; now=time.time()
            want_vip=t_lower in ("кто вип","кто випы")
            rows=[]
            for suid,row in activity.items():
                active=float((vips.get(str(suid),{}) or {}).get("until",0) or 0)>now
                if active==want_vip: rows.append((int(suid),row.get("name","Участник")))
            title="VIP УЧАСТНИКИ" if want_vip else "УЧАСТНИКИ БЕЗ VIP"
            lines=[f"• {get_user_mention(user_id=u,first_name=n)}" for u,n in rows[:40]]
            return finish_command(m,"vip_users",bot.send_message(cid,f"💎 <b>{title}</b>\n\n"+("\n".join(lines) if lines else "Никого не найдено."),parse_mode="HTML"),ttl=60)

        if t_lower.startswith("список неактив") or t_lower.startswith("список молчунов") or t_lower.startswith("список по смс"):
            parts2=t_lower.split(); days=7
            if len(parts2)>2 and parts2[-1].isdigit(): days=max(1,min(365,int(parts2[-1])))
            activity=db_get("chat_activity",{}).get(str(cid),{}); now=time.time()
            if t_lower.startswith("список по смс"):
                rows=sorted([(int(r.get("msgs",0) or 0),int(s),r.get("name","Участник")) for s,r in activity.items()], key=lambda x:x[0])
                title="УЧАСТНИКИ ПО КОЛИЧЕСТВУ СООБЩЕНИЙ"
                lines=[f"{i}. {get_user_mention(user_id=s,first_name=n)} — <b>{c}</b>" for i,(c,s,n) in enumerate(rows[:30],1)]
            else:
                rows=[]
                for s,r in activity.items():
                    last=float(r.get("last_seen",0) or 0)
                    if not last or now-last>=days*86400: rows.append((last,int(s),r.get("name","Участник")))
                rows.sort(key=lambda x:x[0])
                title="МОЛЧУНЫ" if t_lower.startswith("список молчунов") else "НЕАКТИВНЫЕ"
                lines=[]
                for _,s,n in rows[:30]: lines.append(f"• {get_user_mention(user_id=s,first_name=n)}")
            return finish_command(m,"inactive_lists",bot.send_message(cid,f"📋 <b>{title}</b>\nПериод: {days} дн.\n\n"+("\n".join(lines) if lines else "Никого не найдено."),parse_mode="HTML"),ttl=60)

        if t_lower == "мой актив":
            row=db_get("chat_activity",{}).get(str(cid),{}).get(str(uid),{})
            last=float(row.get("last_seen",0) or 0); first=float(row.get("first_seen",last) or last)
            return finish_command(m,"my_active",bot.send_message(cid,f"📈 <b>МОЙ АКТИВ</b>\n\nСообщений: <b>{int(row.get('msgs',0) or 0)}</b>\nПервое появление: <b>{datetime.fromtimestamp(first,KYIV_TZ).strftime('%d.%m.%Y %H:%M') if first else '—'}</b>\nПоследняя активность: <b>{datetime.fromtimestamp(last,KYIV_TZ).strftime('%d.%m.%Y %H:%M') if last else '—'}</b>",parse_mode="HTML"),ttl=30)

        # --- IRIS: статистика и онлайн ---
        if t_lower in ("статистика", "моя статистика", "моя стат", "стата"):
            flush_stats(uid)
            users = db_get("users_data", {})
            u = users.get(str(uid), {})
            activity = db_get("chat_activity", {}).get(str(cid), {}).get(str(uid), {})
            msgs = int(u.get("msgs", 0) or 0)
            first_seen = activity.get("first_seen", u.get("first_seen", time.time()))
            last_seen = activity.get("last_seen", u.get("last_seen", time.time()))
            days = max(0, int((time.time() - first_seen) / 86400))
            last = datetime.fromtimestamp(last_seen, KYIV_TZ).strftime("%d.%m.%Y %H:%M")
            txt = (
                "📊 <b>СТАТИСТИКА ЛИЗЫ</b>\n\n"
                f"👤 {get_user_mention(m.from_user)}\n"
                f"💬 Сообщений: <b>{msgs}</b>\n"
                f"⭐ XP: <b>{int(u.get('xp', 0) or 0)}</b>\n"
                f"📅 В чате: <b>{days} дн.</b>\n"
                f"🕒 Последняя активность: <b>{last}</b>\n\n"
                "<i>Статистика ведётся отдельно для каждого чата.</i>"
            )
            return finish_command(m, "stats", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

        if t_lower in ("топ", "топ чата", "топ сообщений", "статистика чата"):
            activity = db_get("chat_activity", {}).get(str(cid), {})
            rows = []
            for suid, data in activity.items():
                try: count = int(data.get("msgs", 0) or 0)
                except Exception: count = 0
                if count:
                    rows.append((count, int(suid), data.get("name", "Участник")))
            rows.sort(reverse=True)
            lines = []
            for i, (count, suid, name) in enumerate(rows[:10], 1):
                icon = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else "▫️"
                lines.append(f"{icon} <b>{i}.</b> {get_user_mention(user_id=suid, first_name=name)} — <b>{count}</b>")
            txt = "📊 <b>ТОП УЧАСТНИКОВ ЧАТА</b>\n\n" + ("\n".join(lines) if lines else "Пока статистики недостаточно.")
            return finish_command(m, "chat_top", bot.send_message(cid, txt, parse_mode="HTML"), ttl=90)

        if t_lower in ("онлайн", "кто онлайн", "мой онлайн"):
            activity = db_get("chat_activity", {}).get(str(cid), {})
            now = time.time()
            online = []
            for suid, data in activity.items():
                last = float(data.get("last_seen", 0) or 0)
                if last and now - last <= 15 * 60:
                    online.append((last, int(suid), data.get("name", "Участник")))
            online.sort(reverse=True)
            if t_lower == "мой онлайн":
                last = activity.get(str(uid), {}).get("last_seen", 0)
                state = "🟢 онлайн" if last and now-last <= 15*60 else "⚪️ оффлайн"
                txt = f"🟢 <b>МОЙ ОНЛАЙН</b>\n\n{get_user_mention(m.from_user)} — {state}"
            else:
                lines = [f"• {get_user_mention(user_id=u, first_name=n)}" for _,u,n in online[:30]]
                txt = "🟢 <b>СЕЙЧАС АКТИВНЫ</b>\n\n" + ("\n".join(lines) if lines else "Никто не проявлял активность последние 15 минут.")
            return finish_command(m, "online", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

        # --- IRIS: история приглашений ---
        if t_lower in ("кто меня добавил", "кто добавил меня") or t_lower.startswith("кто добавил "):
            target_uid, target_name, _ = extract_target_and_args(m)
            if t_lower in ("кто меня добавил", "кто добавил меня"):
                target_uid = uid
            if not target_uid:
                return finish_command(m, "inviter_err", bot.send_message(cid, "⚠️ Укажи пользователя через @username, ID или reply."), ttl=10)
            rec = db_get("chat_invites", {}).get(str(cid), {}).get(str(target_uid))
            if not rec:
                return finish_command(m, "inviter_none", bot.send_message(cid, "ℹ️ Лиза не видит, кто добавил этого пользователя. Telegram не передал данные о пригласившем."), ttl=20)
            inviter_id = int(rec.get("inviter_id", 0) or 0)
            inviter_name = rec.get("inviter_name") or "Пользователь"
            when = datetime.fromtimestamp(float(rec.get("date", 0) or 0), KYIV_TZ).strftime("%d.%m.%Y %H:%M")
            return finish_command(m, "inviter", bot.send_message(cid, f"👤 {get_user_mention(user_id=target_uid, first_name=target_name)} добавил(а): {get_user_mention(user_id=inviter_id, first_name=inviter_name)}\n🕒 {when}", parse_mode="HTML"), ttl=30)

        # --- IRIS: базовые идентификаторы и навигация ---
        if t_lower in ("смс ид", "!смс ид", ".смс ид", "/смс ид"):
            if not m.reply_to_message:
                return finish_command(m, "sms_id_err", bot.send_message(cid, "⚠️ Используй команду ответом на нужное сообщение."), ttl=10)
            mid = m.reply_to_message.message_id
            return finish_command(m, "sms_id", bot.send_message(cid, f"🆔 ID сообщения: <code>{mid}</code>", parse_mode="HTML"), ttl=20)

        if t_lower.startswith("перейти к смс "):
            try: mid = int(t_lower.split()[-1])
            except Exception: return finish_command(m, "goto_sms_err", bot.send_message(cid, "⚠️ Укажи числовой ID сообщения."), ttl=10)
            try:
                link = f"https://t.me/c/{str(cid).replace('-100','')}/{mid}" if str(cid).startswith("-100") else None
                if link:
                    return finish_command(m, "goto_sms", bot.send_message(cid, f"🔗 <a href=\"{link}\">Перейти к сообщению {mid}</a>", parse_mode="HTML"), ttl=30)
            except Exception: pass
            return finish_command(m, "goto_sms_err", bot.send_message(cid, "⚠️ Не удалось сформировать ссылку для этого чата."), ttl=10)

        if t_lower in ("чат ид", "!чат ид", ".чат ид", "/чат ид"):
            return finish_command(m, "chat_id", bot.send_message(cid, f"🆔 ID чата: <code>{cid}</code>", parse_mode="HTML"), ttl=20)

        if t_lower in ("код чата", "код беседы"):
            # Локальный код безопасно привязан к Telegram ID и подходит для будущей сетки.
            code = str(abs(cid))
            return finish_command(m, "chat_code", bot.send_message(cid, f"🔑 Код чата: <code>{code}</code>", parse_mode="HTML"), ttl=30)

        if t_lower in ("кто я", "профиль", "анкета", "моя анкета"):
            finish_command(m, "profile")
            return handle_profile_request(m, uid, m.from_user)

        if t_lower.startswith("кто ты ") and not m.reply_to_message:
            target_uid, _, _ = extract_target_and_args(m, parts)
            if target_uid:
                finish_command(m, "who_are_you")
                return handle_profile_request(m, target_uid)

        # Небольшие поля анкеты Iris-style. Хранятся отдельно для каждого чата.
        if t_lower.startswith("+ник ") or t_lower == "ник удалить" or t_lower == "-ник":
            chat_users = db_get("chat_profiles", {})
            profile = chat_users.setdefault(str(cid), {}).setdefault(str(uid), {})
            if t_lower in ("ник удалить", "-ник"):
                profile.pop("nickname", None)
                db_set("chat_profiles", chat_users)
                return finish_command(m, "nick_del", bot.send_message(cid, "✅ Ник удалён."), ttl=10)
            value = t.split(maxsplit=1)[1].strip()[:30]
            profile["nickname"] = value
            db_set("chat_profiles", chat_users)
            return finish_command(m, "nick_set", bot.send_message(cid, f"✅ Твой ник: <b>{html.escape(value)}</b>", parse_mode="HTML"), ttl=15)

        if t_lower in ("ник", "+ник"):
            prof = db_get("chat_profiles", {}).get(str(cid), {}).get(str(uid), {})
            return finish_command(m, "nick_show", bot.send_message(cid, f"🏷 Твой ник: <b>{html.escape(prof.get('nickname','не установлен'))}</b>", parse_mode="HTML"), ttl=20)

        if t_lower.startswith("+звание ") or t_lower == "звание удалить" or t_lower == "-звание":
            chat_users = db_get("chat_profiles", {})
            profile = chat_users.setdefault(str(cid), {}).setdefault(str(uid), {})
            if t_lower in ("звание удалить", "-звание"):
                profile.pop("title", None)
                db_set("chat_profiles", chat_users)
                return finish_command(m, "title_del", bot.send_message(cid, "✅ Звание удалено."), ttl=10)
            value = t.split(maxsplit=1)[1].strip()[:30]
            profile["title"] = value
            db_set("chat_profiles", chat_users)
            return finish_command(m, "title_set", bot.send_message(cid, f"🎖 Твоё звание: <b>{html.escape(value)}</b>", parse_mode="HTML"), ttl=15)

        if t_lower in ("звание", "+звание"):
            prof = db_get("chat_profiles", {}).get(str(cid), {}).get(str(uid), {})
            return finish_command(m, "title_show", bot.send_message(cid, f"🎖 Твоё звание: <b>{html.escape(prof.get('title','не установлено'))}</b>", parse_mode="HTML"), ttl=20)

        if t_lower.startswith("+девиз ") or t_lower == "-девиз" or t_lower == "девиз":
            chat_users = db_get("chat_profiles", {})
            profile = chat_users.setdefault(str(cid), {}).setdefault(str(uid), {})
            if t_lower == "-девиз":
                profile.pop("motto", None); db_set("chat_profiles", chat_users)
                return finish_command(m, "motto_del", bot.send_message(cid, "✅ Девиз удалён."), ttl=10)
            if t_lower == "девиз":
                return finish_command(m, "motto_show", bot.send_message(cid, f"💬 Девиз: <i>{html.escape(profile.get('motto','не установлен'))}</i>", parse_mode="HTML"), ttl=20)
            value=t.split(maxsplit=1)[1].strip()[:100]
            profile["motto"]=value; db_set("chat_profiles", chat_users)
            return finish_command(m, "motto_set", bot.send_message(cid, "✅ Девиз сохранён."), ttl=10)

        # --- IRIS: СЕТКА ЧАТОВ ---
        # Сетка хранится как общий объект с владельцем и списком chat_id.
        # Команды намеренно требуют создателя текущего чата, чтобы нельзя было
        # самовольно присоединить чужой чат к чужой сетке.
        if t_lower in ("чаты", "сетка чаты"):
            grids = db_get("chat_grids", {})
            my_grids = [g for g in grids.values() if cid in g.get("chats", [])]
            if not my_grids:
                return finish_command(m, "grid_none", bot.send_message(cid, "🌐 Этот чат пока не состоит ни в одной сетке."), ttl=20)
            g = my_grids[0]
            lines = []
            for gcid in g.get("chats", []):
                try:
                    ch = bot.get_chat(gcid)
                    title = ch.title or str(gcid)
                    desc = ch.description or "Описание не задано"
                except Exception:
                    title, desc = str(gcid), "Нет доступа к информации"
                lines.append(f"• <b>{html.escape(title)}</b>\n  <i>{html.escape(desc[:120])}</i>")
            txt = "🌐 <b>СЕТКА ЧАТОВ</b>\n\n" + "\n".join(lines)
            return finish_command(m, "grid_chats", bot.send_message(cid, txt, parse_mode="HTML"), ttl=90)

        if t_lower.startswith("+сетка") or t_lower.startswith("-сетка"):
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            grids = db_get("chat_grids", {})
            # +Сетка создаёт сетку из текущего чата; +Сетка <код/ID> добавляет чат.
            if t_lower.startswith("+сетка"):
                arg = t.strip()[6:].strip()
                existing = None
                for gid, g in grids.items():
                    if cid in g.get("chats", []):
                        existing = (gid, g); break
                if existing:
                    gid, g = existing
                else:
                    gid = str(random.randint(10000000, 99999999))
                    while gid in grids: gid = str(random.randint(10000000, 99999999))
                    g = {"owner": uid, "chats": [cid], "global_mods": {}, "global_admins": {}, "created": time.time()}
                    grids[gid] = g
                if arg:
                    try:
                        target_cid = int(arg)
                    except Exception:
                        target_cid = None
                    if target_cid and target_cid not in g["chats"]:
                        # Добавлять можно только чат, где этот бот уже установлен.
                        try:
                            me = bot.get_chat_member(target_cid, BOT_ID)
                            if me.status not in ("administrator", "creator"):
                                return finish_command(m, "grid_err", bot.send_message(cid, "⚠️ В указанном чате Лиза не является администратором."), ttl=15)
                            g["chats"].append(target_cid)
                        except Exception:
                            return finish_command(m, "grid_err", bot.send_message(cid, "⚠️ Не удалось проверить указанный чат."), ttl=15)
                db_set("chat_grids", grids)
                return finish_command(m, "grid_ok", bot.send_message(cid, f"🌐 <b>Сетка сохранена.</b>\nКод: <code>{gid}</code>\nЧатов: <b>{len(g['chats'])}</b>", parse_mode="HTML"), ttl=30)
            else:
                my = next(((gid,g) for gid,g in grids.items() if cid in g.get("chats", []) and g.get("owner") == uid), None)
                if not my:
                    return reply_no_rights(m)
                gid,g=my
                arg=t.strip()[6:].strip()
                try: target=int(arg)
                except Exception: target=None
                if target and target in g.get("chats",[]) and target != cid:
                    g["chats"].remove(target)
                elif target is None:
                    if len(g.get("chats",[])) > 1:
                        g["chats"].remove(cid)
                    else:
                        grids.pop(gid,None)
                db_set("chat_grids", grids)
                return finish_command(m, "grid_off", bot.send_message(cid, "🌐 Чат удалён из сетки."), ttl=15)

        # Глобальные модераторы/администраторы сетки.
        if t_lower in ("сетка модеры", "сетка модераторы"):
            grids=db_get("chat_grids",{}); g=next((g for g in grids.values() if cid in g.get("chats",[])),None)
            if not g: return finish_command(m,"grid_none",bot.send_message(cid,"🌐 Чат не состоит в сетке."),ttl=15)
            rows=[]
            for suid,rank in g.get("global_mods",{}).items(): rows.append(f"• {get_user_mention(user_id=int(suid), first_name=db_get('users_data',{}).get(str(suid),{}).get('name','Участник'))} — ранг {rank}")
            for suid in g.get("global_admins",{}): rows.append(f"• {get_user_mention(user_id=int(suid), first_name=db_get('users_data',{}).get(str(suid),{}).get('name','Участник'))} — гладмин")
            return finish_command(m,"grid_mods",bot.send_message(cid,"🌐 <b>ГЛОБАЛЬНАЯ МОДЕРАЦИЯ</b>\n\n"+("\n".join(rows) if rows else "Пока никого нет."),parse_mode="HTML"),ttl=60)

        if t_lower.startswith("+глмодер") or t_lower.startswith("-глмодер") or t_lower.startswith("+гладмин") or t_lower.startswith("-гладмин"):
            grids=db_get("chat_grids",{}); found=next(((gid,g) for gid,g in grids.items() if cid in g.get("chats",[])),None)
            if not found: return finish_command(m,"grid_none",bot.send_message(cid,"🌐 Сначала создайте или подключите чат к сетке."),ttl=15)
            gid,g=found
            is_owner=g.get("owner")==uid
            is_gladmin=str(uid) in g.get("global_admins",{})
            if not (is_owner or is_gladmin): return reply_no_rights(m)
            target_uid,target_name,_=extract_target_and_args(m,parts)
            if not target_uid: return finish_command(m,"grid_target",bot.send_message(cid,"⚠️ Укажи пользователя ответом, @username или ID."),ttl=10)
            add=t_lower.startswith("+")
            if "гладмин" in t_lower:
                if not is_owner: return reply_no_rights(m)
                if add: g.setdefault("global_admins",{})[str(target_uid)]=time.time()
                else: g.setdefault("global_admins",{}).pop(str(target_uid),None)
                msg="назначен гладмином" if add else "снят с глобальных администраторов"
            else:
                if add:
                    rank=1
                    if len(parts)>1 and parts[1].isdigit(): rank=max(1,min(5,int(parts[1])))
                    g.setdefault("global_mods",{})[str(target_uid)]=rank; msg=f"назначен глобальным модератором {rank} ранга"
                else:
                    g.setdefault("global_mods",{}).pop(str(target_uid),None); msg="снят с глобальной модерации"
            db_set("chat_grids",grids)
            return finish_command(m,"grid_role",bot.send_message(cid,f"🌐 {get_user_mention(user_id=target_uid,first_name=target_name)} {msg}.",parse_mode="HTML"),ttl=20)

        # Глобальный бан/разбан/кик в сетке.
        if t_lower.startswith("глобан") or t_lower.startswith("глоразбан") or t_lower.startswith("сетка баны") or t_lower.startswith("сетка кик"):
            grids=db_get("chat_grids",{}); found=next(((gid,g) for gid,g in grids.items() if cid in g.get("chats",[])),None)
            if not found: return finish_command(m,"grid_none",bot.send_message(cid,"🌐 Чат не состоит в сетке."),ttl=15)
            gid,g=found
            if g.get("owner")!=uid and str(uid) not in g.get("global_admins",{}): return reply_no_rights(m)
            if t_lower=="сетка баны":
                bans=g.get("global_bans",{})
                rows=[f"• {get_user_mention(user_id=int(s), first_name=db_get('users_data',{}).get(s,{}).get('name','Участник'))}" for s in bans]
                return finish_command(m,"grid_bans",bot.send_message(cid,"🌐 <b>ГЛОБАЛЬНЫЕ БАНЫ</b>\n\n"+("\n".join(rows) if rows else "Список пуст."),parse_mode="HTML"),ttl=60)
            target_uid,target_name,_=extract_target_and_args(m,parts)
            if not target_uid: return finish_command(m,"grid_target",bot.send_message(cid,"⚠️ Укажи пользователя."),ttl=10)
            if t_lower.startswith("глобан"):
                g.setdefault("global_bans",{})[str(target_uid)]={"name":target_name,"date":time.time()}
                action="заблокирован во всей сетке"
            elif t_lower.startswith("глоразбан"):
                g.setdefault("global_bans",{}).pop(str(target_uid),None); action="разблокирован в сетке"
            else:
                action="исключён из всех чатов сетки"
            if t_lower.startswith("глобан") or t_lower.startswith("глоразбан") or t_lower.startswith("сетка кик"):
                for gcid in g.get("chats",[]):
                    try:
                        if t_lower.startswith("глобан"): bot.ban_chat_member(gcid,target_uid)
                        elif t_lower.startswith("глоразбан"): bot.unban_chat_member(gcid,target_uid,only_if_banned=True)
                        else:
                            bot.ban_chat_member(gcid,target_uid); bot.unban_chat_member(gcid,target_uid,only_if_banned=True)
                    except Exception: pass
            db_set("chat_grids",grids)
            return finish_command(m,"grid_action",bot.send_message(cid,f"🌐 {get_user_mention(user_id=target_uid,first_name=target_name)} {action}.",parse_mode="HTML"),ttl=30)

        # --- IRIS: РАСШИРЕННАЯ АНКЕТА ---
        if t_lower in ("моя анкета", "анкета", "кто я", "профиль") or t_lower.startswith("анкета ") or t_lower.startswith("профиль "):
            target_uid,target_name,_=extract_target_and_args(m,parts)
            target_uid=target_uid or uid
            users=db_get("users_data",{}); u=users.get(str(target_uid),{})
            profiles=db_get("user_profiles",{}); prof=profiles.get(str(target_uid),{})
            visible=prof.get("visible",True)
            if target_uid != uid and not visible:
                return finish_command(m,"profile_private",bot.send_message(cid,"🔒 Пользователь скрыл свою анкету."),ttl=15)
            name=prof.get("nickname") or u.get("name") or target_name or "Участник"
            lines=[f"👤 <b>{html.escape(name)}</b>"]
            if prof.get("description"): lines.append(f"📝 {html.escape(prof['description'])}")
            if prof.get("title"): lines.append(f"🎖 {html.escape(prof['title'])}")
            if prof.get("motto"): lines.append(f"💬 {html.escape(prof['motto'])}")
            if prof.get("gender"): lines.append(f"⚧ Пол: {html.escape(prof['gender'])}")
            if prof.get("city"): lines.append(f"📍 Город: {html.escape(prof['city'])}")
            if prof.get("birthday"): lines.append(f"🎂 Дата рождения: {html.escape(prof['birthday'])}")
            if prof.get("citizen"): lines.append("🏡 Гражданин этого чата")
            lines.append(f"⭐ XP: <b>{int(u.get('xp',0) or 0)}</b> • сообщений: <b>{int(u.get('msgs',0) or 0)}</b>")
            return finish_command(m,"profile_full",bot.send_message(cid,"\n".join(lines),parse_mode="HTML"),ttl=60)

        # visibility and profile attributes
        if t_lower in ("+анкета","-анкета"):
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            p["visible"]=t_lower=="+анкета"; db_set("user_profiles",profiles)
            return finish_command(m,"profile_visibility",bot.send_message(cid,"👤 Анкета теперь "+("видна другим пользователям." if p["visible"] else "скрыта от других пользователей.")),ttl=15)
        if t_lower.startswith("мой пол ") or t_lower=="-мой пол":
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            if t_lower=="-мой пол": p.pop("gender",None); msg="Пол удалён."
            else:
                val=parts[-1].lower();
                if val not in ("м","ж","др"): return finish_command(m,"profile_err",bot.send_message(cid,"⚠️ Укажи: м, ж или др."),ttl=10)
                p["gender"]={"м":"мужской","ж":"женский","др":"другое"}[val]; msg="Пол сохранён."
            db_set("user_profiles",profiles); return finish_command(m,"profile_gender",bot.send_message(cid,"✅ "+msg),ttl=10)
        if t_lower.startswith("!мой город ") or t_lower.startswith("мой город ") or t_lower=="-мой город":
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            if t_lower=="-мой город": p.pop("city",None); msg="Город удалён."
            else: p["city"]=t.split(None,2)[2][:80]; msg="Город сохранён."
            db_set("user_profiles",profiles); return finish_command(m,"profile_city",bot.send_message(cid,"✅ "+msg),ttl=10)
        if t_lower.startswith("мой др ") or t_lower=="-мой др":
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            if t_lower=="-мой др": p.pop("birthday",None); msg="Дата рождения удалена."
            else:
                value=parts[2] if len(parts)>2 else ""
                if not re.match(r'^\d{2}\.\d{2}\.\d{2,4}$',value): return finish_command(m,"profile_err",bot.send_message(cid,"⚠️ Формат: Мой др ДД.ММ.ГГГГ"),ttl=10)
                p["birthday"]=value; msg="Дата рождения сохранена."
            db_set("user_profiles",profiles); return finish_command(m,"profile_bday",bot.send_message(cid,"✅ "+msg),ttl=10)
        if t_lower.startswith("о себе") or t_lower.startswith("описание ") or t_lower=="-о себе":
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            if t_lower=="-о себе": p.pop("description",None); msg="Описание удалено."
            elif t_lower.startswith("о себе"):
                value=t.split("\n",1)[1].strip() if "\n" in t else t[len("о себе"):].strip()
                if not value: return finish_command(m,"profile_err",bot.send_message(cid,"⚠️ Напиши описание после команды, лучше с новой строки."),ttl=10)
                p["description"]=value[:3800]; msg="Описание сохранено."
            else:
                target_uid,target_name,_=extract_target_and_args(m,parts); value=t.split("\n",1)[1].strip() if "\n" in t else ""
                if target_uid and value and get_admin_rank(cid,uid)>=5:
                    tp=profiles.setdefault(str(target_uid),{}); tp["description"]=value[:3800]; msg="Описание пользователя изменено."
                else: return finish_command(m,"profile_err",bot.send_message(cid,"⚠️ Для своей анкеты используй «О себе» и текст ниже команды."),ttl=10)
            db_set("user_profiles",profiles); return finish_command(m,"profile_desc",bot.send_message(cid,"✅ "+msg),ttl=10)
        if t_lower.startswith("+гражданство"):
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{}); p["citizen"]=True; db_set("user_profiles",profiles)
            return finish_command(m,"citizen",bot.send_message(cid,"🏡 Ты стал гражданином этого чата."),ttl=10)
        if t_lower in ("все граждане","кто гражданин","кто граждане"):
            profiles=db_get("user_profiles",{}); rows=[]
            for suid,p in profiles.items():
                if p.get("citizen"): rows.append(f"• {get_user_mention(user_id=int(suid),first_name=db_get('users_data',{}).get(suid,{}).get('name','Участник'))}")
            return finish_command(m,"citizens",bot.send_message(cid,"🏡 <b>ГРАЖДАНЕ ЧАТА</b>\n\n"+("\n".join(rows) if rows else "Пока никто не отметил гражданство."),parse_mode="HTML"),ttl=60)
        if t_lower in ("!ид","ид","!id","id") or t_lower.startswith("!ид "):
            target_uid,target_name,_=extract_target_and_args(m,parts)
            target_uid=target_uid or uid
            return finish_command(m,"user_id",bot.send_message(cid,f"🆔 ID пользователя: <code>{target_uid}</code>",parse_mode="HTML"),ttl=20)
        if t_lower.startswith("!рег ") or t_lower.startswith("рег ") or t_lower=="регистрация":
            target_uid,target_name,_=extract_target_and_args(m,parts); target_uid=target_uid or uid
            u=db_get("users_data",{}).get(str(target_uid),{}); ts=u.get("first_seen")
            txt="ℹ️ Пользователь ещё не зарегистрирован в статистике." if not ts else f"📅 Впервые замечен: <b>{datetime.fromtimestamp(ts,KYIV_TZ).strftime('%d.%m.%Y %H:%M')}</b>"
            return finish_command(m,"registration",bot.send_message(cid,txt,parse_mode="HTML"),ttl=30)

        # --- ОБЩИЕ КОМАНДЫ ---
        if t_lower == "мой профиль":
            finish_command(m, "my_profile")
            return handle_profile_request(m, uid, m.from_user)
            
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
                                    t_btn, val = trim_btn_text(t_btn.strip()), val.strip()
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
                    return bot.reply_to(m, "✅ Кнопки сохранены!", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))

                elif action == "text":
                    with state_lock:
                        data = db_get("autopost", {"posts": []})
                        for p in data["posts"]:
                            if p["id"] == pid: p["text"] = t
                        db_set("autopost", data)
                    return bot.reply_to(m, "✅ Текст поста сохранён!", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))

                elif action == "interval":
                    sec = parse_interval_input(t)
                    if sec is not None:
                        with state_lock:
                            data = db_get("autopost", {"posts": []})
                            for p in data["posts"]:
                                if p["id"] == pid: p["interval"], p["daily_time"] = sec, None
                            db_set("autopost", data)
                        return bot.reply_to(m, "✅ Интервал сохранён!", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
                    return bot.reply_to(m, "⚠️ Не понимаю такой формат. Пример: 30м, 2ч, 1д")

                elif action == "time":
                    if re.match(r'^\d{2}:\d{2}$', t):
                        with state_lock:
                            data = db_get("autopost", {"posts": []})
                            for p in data["posts"]:
                                if p["id"] == pid: p["daily_time"] = t
                            db_set("autopost", data)
                        return bot.reply_to(m, "✅ Время сохранено!", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
                    return bot.reply_to(m, "⚠️ Нужен формат ЧЧ:ММ, например: 12:00")

                elif action == "date":
                    if re.match(r'^\d{4}-\d{2}-\d{2}$', t):
                        with state_lock:
                            data = db_get("autopost", {"posts": []})
                            for p in data["posts"]:
                                if p["id"] == pid: p["start_date"] = t
                            db_set("autopost", data)
                        return bot.reply_to(m, "✅ Дата старта сохранена!", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
                    return bot.reply_to(m, "⚠️ Нужен формат ГГГГ-ММ-ДД, например: 2026-10-01")

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
            executor.submit(process_word_guess, cid, uid, m.from_user.first_name, t, m.message_id)

        if m.chat.type in ['group', 'supergroup'] and not is_words_active and not is_safe_active and not t.startswith('/'):
            executor.submit(maybe_react_randomly, m)

        if m.chat.type in ['group', 'supergroup'] and get_admin_rank(cid, uid) < 5:
            if m.content_type in ('sticker', 'animation'): run_triggers(m,'стикеры')
            if m.text:
                letters=[c for c in m.text if c.isalpha()]
                if len(letters)>=int(get_v(cid,'caps_min_length',5)) and sum(1 for c in letters if c.isupper())/max(1,len(letters))*100>=float(get_v(cid,'caps_percent',80)): run_triggers(m,'капс')
                if re.search(r'(https?://|t\.me/|telegram\.me/|www\.)',m.text,re.I): run_triggers(m,'ссылки')
            has_bad_word = any(bad_word in t_lower for bad_word in MUTES)
            if has_bad_word: run_triggers(m,'маты')
            if has_bad_word:
                try: bot.delete_message(cid, m.message_id)
                except: pass
                issue_warn(cid, m.chat.title, uid, fname, BOT_ID, "Автомодератор", "Автомодерация: нецензурная лексика", None)
                return

            # Не отправляем каждое сообщение в LLM: это было главным источником
            # очереди и задержек. Сначала дешёвая эвристика, затем AI только при подозрении.
            threat_text = (m.text or "").lower()
            threat_gate = any(x in threat_text for x in (
                "докс", "доксинг", "сват", "сватинг", "адрес", "слив",
                "найду тебя", "найду где живешь", "найду где живёшь",
                "убью", "приеду к тебе", "взломаю", "разнесу", "застрелю"
            ))
            if threat_gate:
                def threat_check():
                    try:
                        if is_threat(m.text):
                            until = int(time.time()) + 300
                            bot.restrict_chat_member(cid, uid, until_date=until, permissions=ChatPermissions(can_send_messages=False))
                            mention = get_user_mention(m.from_user)
                            bosses_ping = ""
                            alert_msg = f"{bosses_ping} 🛡 Обнаружена угроза от {mention}. Пользователь автоматически заглушен на 5 минут для проверки."
                            bot.send_message(cid, alert_msg, parse_mode='HTML')
                    except Exception as e: logging.error(f"[THREAT ACTION] {e}", exc_info=True)
                ai_executor.submit(threat_check)

        direct = m.chat.type == 'private' or (m.reply_to_message and m.reply_to_message.from_user.id == BOT_ID) or "лиза" in t_lower or f"@{BOT_USER}" in t_lower
        conflict_hit = any(w in t_lower for w in CONFL)

        if direct or conflict_hit:
            with state_lock: cities_running = cid in active_word_games
            if cities_running: return  
            if not get_v(cid, "intervene", True) or random.randint(1, 100) > get_v(cid, "freq", 40): return
            prompt = f"В чате ссора: {m.reply_to_message.text} -> {m.text}. Резюме:" if (conflict_hit and m.reply_to_message) else m.text
            def ai_task():
                try:
                    bot.send_chat_action(cid, 'typing')
                    ans = call_ai([{"role": "system", "content": SYS_PROMPT}, {"role": "user", "content": prompt}])
                    if m.chat.type == 'private': bot.send_message(cid, ans, parse_mode='HTML')
                    else: bot.send_message(cid, f"{get_user_mention(m.from_user)}, {ans}", parse_mode='HTML')
                except Exception as e:
                    logging.error(f"[AI TASK] {e}", exc_info=True)
            ai_executor.submit(ai_task)

        elif (m.chat.type in ['group', 'supergroup'] and not is_words_active and not is_safe_active
              and not t.startswith('/') and get_v(cid, "butt_in", False)
              and random.randint(1, 100) <= get_v(cid, "butt_in_chance", 15)):
            def butt_in_task():
                try:
                    bot.send_chat_action(cid, 'typing')
                    prompt = (
                        f"В чате кто-то написал сообщение: '{m.text}'. Тебя никто не звал и не упоминал. "
                        "Просто сама реши встрять в разговор одной короткой живой репликой по теме сообщения, "
                        "как будто случайно зацепилась взглядом за эту фразу в общем чате:"
                    )
                    ans = call_ai([{"role": "system", "content": SYS_PROMPT}, {"role": "user", "content": prompt}])
                    bot.send_message(cid, f"{get_user_mention(m.from_user)}, {ans}", parse_mode='HTML')
                except Exception as e:
                    logging.error(f"[BUTT IN TASK] {e}", exc_info=True)
            ai_executor.submit(butt_in_task)
    except Exception as e:
        logging.error(f"[TEXT HANDLER] {e}", exc_info=True)


# ============================================================
# Telegram integration: реальные права/заявки/проверка участников
# ============================================================
def _telegram_admin_command(m, t_lower, t):
    cid, uid = m.chat.id, m.from_user.id
    if m.chat.type not in ('group','supergroup'):
        return None
    if not (t_lower.startswith(('тг админ', '+тг админ', '-тг админ', 'тг права', 'тг разрешения чата',
                                'проверить в чате', '+автозаявки', '-автозаявки'))):
        return None

    if t_lower in ('тг права', 'тг разрешения чата'):
        try:
            me = bot.get_chat_member(cid, BOT_ID)
            p = getattr(me, 'can_manage_chat', None)
            lines = [f"🤖 <b>Права Лизы</b>", f"Статус: <b>{html.escape(str(me.status))}</b>"]
            if me.status in ('administrator','creator'):
                for attr, label in (
                    ('can_delete_messages','Удаление сообщений'), ('can_restrict_members','Блокировка участников'),
                    ('can_pin_messages','Закрепление'), ('can_invite_users','Приглашение пользователей'),
                    ('can_manage_chat','Управление чатом'), ('can_promote_members','Назначение админов')):
                    val = getattr(me, attr, None)
                    if val is not None: lines.append(f"{'✅' if val else '❌'} {label}")
            else:
                lines.append('❌ Лиза не является администратором.')
            return finish_command(m,'tg_perms',bot.send_message(cid,'\n'.join(lines),parse_mode='HTML'),ttl=30)
        except Exception as e:
            logging.error(f'[TG PERMS] {e}', exc_info=True)
            return finish_command(m,'tg_perms_err',bot.send_message(cid,'⚠️ Не удалось получить права Лизы.'),ttl=15)

    if t_lower.startswith('проверить в чате'):
        parts=t.split(); target_uid,target_name,_=extract_target_and_args(m,parts)
        if not target_uid:
            return finish_command(m,'check_chat_err',bot.send_message(cid,'⚠️ Используй <code>Проверить в чате @user</code> или ответом на сообщение.',parse_mode='HTML'),ttl=10)
        try:
            member=bot.get_chat_member(cid,target_uid)
            status_map={'creator':'создатель','administrator':'администратор','member':'участник','restricted':'ограничен','left':'вышел','kicked':'заблокирован'}
            status=status_map.get(member.status,member.status)
            extra=[]
            if member.status=='administrator':
                for attr,label in (('can_delete_messages','удаление'),('can_restrict_members','ограничение'),('can_pin_messages','закрепление'),('can_invite_users','приглашения'),('can_promote_members','админы')):
                    if getattr(member,attr,None): extra.append(label)
            text=f"🔎 {get_user_mention(user_id=target_uid,first_name=target_name)}\nСтатус: <b>{status}</b>"
            if extra: text += '\nПрава: ' + ', '.join(extra)
            return finish_command(m,'check_chat',bot.send_message(cid,text,parse_mode='HTML'),ttl=30)
        except Exception:
            return finish_command(m,'check_chat_err',bot.send_message(cid,'⚠️ Пользователь не найден в этом чате.'),ttl=15)

    if t_lower.startswith(('+автозаявки','-автозаявки')):
        if not _cleanup_dk_allowed(cid,uid,'настройки'): return reply_no_rights(m)
        enabled=t_lower.startswith('+')
        set_chat_setting(cid,'auto_join_requests',enabled)
        return finish_command(m,'auto_requests',bot.send_message(cid, f"📥 Автозаявки {'включены' if enabled else 'выключены'}.",parse_mode='HTML'),ttl=15)

    if t_lower.startswith(('+тг админ','тг админ','-тг админ')):
        if not _cleanup_dk_allowed(cid,uid,'настройки'): return reply_no_rights(m)
        if not has_permission(cid,uid,'can_promote'): return reply_no_rights(m)
        parts=t.split(); target_uid,target_name,_=extract_target_and_args(m,parts)
        if not target_uid:
            return finish_command(m,'tg_admin_err',bot.send_message(cid,'⚠️ Укажи пользователя через @username, ID или reply.',parse_mode='HTML'),ttl=10)
        if target_uid == BOT_ID:
            return finish_command(m,'tg_admin_err',bot.send_message(cid,'⚠️ Права самой Лизы меняются только через Telegram.'),ttl=10)
        promote=not t_lower.startswith('-')
        try:
            if promote:
                bot.promote_chat_member(cid,target_uid,can_manage_chat=False,can_delete_messages=True,can_restrict_members=True,can_invite_users=True,can_pin_messages=True,can_manage_topics=True)
                msg=f"👮 {get_user_mention(user_id=target_uid,first_name=target_name)} назначен администратором Telegram."
            else:
                bot.promote_chat_member(cid,target_uid,can_manage_chat=False,can_delete_messages=False,can_restrict_members=False,can_invite_users=False,can_pin_messages=False,can_manage_topics=False)
                msg=f"🧹 Права администратора Telegram у {get_user_mention(user_id=target_uid,first_name=target_name)} сняты."
            return finish_command(m,'tg_admin',bot.send_message(cid,msg,parse_mode='HTML'),ttl=20)
        except Exception as e:
            logging.error(f'[TG ADMIN] {e}', exc_info=True)
            return finish_command(m,'tg_admin_err',bot.send_message(cid,'⚠️ Telegram не разрешил изменить права. Нужны соответствующие права Лизы.'),ttl=20)
    return None


def handle_chat_join_request(r):
    try:
        cid = r.chat.id
        if not get_v(cid,'auto_join_requests',False): return
        # Автопринятие только заявок, пришедших в чат, где настройка включена.
        bot.approve_chat_join_request(cid, r.from_user.id)
        logging.info(f'[JOIN REQUEST] approved user={r.from_user.id} chat={cid}')
    except Exception as e:
        logging.error(f'[JOIN REQUEST] {e}', exc_info=True)


# ============================================================
# Регистрация обработчиков.
# Без этого блока бот получает апдейты от Telegram, но НИКАК
# на них не реагирует — ни одна функция выше не была подключена
# к боту (текстовые сообщения, кнопки, команды, вход/выход
# участников). Судя по всему, это и было потеряно при рефакторинге.
#
# Названия слэш-команд для cmd_sgs/cmd_lsg/cmd_start_words и т.д.
# восстановлены по внутренним меткам finish_command(...) и по
# текстам самих сообщений (например "/start_words_game",
# "/leaders_safe_game"). Для "сейфа" точное название команды в
# коде нигде прямо не упоминается — заведено сразу под 3
# вероятных варианта, лишние просто никогда не сработают.
# ============================================================

_EXPLICIT_COMMANDS = [
    "start", "settings", "lucky_game", "leaders_lucky_game", "events",
    "start_game_safe", "start_safe_game", "safe_game", "leaders_safe_game",
    "start_words_game", "stop_words_game", "leaders_words_game", "words_status",
]

def _is_explicit_command(m):
    """True если сообщение — одна из команд, для которых зарегистрирован
    отдельный обработчик ниже (нужно, чтобы text_handler не обрабатывал
    их повторно)."""
    if not m.text or not m.text.startswith("/"):
        return False
    cmd = m.text.split()[0][1:].split("@")[0].lower()
    return cmd in _EXPLICIT_COMMANDS

# Системные сообщения (вход/выход участников) и фото
bot.register_message_handler(handle_system_messages, content_types=["new_chat_members", "left_chat_member"])
try:
    bot.register_chat_join_request_handler(handle_chat_join_request)
except AttributeError:
    logging.warning("[JOIN REQUEST] Handler registration unavailable in installed pyTelegramBotAPI")
bot.register_message_handler(on_photo, content_types=["photo"])

# Явные слэш-команды
bot.register_message_handler(cmd_start, commands=["start"])
bot.register_message_handler(cmd_settings, commands=["settings"])
bot.register_message_handler(cmd_lg, commands=["lucky_game"])
bot.register_message_handler(cmd_llg, commands=["leaders_lucky_game"])
bot.register_message_handler(cmd_events, commands=["events"])
bot.register_message_handler(cmd_sgs, commands=["start_game_safe", "start_safe_game", "safe_game"])
bot.register_message_handler(cmd_lsg, commands=["leaders_safe_game"])
bot.register_message_handler(cmd_start_words, commands=["start_words_game"])
bot.register_message_handler(cmd_stop_words, commands=["stop_words_game"])
bot.register_message_handler(cmd_leaders_words, commands=["leaders_words_game"])
bot.register_message_handler(cmd_words_status, commands=["words_status"])

# Весь остальной текст (включая "кто админ", "+варн", "/ban", "фарм" и т.д.,
# они разбираются внутри самого text_handler по тексту сообщения)
bot.register_message_handler(text_handler, content_types=["text"], func=lambda m: not _is_explicit_command(m))

# Инлайн-кнопки
bot.register_callback_query_handler(cb_handler, func=lambda c: True)
