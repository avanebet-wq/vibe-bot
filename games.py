# -*- coding: utf-8 -*-
"""VIBE Bot — games module."""
from runtime import *
from ai import *
from core import *
from general import *

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
    for pinned_id in game.get("pinned_message_ids", []) or []:
        try: bot.unpin_chat_message(cid, pinned_id)
        except Exception as e:
            if "message to unpin not found" not in str(e) and "message to be unpinned not found" not in str(e): pass

    sb_id = game.get("scoreboard_msg_id")
    if sb_id:
        try: bot.delete_message(cid, sb_id)
        except Exception as e:
            if "message to delete not found" not in str(e): pass

    scoreboard = build_active_scoreboard(game)
    if reason == "limit": txt = f"🏁 <b>ИГРА «СЛОВА» ЗАВЕРШЕНА!</b> 🎉\nУра! Вы совместно назвали 50 слов!\n\n🏆<b>УЧАСТНИКИ И БАЛЫ:</b>\n{scoreboard}"
    else: txt = f"⌛ <b>ИГРА «СЛОВА» ОКОНЧЕНА ПО ТАЙМ-АУТУ.</b>\nНазвано слов: {game['moves']}.\n\n🏆<b>УЧАСТНИКИ И БАЛЫ:</b>\n{scoreboard}\n\n/start_words_game — начать заново"
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
                    if elapsed >= WORD_HINT_DELAY_SECONDS and not game.get("hint_given"):
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

def build_lobby_text(lobby):
    remaining = max(0, int(lobby["end_time"] - time.time()))
    mins, secs = remaining // 60, remaining % 60
    time_str = f"{mins}м {secs}с"
    players = lobby["players"]
    plist = "\n".join(f"• <b>{get_user_mention(user_id=u, first_name=n)}</b>" for u, n in players.items()) or "Пока никто не записался."
    return (
        "<b>💜━━━━━━━━VIBE━━━━━━━━💜\n"
        "🌿 ИГРА «СЛОВА» 🌟</b>\n\n"
        "<b>📖 ПРАВИЛА ИГРЫ:</b>\n\n"
        "По очереди называем слова на <b>русском</b> языке. \n"
        "Новое слово должно начинаться на <b>последнюю</b> букву <b>предыдущего</b> слова.\n\n"
        "🚫 Повторять слова <b>нельзя!</b>\n"
        "⚡ За каждое правильное слово — <b>+1 балл</b>.\n\n"
        f"⏳ До старта: <b>{time_str}</b>\n"
        f"👥 Участники ({len(players)}):\n{plist}\n\n"
        "🔥 БЫСТРЕЕ ЖМИ КНОПКУ <b>ЗАПИСАТЬСЯ</b> И УЧАСТВУЙ В ИГРЕ!🥳\n"
        "<b>💜━━━━━━━━VIBE━━━━━━━━💜</b>"
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

    if outcome == "closed": bot.send_message(m.chat.id, "⏳ Регистрация уже закрыта.")
    elif outcome == "already":
        bot.send_message(
            m.chat.id,
            f"<b>🍀 {fname}, ты уже в списке участников! Проверь чат.🤫\n\n"
            "Теперь просто дождись начала игры и приготовься побороться за первое место! 🏆🔥\n\n"
            "⏳ Скоро начинаем — удачи и побольше быстрых ответов! 😎</b>",
            parse_mode='HTML'
        )
    elif outcome == "registered":
        bot.send_message(
            m.chat.id,
            f"<b>✅ ЗАПИСАЛА ТЕБЯ, {fname}! ☺️\n\n"
            "⏳ ЖДИ НАЧАЛА ИГРЫ И — ПОБЕЖДАЙ! 🍀🔥</b>",
            parse_mode='HTML'
        )
        repost_lobby(cid)
    elif outcome == "removed":
        bot.send_message(
            m.chat.id,
            f"<b>{fname} ❌ ВЫЧЕРКНУЛА ТЕБЯ ИЗ СПИСКА УЧАСТНИКОВ!\n\n"
            "Если передумаешь — сможешь зарегистрироваться снова, пока набор участников открыт. 🍀</b>",
            parse_mode='HTML'
        )
        repost_lobby(cid)
    elif outcome == "not_in": bot.send_message(m.chat.id, "🤷‍♀️ Ты и не был(а) записан(а) на игру.")

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

    if len(players) < MIN_WORD_PLAYERS:
        try: bot.send_message(cid, f"😔 Записалось меньше {MIN_WORD_PLAYERS} участников — игра отменена.\n/start_words_game — попробовать снова")
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
            "scoreboard_msg_id": None, "pinned_message_ids": [reg_msg_id] if reg_msg_id else [],
        }

    plist = "\n".join(f"• {get_user_mention(user_id=u, first_name=n)}" for u, n in players.items())
    hint_mins = WORD_HINT_DELAY_SECONDS // 60
    try:
        bot.send_message(
            cid,
            "<b>💜━━━━━━━━ VIBE ━━━━━━━━💜</b>\n\n"
            "<b>🔤 ИГРА «СЛОВА» НАЧАЛАСЬ! 🔥</b>\n\n"
            f"👥 <b>УЧАСТНИКИ:</b>\n{plist}\n\n"
            f"🎯 <b>ПЕРВОЕ СЛОВО:</b>\n«<b>{html.escape(seed)}</b>»\n\n"
            f"➡️ <b>СЛЕДУЮЩЕЕ СЛОВО ДОЛЖНО НАЧИНАТЬСЯ НА БУКВУ:</b>\n«<b>{eff.upper()}</b>»\n\n"
            f"⏱ <b>ПЕРВАЯ ПОДСКАЗКА ЧЕРЕЗ:</b> <b>{hint_mins} МИНУТЫ</b>\n\n"
            "🔇 <b>ПОКА ИДЁТ ИГРА — ЛИЗА НЕ ВМЕШИВАЕТСЯ В ЧАТ.</b>\n"
            "Можно спокойно играть и отправлять свои ответы без лишних сообщений. 😎\n\n"
            "🔥 ВРЕМЯ ПОКАЗАТЬ, КТО ЗДЕСЬ САМЫЙ БЫСТРЫЙ!\n\n"
            "<b>💜━━━━━━━━ VIBE ━━━━━━━━💜</b>",
            parse_mode='HTML'
        )
    except Exception as e: logging.error(f"[WORDS START] {e}", exc_info=True)

def build_active_scoreboard(game):
    scores = game.get("scores", {})
    players = game.get("players", {})
    if not scores: return "Пока нет очков."
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    lines = [f"{i}. {get_user_mention(user_id=uid, first_name=players.get(uid, 'Игрок'))} — <b>{pts}</b> б." for i, (uid, pts) in enumerate(ordered, 1)]
    return "\n".join(lines)

def process_word_guess(cid, uid, fname, raw_text, message_id=None):
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
                msg = bot.send_message(cid, f"♻️ «{html.escape(raw)}» уже называли. Попробуй другое слово!")
                auto_del(msg, 10)
                return

        if not resolve_is_word(raw, norm):
            if req_letter:
                msg = bot.send_message(cid, f"🤔 Не нахожу такого слова. Попробуй ещё раз, на букву «{req_letter.upper()}».")
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

        if message_id:
            react_with_emoji(cid, message_id, "🎉")

        if moves_count >= 50:
            with state_lock:
                if cid in active_word_games: game_obj = active_word_games.pop(cid)
            end_word_game(cid, game_obj, reason="limit")
            return

        with state_lock:
            game_now = active_word_games.get(cid)
            old_sb_id = game_now.get("scoreboard_msg_id") if game_now else None

        if old_sb_id:
            try: bot.delete_message(cid, old_sb_id)
            except Exception as e:
                if "message to delete not found" not in str(e): logging.error(f"[WORDS SCOREBOARD DEL] {e}")

        if game_now:
            try:
                sb_msg = bot.send_message(cid, f"📊 <b>Актуальный счёт игроков:</b>\n{build_active_scoreboard(game_now)}", parse_mode='HTML')
                with state_lock:
                    if cid in active_word_games: active_word_games[cid]["scoreboard_msg_id"] = sb_msg.message_id
            except Exception as e: logging.error(f"[WORDS SCOREBOARD SEND] {e}", exc_info=True)

        mention = get_user_mention(user_id=uid, first_name=fname)
        bot.send_message(cid, f"🥳 {mention} угадал(а) слово «<b>{html.escape(raw)}</b>» и получает +1 балл!🔥\nСледующее слово на букву «<b>{eff_letter.upper()}</b>»", parse_mode='HTML')
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
            txt = f"🎉 {mention}, невероятно! Ты выиграл(а) +1 балл!\n🏆 Твоё место в рейтинге: #{rank}\n"
        else:
            txt = f"😔 {mention}, не повезло. Попробуй ещё раз!\n"
        
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🎲 Снова", callback_data=f"lucky:again:{uid}")) if left > 0 else None
        if left == 0:
            with state_lock: rem = int(lucky_limits[uid]["reset_at"] - time.time())
            txt += f"\n😔 Попытки закончились.\n⏳ Новые через: {max(0, rem//60)}м {max(0, rem%60)}с.\n\n😉 А в нашем боте можно играть без ограничений!\n🔥 @vibe_247top_bot"
        else:
            txt += f"🎲 Осталось попыток: {left}\nСыграем ещё?"
            
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
                    txt = f"😔 {get_user_mention(user_id=uid, first_name=fname)}, попытки закончились.\n⏳ Новые через: {rem//60}м {rem%60}с.\n\n😉 А в нашем боте можно играть без ограничений!\n🔥 @vibe_247top_bot"
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
            bot.send_message(cid, "⚠️ У меня нет прав отправлять кубики в этом чате.")
            with state_lock:
                if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))
            return
        win = (emoji in ["🎯", "🎳"] and dice.dice.value == 6) or (emoji == "🏀" and dice.dice.value in [4, 5])
        executor.submit(lucky_game_result, cid, uid, fname, dice.message_id, win, lim["left"], emoji)
    except Exception as e:
        logging.error(f"[PLAY LUCKY] {e}", exc_info=True)
        with state_lock:
            if (cid, uid) in active_lucky_players: active_lucky_players.remove((cid, uid))
