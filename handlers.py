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
                _record_join(m.chat.id, new_user)
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
        if m.from_user.id not in BOSSES:
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
    if caller_rank < 1 and m.from_user.id not in BOSSES: return reply_no_rights(m)
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

def cmd_farm(m):
    if not check_access(m): return
    process_farm_command(m)

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
                "🔹 Фарм травки: /farm\n"
                "🔹 Иногда сама встреваю в чат и ставлю реакции 🔥"
            )
            return bot.edit_message_text(txt_can_do, cid, c.message.message_id, parse_mode='HTML', reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data="back_to_start")))
            
        if d == "back_to_start":
            bot.answer_callback_query(c.id)
            kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Что ты умеешь?", callback_data="what_can_i_do"))
            if uid in BOSSES: kb.add(types.InlineKeyboardButton("⚙️ Настройки", callback_data="open_main_settings"))
            back_txt = (
                f"👋 Привет, {get_user_mention(c.from_user)}!\n"
                "Я <b>Лиза</b> 🌿 — слежу за порядком и веселю чат.\n\n"
                "💭 Наш чат: https://t.me/+8WZ4kwpAaZ0yZTRi\n"
                "🛒 Наш бот: @vibe_247top_bot"
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
            if (is_pv and uid not in BOSSES) or (not is_pv and get_admin_rank(cid, uid) < 5):
                return bot.answer_callback_query(c.id, "⛔ Только создатель чата может менять базовые настройки.", show_alert=True)
            bot.answer_callback_query(c.id)
            return bot.edit_message_text("🎛 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ЛИЗОЙ</b> ✨\n\nНастрой характер и функции бота под свой чат:", cid, c.message.message_id, reply_markup=main_kb(cid, is_pv), parse_mode='HTML')

        if d.startswith("m:") or action == "s":
            if (is_pv and uid not in BOSSES) or (not is_pv and get_admin_rank(cid, uid) < 5):
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

        if d == "to_group_settings" and uid in BOSSES:
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
                bot.reply_to(m, "✅ Фото сохранено!", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:select:{pid}")))
        record_xp_and_stats(m)
    except Exception as e: logging.error(f"[ON PHOTO] {e}", exc_info=True)

def text_handler(m):
    try:
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
        boss = uid in BOSSES

        # Track activity even for commands; this is the basis for cleanup tools.
        if m.chat.type in ["group", "supergroup"] and not m.from_user.is_bot:
            try: _record_join(cid, m.from_user)
            except Exception: pass

        t_lower = t.lower()
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
        if t_lower in ["!закреп", "закреп", "!пин", "пин", "/pin"]:
            if not _cleanup_dk_allowed(cid, uid, "закреп"): return reply_no_rights(m)
            target = m.reply_to_message
            if not target: return finish_command(m, "pin_err", bot.send_message(cid, "⚠️ Используй команду ответом на сообщение."), ttl=10)
            try:
                bot.pin_chat_message(cid, target.message_id, disable_notification=True)
                return finish_command(m, "pin", bot.send_message(cid, "📌 <b>VIBE-ПИН</b>\nСообщение закреплено.", parse_mode="HTML"), ttl=10)
            except Exception as e:
                logging.error(f"[PIN] {e}")
                return finish_command(m, "pin_err", bot.send_message(cid, "⚠️ Не удалось закрепить сообщение. Проверь права бота."), ttl=15)
        if t_lower in ["!открепить", "открепить", "!анпин", "анпин", "/unpin"]:
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
            if not boss and get_admin_rank(cid, uid) < 5:
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
            if not boss and get_admin_rank(cid, uid) < 5:
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
                if not boss and (caller_rank <= rank or not has_permission(cid, uid, "can_promote")):
                    return reply_no_rights(m)
                if target_uid == BOT_ID or (not boss and get_admin_rank(cid, target_uid) >= caller_rank):
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
                        dur_secs = 0
                        time_str = "навсегда"
                        
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
                    reason = " ".join(args) if args else "Не указана"
                    try:
                        bot.ban_chat_member(cid, target_uid, until_date=0)
                        bot.unban_chat_member(cid, target_uid, only_if_banned=True)
                        mention_admin = get_user_mention(user_id=uid, first_name=fname)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = format_moderation_message("kick", mention_admin, mention_target, reason=reason)
                        log_moderation_action(m.chat.title or str(cid), txt)
                        save_moderation_record(cid, "kick", target_uid, target_name, uid, fname, reason, 0)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "kick", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["мут", "/mute"]:
                    dur_secs, parsed, consumed = parse_duration_from_args(args)
                    if parsed:
                        reason = " ".join(args[consumed:]) if len(args) > consumed else "Не указана"
                        time_str = format_seconds_human(dur_secs) if dur_secs > 0 else "навсегда"
                    else:
                        reason = " ".join(args) if args else "Не указана"
                        dur_secs = 0
                        time_str = "навсегда"
                        
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

        # --- ОБЩИЕ КОМАНДЫ ---
        if t_lower == "мой профиль":
            finish_command(m, "my_profile")
            return handle_profile_request(m, uid, m.from_user)
            
        if t_lower in ["фарм", "/farm", "/фарм"]:
            return process_farm_command(m)

        if t_lower.rstrip("!.?") in SMOKE_WEED_TRIGGERS:
            return process_smoke_weed(m)

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

        if m.chat.type in ['group', 'supergroup'] and not boss:
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
                            bosses_ping = " ".join([f"<a href='tg://user?id={b}'>⚠️</a>" for b in BOSSES])
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
