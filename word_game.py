# -*- coding: utf-8 -*-
"""Игра «Слова»: запись, ручной запуск, круговые ходы и финальные призы."""
from __future__ import annotations

import html
import logging
import random
import threading
import time
from datetime import datetime, timedelta

from telebot import types

from database import db_get, db_update_json
from runtime import bot
from utils import is_chat_admin
import settings_store as store

LOG = logging.getLogger("word_game")
KEY = "word_game_sessions"
_LOCK = threading.RLock()

# Большой базовый набор распространённых украинских слов. Игра не обращается к ИИ:
# слово всегда выбирается только из этого локального словаря.
WORDS = [
    "абрикос", "автобус", "адреса", "акація", "акула", "аптека", "аркуш", "банан",
    "батько", "бджола", "берег", "береза", "бібліотека", "блискавка", "болото", "будинок",
    "булка", "буряк", "ваза", "весна", "вечір", "вікно", "вишня", "вогонь", "вода", "ворота",
    "вулиця", "газета", "глечик", "глобус", "груша", "гурт", "двері", "дерево", "джерело", "диван",
    "дитина", "дощ", "дорога", "дружба", "дуб", "екран", "завдання", "зайчик", "замок", "заняття",
    "зірка", "зошит", "іскра", "історія", "кава", "кактус", "камінь", "канікули", "капелюх", "картина",
    "каша", "квітка", "килим", "кишеня", "книга", "кіт", "кіно", "кішка", "ключ", "колесо", "колір",
    "команда", "компот", "кордон", "корова", "космос", "краватка", "криниця", "кролик", "крила", "кухня",
    "лампа", "лисиця", "листок", "лікар", "ліс", "літак", "літо", "ложка", "магазин", "майдан", "майстер",
    "малина", "мед", "медаль", "метелик", "місяць", "місто", "молоко", "морква", "море", "музика", "мураха",
    "навчання", "надія", "народ", "небо", "ніч", "ножиці", "область", "озеро", "океан", "олівець", "осінь",
    "папір", "парк", "парта", "пензель", "пісня", "півень", "підлога", "погода", "подорож", "полуниця",
    "пошта", "праця", "птах", "пшениця", "пустеля", "ранок", "райдуга", "радіо", "ракета", "річка", "родина",
    "рослина", "сад", "салат", "світло", "село", "серце", "сестра", "сніг", "сонце", "сорочка", "сосна",
    "список", "спорт", "стіл", "сторінка", "струмок", "сумка", "театр", "телефон", "тиждень", "тінь", "трава",
    "трамвай", "троянда", "хмара", "хліб", "хлопець", "холодильник", "чай", "черепаха", "човен", "час", "чашка",
    "червень", "чорниця", "шапка", "школа", "шлях", "яблуко", "ялинка", "яскравість", "яйце", "яструб",
    "актор", "апарат", "армія", "басейн", "бібліотекар", "біг", "борщ", "бризки", "буква", "вагон", "варення",
    "веселка", "вітрина", "вогнище", "вчитель", "грати", "грім", "гриб", "десерт", "дощовик", "долина", "досвід",
    "друкарка", "журнал", "жук", "завод", "змагання", "золото", "кабінет", "календар", "капуста", "карта",
    "картопля", "карусель", "клас", "книга", "коробка", "корона", "лавка", "лебідь", "лимон", "машина", "меблі",
    "мелодія", "міст", "молоток", "морозиво", "наука", "обкладинка", "океан", "опера", "організація", "освіта",
    "палац", "папуга", "паровоз", "перемога", "пиріг", "планета", "плеєр", "подарунок", "помідор", "попкорн",
    "потяг", "прапор", "пригоди", "природа", "професія", "профіль", "пружина", "пташка", "пузир", "робота",
    "ромашка", "рукавичка", "самокат", "свято", "скриня", "скрипка", "сніданок", "сокіл", "сокира", "спогад",
    "станція", "стежка", "стілець", "телевізор", "торт", "трактор", "турбота", "учень", "учитель", "футбол",
    "хвиля", "хмаринка", "цукерка", "цукор", "чарівник", "черевики", "черешня", "шоколад", "штурвал", "щастя",
]
WORDS = list(dict.fromkeys(WORDS))


def _store():
    return db_get(KEY, {}) or {}


def _get(gid):
    return _store().get(str(gid))


def is_active(gid):
    session = _get(gid)
    return bool(session and session.get("phase") in {"scheduled", "registration", "waiting", "playing"})


def _esc(v):
    return html.escape(str(v or ""), quote=False)


def _mention(p):
    uid = p.get("id")
    username = p.get("username")
    if username:
        return "@" + _esc(username)
    name = p.get("name") or "учасник"
    return f'<a href="tg://user?id={uid}">{_esc(name)}</a>'


def _participant_list(session):
    return list((session.get("participants") or {}).values())


def _registration_text(session):
    cfg = session.get("config") or {}
    lines = [_esc(cfg.get("text") or "🎯 Гра «Слова»"), "", "👥 <b>Учасники:</b>"]
    participants = _participant_list(session)
    if participants:
        for i, p in enumerate(participants, 1):
            lines.append(f"{i}. {_mention(p)}")
    else:
        lines.append("Поки ніхто не записався.")
    max_p = int(cfg.get("max_participants", 10) or 10)
    lines += ["", f"Місць: <b>{len(participants)}/{max_p}</b>"]
    if session.get("phase") == "registration":
        lines += ["", "Натисніть «Записатися», щоб взяти участь."]
    elif session.get("phase") == "waiting":
        lines += ["", "<b>Запис закінчено. Очікуйте початку гри!</b>"]
    return "\n".join(lines)


def _registration_kb(gid, session):
    rows = []
    if session.get("phase") == "registration":
        rows.append([types.InlineKeyboardButton("📝 Записатися", callback_data=f"wgame|register|{gid}")])
    rows.append([types.InlineKeyboardButton("▶️ Почати гру", callback_data=f"wgame|start|{gid}")])
    kb = types.InlineKeyboardMarkup(row_width=1)
    for row in rows:
        kb.row(*row)
    return kb


def _game_status_text(session):
    participants = _participant_list(session)
    scores = session.get("scores") or {}
    solved = int(session.get("solved_words", 0) or 0)
    total = int((session.get("config") or {}).get("total_words", 20) or 20)
    lines = ["🎯 <b>ГРА «СЛОВА»</b>", "", f"Відгадано слів: <b>{solved}/{total}</b>", "", "🏆 <b>Рахунок:</b>"]
    for p in participants:
        lines.append(f"• {_mention(p)} — <b>{int(scores.get(str(p['id']), 0))}</b>")
    return "\n".join(lines)


def _send_registration(gid, session, replace=True):
    cfg = session.get("config") or {}
    media = cfg.get("photo") or {}
    previous = session.get("last_message_id")
    markup = _registration_kb(gid, session)
    if media.get("file_id"):
        msg = bot.send_photo(gid, media["file_id"], caption=_registration_text(session), reply_markup=markup, parse_mode="HTML")
    else:
        msg = bot.send_message(gid, _registration_text(session), reply_markup=markup, parse_mode="HTML")
    mid = getattr(msg, "message_id", None)
    def mutate(data):
        current = data.get(str(gid))
        if not current or current.get("session_id") != session.get("session_id"):
            return data
        current["last_message_id"] = mid
        current["last_sent_at"] = time.time()
        current["next_republish_at"] = time.time() + int(cfg.get("interval_seconds") or 0) if cfg.get("interval_seconds") else None
        return data
    db_update_json(KEY, mutate, {})
    if replace and previous and previous != mid and cfg.get("delete_last", True):
        try: bot.delete_message(gid, previous)
        except Exception: pass
    return msg


def _refresh_registration(gid, session):
    mid = session.get("last_message_id")
    if not mid:
        return _send_registration(gid, session, replace=False)
    try:
        markup = _registration_kb(gid, session)
        cfg = session.get("config") or {}
        if (cfg.get("photo") or {}).get("file_id"):
            bot.edit_message_caption(_registration_text(session), gid, mid, reply_markup=markup, parse_mode="HTML")
        else:
            bot.edit_message_text(_registration_text(session), gid, mid, reply_markup=markup, parse_mode="HTML")
        return True
    except Exception:
        return _send_registration(gid, session, replace=True)


def _schedule_start_timestamp(raw):
    if not raw:
        return time.time()
    try:
        hh, mm = map(int, raw.split(":"))
        now = datetime.now().astimezone()
        dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt.timestamp()
    except Exception:
        return time.time()


def _choose_post(gid, args):
    raw = (args or "").strip()
    if raw and raw.split()[0].isdigit() and store.get_post(gid, raw.split()[0]):
        return raw.split()[0]
    selected = store.get_word_game_post_id(gid)
    if selected and store.get_post(gid, selected):
        return str(selected)
    posts = sorted(store.get_posts(gid).keys(), key=lambda x: int(x))
    if len(posts) == 1:
        return str(posts[0])
    return None


def cmd_register(message, args=""):
    gid = message.chat.id
    if message.chat.type not in ("group", "supergroup"):
        return bot.reply_to(message, "⚠️ Команда «запись слова» работает только в группе.")
    if not is_chat_admin(gid, message.from_user.id):
        return bot.reply_to(message, "⛔ Только администратор может открыть запись на игру.")
    if is_active(gid):
        return bot.reply_to(message, "⚠️ Запись или игра уже идут в этом чате.")
    pid = _choose_post(gid, args)
    if not pid:
        return bot.reply_to(message, "⚠️ Не удалось определить публикацию. Если их несколько, укажите номер: <code>запись слова 2</code>.")
    post = store.get_post(gid, pid) or {}
    cfg = store.get_word_game_config(gid, pid) or {}
    if not (post.get("text") or (post.get("media") or {}).get("file_id")):
        return bot.reply_to(message, "⚠️ В выбранной публикации нет текста или фото. Сначала настройте её через «Настройки слова».")
    participants = {}
    start_at = _schedule_start_timestamp(cfg.get("start_time")) if cfg.get("start_time") else time.time()
    session = {
        "session_id": f"{gid}:{time.time_ns()}", "gid": gid, "pid": str(pid), "owner_id": message.from_user.id,
        "phase": "scheduled" if start_at > time.time() else "registration", "started_at": time.time(),
        "registration_started_at": start_at if start_at <= time.time() else None, "start_at": start_at,
        "last_message_id": None, "next_republish_at": None, "participants": participants,
        "scores": {}, "solved_words": 0, "turn_index": 0, "current_word": None,
        "current_scrambled": None, "current_started_at": None, "answer_deadline": None,
        "used_words": [], "config": dict(cfg),
    }
    db_update_json(KEY, lambda data: {**data, str(gid): session}, {})
    if session["phase"] == "scheduled":
        return bot.reply_to(message, f"⏰ Запись начнётся о <b>{_esc(cfg.get('start_time'))}</b>.", parse_mode="HTML")
    _send_registration(gid, session, replace=False)
    return None


def _close_registration(gid, session, full=False):
    session = dict(session)
    session["phase"] = "waiting"
    session["registration_closed_at"] = time.time()
    def mutate(data):
        current = data.get(str(gid))
        if current and current.get("session_id") == session.get("session_id"):
            current.update(session)
        return data
    db_update_json(KEY, mutate, {})
    current = _get(gid)
    if current:
        _refresh_registration(gid, current)
    if full:
        try: bot.send_message(gid, "📝 <b>Запись окончена.</b>\n\nОжидайте начала игры!", parse_mode="HTML")
        except Exception: pass


def _register(call, gid):
    user = call.from_user
    if user.id is None:
        return bot.answer_callback_query(call.id, "Не удалось определить пользователя.", show_alert=True)
    outcome = {"ok": False, "reason": "closed", "session": None}
    def mutate(data):
        session = data.get(str(gid))
        if not session or session.get("phase") != "registration":
            outcome["reason"] = "closed"; return data
        participants = session.setdefault("participants", {})
        uid = str(user.id)
        if uid in participants:
            outcome["reason"] = "already"; return data
        max_p = int((session.get("config") or {}).get("max_participants", 10) or 10)
        if len(participants) >= max_p:
            outcome["reason"] = "full"; return data
        participants[uid] = {"id": user.id, "username": user.username, "name": user.first_name or user.username or str(user.id), "registered_at": time.time()}
        outcome["ok"] = True; outcome["session"] = session
        if len(participants) >= max_p:
            session["phase"] = "waiting"
            session["registration_closed_at"] = time.time()
            outcome["full"] = True
        return data
    db_update_json(KEY, mutate, {})
    if outcome["reason"] == "already":
        return bot.answer_callback_query(call.id, "Ты уже записан(а).", show_alert=True)
    if outcome["reason"] in {"full", "closed"}:
        return bot.answer_callback_query(call.id, "Запись уже окончена.", show_alert=True)
    bot.answer_callback_query(call.id, "✅ Ты записан(а)!")
    current = _get(gid)
    if current:
        _refresh_registration(gid, current)
        if current.get("phase") == "waiting" and outcome.get("full"):
            try: bot.send_message(gid, "📝 <b>Запись окончена.</b>\n\nОжидайте начала игры!", parse_mode="HTML")
            except Exception: pass


def _normalize_answer(text):
    import unicodedata
    value = unicodedata.normalize("NFC", (text or "").strip().lower())
    return "".join(ch for ch in value if ch.isalpha())


def _scramble(word):
    chars = list(word)
    if len(chars) < 2:
        return word
    for _ in range(20):
        random.shuffle(chars)
        candidate = "".join(chars)
        if candidate != word:
            return candidate
    return "".join(reversed(chars)) if "".join(chars) == word else "".join(chars)


def _next_word(session):
    used = set(session.get("used_words") or [])
    available = [w for w in WORDS if w not in used]
    if not available:
        available = WORDS[:]
    word = random.choice(available)
    session.setdefault("used_words", []).append(word)
    return word, _scramble(word)


def _send_turn(gid, session):
    participants = _participant_list(session)
    if not participants:
        return _finish_game(gid, session, "no_participants")
    total = int((session.get("config") or {}).get("total_words", 20) or 20)
    if int(session.get("solved_words", 0) or 0) >= total:
        return _finish_game(gid, session, "complete")
    idx = int(session.get("turn_index", 0) or 0) % len(participants)
    p = participants[idx]
    word, scrambled = _next_word(session)
    answer_minutes = int((session.get("config") or {}).get("answer_time_minutes", 1) or 1)
    now = time.time()
    text = (
        f"🎯 {_mention(p)} <b>Внимание!</b>\n\n"
        f"Отгадайте слово: <b>{_esc(scrambled)}</b>\n"
        f"Время на ответ: <b>{answer_minutes} мин.</b>"
    )
    try:
        bot.send_message(gid, text, parse_mode="HTML")
    except Exception:
        LOG.exception("failed to send word turn")
    session.update({
        "phase": "playing", "current_word": word, "current_scrambled": scrambled,
        "current_user_id": p["id"], "current_started_at": now, "answer_deadline": now + answer_minutes * 60,
    })
    db_update_json(KEY, lambda data: _mutate_current(data, gid, session), {})


def _mutate_current(data, gid, session):
    current = data.get(str(gid))
    if current and current.get("session_id") == session.get("session_id"):
        current.update(session)
    return data


def _start_game(gid, session):
    participants = _participant_list(session)
    if not participants:
        return False, "⚠️ Никто не записался на игру."
    session = dict(session)
    session["phase"] = "playing"
    session["turn_index"] = 0
    session["scores"] = {str(p["id"]): 0 for p in participants}
    session["solved_words"] = 0
    session["used_words"] = []
    session["game_started_at"] = time.time()
    db_update_json(KEY, lambda data: _mutate_current(data, gid, session), {})
    current = _get(gid)
    try:
        if current and current.get("last_message_id"):
            mid = current["last_message_id"]
            if (current.get("config") or {}).get("photo", {}).get("file_id"):
                bot.edit_message_caption(_game_status_text(current), gid, mid, reply_markup=None, parse_mode="HTML")
            else:
                bot.edit_message_text(_game_status_text(current), gid, mid, reply_markup=None, parse_mode="HTML")
        bot.send_message(gid, "🎯 <b>Игра «Слова» начинается!</b>\n\nХоды передаются строго по кругу.", parse_mode="HTML")
    except Exception:
        LOG.exception("failed to announce word game start")
    _send_turn(gid, _get(gid))
    return True, None


def start_from_button(call, gid):
    if not is_chat_admin(gid, call.from_user.id):
        return bot.answer_callback_query(call.id, "⛔ Только администратор может начать игру.", show_alert=True)
    session = _get(gid)
    if not session or session.get("phase") not in {"registration", "waiting"}:
        return bot.answer_callback_query(call.id, "ℹ️ Активной записи нет.", show_alert=True)
    bot.answer_callback_query(call.id, "▶️ Игра запускается…")
    ok, err = _start_game(gid, session)
    if not ok:
        return bot.send_message(gid, err)


def _finish_game(gid, session, reason="complete"):
    session = dict(session)
    session["phase"] = "finished"
    session["finished_at"] = time.time()
    session["finish_reason"] = reason
    db_update_json(KEY, lambda data: _mutate_current(data, gid, session), {})
    participants = _participant_list(session)
    scores = session.get("scores") or {}
    ranked = sorted(participants, key=lambda p: (-int(scores.get(str(p["id"]), 0)), float(p.get("registered_at") or 0)))
    cfg = session.get("config") or {}
    places = int(cfg.get("prize_places", 3) or 3)
    prizes = cfg.get("prizes") or []
    reward = cfg.get("reward_username")
    lines = ["🏁 <b>Гра «Слова» завершена!</b>", "", f"Всього відгадано: <b>{int(session.get('solved_words', 0) or 0)}</b>", "", "🏆 <b>Результати:</b>"]
    for i, p in enumerate(ranked[:places], 1):
        score = int(scores.get(str(p["id"]), 0))
        prize = prizes[i - 1] if i - 1 < len(prizes) else "нагорода не вказана"
        lines.append(f"<b>{i}. {_mention(p)}</b> — {score} бал. 🎁 {_esc(prize)}")
    if reward:
        lines += ["", f"💰 Для отримання нагороди звертайтесь до: <b>{_esc(reward)}</b>"]
    try: bot.send_message(gid, "\n".join(lines), parse_mode="HTML")
    except Exception: LOG.exception("failed to send word game results")
    return True


def _advance_after_timeout(gid, session):
    participants = _participant_list(session)
    if not participants:
        return _finish_game(gid, session, "no_participants")
    current = participants[int(session.get("turn_index", 0) or 0) % len(participants)]
    try:
        bot.send_message(gid, f"⏰ {_mention(current)} не встиг(ла) відгадати слово. Хід переходить далі.", parse_mode="HTML")
    except Exception: pass
    session = dict(session)
    session["turn_index"] = (int(session.get("turn_index", 0) or 0) + 1) % len(participants)
    session["current_word"] = None
    session["answer_deadline"] = None
    db_update_json(KEY, lambda data: _mutate_current(data, gid, session), {})
    _send_turn(gid, _get(gid))


def handle_text(message):
    gid = message.chat.id
    session = _get(gid)
    if not session or session.get("phase") != "playing":
        return False
    uid = getattr(getattr(message, "from_user", None), "id", None)
    if uid is None:
        return True
    # Во время игры админы тоже не отправляют обычный текст в AI — игра полностью автономна.
    try:
        if not is_chat_admin(gid, uid):
            bot.delete_message(gid, message.message_id)
            bot.send_message(gid, "🚫 Нельзя писать в чат во время игры! Дождитесь конца.")
            return True
    except Exception:
        pass
    current_uid = int(session.get("current_user_id") or 0)
    if uid != current_uid:
        return True
    answer = _normalize_answer(message.text or "")
    word = _normalize_answer(session.get("current_word") or "")
    if answer != word:
        return True
    # Победа засчитывается атомарно: если два одинаковых сообщения прилетят почти одновременно,
    # только одно сможет закрыть текущий ход.
    outcome = {"ok": False, "session": None}
    def mutate(data):
        cur = data.get(str(gid))
        if not cur or cur.get("phase") != "playing" or int(cur.get("current_user_id") or 0) != uid:
            return data
        if _normalize_answer(cur.get("current_word") or "") != answer:
            return data
        cur.setdefault("scores", {})[str(uid)] = int(cur.get("scores", {}).get(str(uid), 0)) + 1
        cur["solved_words"] = int(cur.get("solved_words", 0) or 0) + 1
        participants = _participant_list(cur)
        cur["turn_index"] = (int(cur.get("turn_index", 0) or 0) + 1) % max(1, len(participants))
        cur["current_word"] = None; cur["answer_deadline"] = None
        outcome["ok"] = True; outcome["session"] = dict(cur)
        return data
    db_update_json(KEY, mutate, {})
    if not outcome["ok"]:
        return True
    winner = next((p for p in _participant_list(outcome["session"]) if p.get("id") == uid), None)
    try: bot.send_message(gid, f"✅ {_mention(winner)} правильно! +1 бал.", parse_mode="HTML")
    except Exception: pass
    current = _get(gid)
    total = int((current.get("config") or {}).get("total_words", 20) or 20)
    if int(current.get("solved_words", 0) or 0) >= total:
        return _finish_game(gid, current, "complete")
    _send_turn(gid, current)
    return True


def handle_media(message):
    gid = message.chat.id
    session = _get(gid)
    if not session or session.get("phase") != "playing":
        return False
    try:
        bot.delete_message(gid, message.message_id)
        bot.send_message(gid, "🚫 Нельзя писать в чат во время игры! Дождитесь конца.")
    except Exception: pass
    return True


def stop(message):
    gid = message.chat.id
    if not is_chat_admin(gid, message.from_user.id):
        return bot.reply_to(message, "⛔ Только администратор может завершить игру.")
    session = _get(gid)
    if not session or session.get("phase") == "finished":
        return bot.reply_to(message, "ℹ️ Активной игры нет.")
    db_update_json(KEY, lambda data: _stop_mutate(data, gid), {})
    bot.send_message(gid, "🛑 Игра «Слова» остановлена администратором.")


def _stop_mutate(data, gid):
    cur = data.get(str(gid))
    if cur:
        cur["phase"] = "finished"; cur["finished_at"] = time.time(); cur["finish_reason"] = "admin"
    return data


def tick(bot_instance=None):
    now = time.time()
    sessions = _store()
    for gid_str, raw in list(sessions.items()):
        try: gid = int(gid_str)
        except Exception: continue
        session = _get(gid)
        if not session: continue
        phase = session.get("phase")
        if phase == "scheduled" and now >= float(session.get("start_at") or 0):
            session = dict(session); session["phase"] = "registration"; session["registration_started_at"] = now
            db_update_json(KEY, lambda data: _mutate_current(data, gid, session), {})
            _send_registration(gid, _get(gid), replace=False)
            continue
        if phase == "registration":
            next_rep = session.get("next_republish_at")
            if next_rep and now >= float(next_rep):
                _send_registration(gid, session, replace=True)
            continue
        if phase == "playing" and session.get("answer_deadline") and now >= float(session["answer_deadline"]):
            _advance_after_timeout(gid, session)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("wgame|"))
def _callback_router(call):
    try:
        parts = (call.data or "").split("|")
        if len(parts) < 3:
            return
        action = parts[1]
        try: gid = int(parts[2])
        except Exception: return bot.answer_callback_query(call.id, "⚠️ Некорректная игра.", show_alert=True)
        if action == "register":
            _register(call, gid)
        elif action == "start":
            start_from_button(call, gid)
        else:
            bot.answer_callback_query(call.id)
    except Exception:
        LOG.exception("word game callback failed")
        try: bot.answer_callback_query(call.id, "⚠️ Ошибка игры.", show_alert=True)
        except Exception: pass


@bot.message_handler(content_types=["text"], func=lambda m: is_active(m.chat.id) and getattr(m.chat, "type", "") in ("group", "supergroup"))
def _active_text_router(message):
    return handle_text(message)


@bot.message_handler(content_types=[
    "photo", "video", "animation", "document", "voice", "audio", "sticker", "video_note"
], func=lambda m: is_active(m.chat.id) and getattr(m.chat, "type", "") in ("group", "supergroup"))
def _active_media_router(message):
    return handle_media(message)
