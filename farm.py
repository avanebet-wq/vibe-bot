# -*- coding: utf-8 -*-
"""Мини-игра «Ферма»: выращивание, полив и прокачка территории.

Команды «ферма» / «плантация» / «сад» и /game открывают одно и то же меню.
Повторный вызов команды удаляет предыдущее сообщение с фермой, чтобы не
засорять чат — весь прогресс и обратная связь (полученный опыт) живут
внутри одного редактируемого сообщения.
"""
import html
import logging
import time

from telebot import types

from database import conn, db_lock
from runtime import bot
from profile import get_xp, add_xp, spend_xp

LOG = logging.getLogger("farm")

WATER_COOLDOWN = 4 * 60 * 60  # 4 часа
GROWTH_STEP = 25
CB = "farm"

# Уровни прокачки фермы: количество соток, множитель опыта и цена перехода
# на следующий уровень (в XP, списывается с общего счёта опыта).
LEVELS = {
    1: {"plots": 1, "xp_mult": 1.00, "upgrade_cost": 150},
    2: {"plots": 2, "xp_mult": 1.20, "upgrade_cost": 350},
    3: {"plots": 3, "xp_mult": 1.45, "upgrade_cost": 650},
    4: {"plots": 4, "xp_mult": 1.75, "upgrade_cost": 1200},
    5: {"plots": 6, "xp_mult": 2.10, "upgrade_cost": 2200},
    6: {"plots": 8, "xp_mult": 2.50, "upgrade_cost": 4000},
    7: {"plots": 10, "xp_mult": 3.00, "upgrade_cost": 0},
}
MAX_LEVEL = max(LEVELS)

WATER_XP_BASE = [(4, 0.50), (6, 0.28), (9, 0.14), (14, 0.06), (20, 0.02)]
HARVEST_XP_BASE = [(12, 0.50), (18, 0.28), (26, 0.14), (40, 0.06), (60, 0.02)]


def ensure_schema():
    with db_lock:
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS farm_state (
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT,
                    display_name TEXT,
                    level INTEGER NOT NULL DEFAULT 1,
                    growth INTEGER NOT NULL DEFAULT 0,
                    planted INTEGER NOT NULL DEFAULT 0,
                    last_water REAL NOT NULL DEFAULT 0,
                    last_message_id BIGINT,
                    updated_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(chat_id, user_id)
                )"""
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _user_info(user):
    uid = getattr(user, "id", None)
    username = (getattr(user, "username", None) or "").strip()
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    display = " ".join(x for x in (first, last) if x).strip() or username or str(uid)
    return str(uid), username, display


def _row_to_state(row):
    if not row:
        return None
    level, growth, planted, last_water, last_message_id = row
    return {
        "level": int(level),
        "growth": int(growth),
        "planted": bool(planted),
        "last_water": float(last_water or 0),
        "last_message_id": last_message_id,
    }


def _get_state(chat_id, user_id):
    with db_lock:
        try:
            row = conn.execute(
                "SELECT level, growth, planted, last_water, last_message_id "
                "FROM farm_state WHERE chat_id=? AND user_id=?",
                (str(chat_id), str(user_id)),
            ).fetchone()
        except Exception:
            conn.rollback()
            raise
    return _row_to_state(row)


def _ensure_row(chat_id, user_id, username, display_name):
    with db_lock:
        try:
            conn.execute(
                "INSERT INTO farm_state(chat_id,user_id,username,display_name,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET "
                "username=excluded.username, display_name=excluded.display_name",
                (str(chat_id), str(user_id), username, display_name, time.time()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _get_state(chat_id, user_id)


def _update(chat_id, user_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    params = list(fields.values()) + [str(chat_id), str(user_id)]
    with db_lock:
        try:
            conn.execute(
                f"UPDATE farm_state SET {cols} WHERE chat_id=? AND user_id=?",
                params,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _save_message_id(chat_id, user_id, message_id):
    _update(chat_id, user_id, last_message_id=message_id)


def _fmt_wait(seconds):
    seconds = max(0, int(seconds + 0.999))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if secs or not parts:
        parts.append(f"{secs} сек")
    return " ".join(parts)


def _roll_xp(table, xp_mult):
    import random
    values = [x[0] for x in table]
    weights = [x[1] for x in table]
    base = random.choices(values, weights=weights, k=1)[0]
    return max(1, int(round(base * xp_mult)))


def _bar(growth):
    filled = max(0, min(4, growth // GROWTH_STEP))
    return "🟩" * filled + "⬜" * (4 - filled)


def _cb_data(action, owner_id):
    return f"{CB}|{action}|{owner_id}"


def _keyboard(owner_id):
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("💧 Полить", callback_data=_cb_data("water", owner_id)))
    kb.row(types.InlineKeyboardButton("🌱 Посадить семена", callback_data=_cb_data("plant", owner_id)))
    kb.row(types.InlineKeyboardButton("📐 Увеличить территорию", callback_data=_cb_data("territory", owner_id)))
    return kb


def _render(display_name, state, xp_total, flash=None):
    level = state["level"]
    info = LEVELS[level]
    now = time.time()
    lines = [
        "🌿 <b>Ферма</b>",
        f"👤 {html.escape(display_name)}",
        f"📈 Уровень: <b>{level}</b> ({info['plots']} соток)",
        "",
    ]
    if not state["planted"]:
        lines.append("🟤 Грядка пустая. Нажми «🌱 Посадить семена», чтобы начать растить.")
    else:
        growth = state["growth"]
        lines.append(f"🌱 Рост: {_bar(growth)} <b>{growth}%</b>")
        if growth >= 100:
            lines.append("🌾 Урожай созрел! Нажми «🌱 Посадить семена», чтобы собрать и посадить заново.")
        else:
            remaining = WATER_COOLDOWN - (now - state["last_water"]) if state["last_water"] else 0
            if remaining > 0:
                lines.append(f"🕐 Следующий полив: через <b>{_fmt_wait(remaining)}</b>")
            else:
                lines.append("💧 Можно поливать!")
    lines.append("")
    if flash:
        lines.append(flash)
        lines.append("")
    if level < MAX_LEVEL:
        cost = info["upgrade_cost"]
        lines.append(f"📐 Увеличить территорию: <b>{cost} XP</b>")
    else:
        lines.append("📐 Территория максимального размера")
    lines.append(f"⭐ Опыт: <b>{xp_total} XP</b>")
    return "\n".join(lines)


def _send_farm(message, user):
    chat_id = message.chat.id
    uid, username, display_name = _user_info(user)
    state = _ensure_row(chat_id, uid, username, display_name)
    if state.get("last_message_id"):
        try:
            bot.delete_message(chat_id, state["last_message_id"])
        except Exception:
            pass
    xp_total = get_xp(chat_id, uid)
    text = _render(display_name, state, xp_total)
    msg = bot.send_message(chat_id, text, reply_markup=_keyboard(uid), parse_mode="HTML")
    _save_message_id(chat_id, uid, msg.message_id)


def cmd_farm(message, args=""):
    try:
        _send_farm(message, message.from_user)
    except Exception:
        LOG.exception("cmd_farm failed")
        bot.reply_to(message, "⚠️ Не удалось открыть ферму, попробуй ещё раз.")


def profile_line(chat_id, user_id):
    """Короткая строка для профиля пользователя."""
    state = _get_state(chat_id, user_id)
    if not state:
        return None
    info = LEVELS[state["level"]]
    status = f"{state['growth']}% 🌱" if state["planted"] else "пусто"
    return f"🌿 Ферма: ур. <b>{state['level']}</b> ({info['plots']} соток) — {status}"


def reset_user_farm(chat_id, user_id):
    """Сбрасывает прогресс фермы указанного пользователя (для админов)."""
    with db_lock:
        try:
            cur = conn.execute(
                "DELETE FROM farm_state WHERE chat_id=? AND user_id=?",
                (str(chat_id), str(user_id)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return getattr(cur, "rowcount", 0) or 0


def cmd_reset(message, args_text=""):
    from utils import extract_target, is_chat_admin, get_mention

    cid = message.chat.id
    if not is_chat_admin(cid, message.from_user.id):
        bot.reply_to(message, "⛔ Эта команда только для админов чата.")
        return

    target_id, target_name, _ = extract_target(message, args_text)
    if not target_id:
        bot.reply_to(
            message,
            "🤔 Не поняла, кого. Ответь этой командой на сообщение человека "
            "или укажи @username.\n\nПример: <code>обнулить @username</code>",
        )
        return

    try:
        reset_user_farm(cid, target_id)
    except Exception:
        LOG.exception("reset farm failed")
        bot.reply_to(message, "⚠️ Не получилось обнулить ферму, попробуй ещё раз.")
        return

    bot.reply_to(
        message,
        f"🧹 Ферма {get_mention(target_id, target_name)} обнулена.",
        parse_mode="HTML",
    )


def _owner_only(call, owner_id):
    if str(call.from_user.id) != str(owner_id):
        bot.answer_callback_query(call.id, "🌿 Это не твоя ферма. Открой свою командой «ферма».", show_alert=True)
        return False
    return True


def _refresh_message(call, display_name, state, xp_total, flash=None):
    text = _render(display_name, state, xp_total, flash=flash)
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=_keyboard(str(call.from_user.id)),
            parse_mode="HTML",
        )
    except Exception:
        # Сообщение могли удалить вручную — отправим новое и запомним его id.
        try:
            msg = bot.send_message(
                call.message.chat.id, text,
                reply_markup=_keyboard(str(call.from_user.id)), parse_mode="HTML",
            )
            _save_message_id(call.message.chat.id, str(call.from_user.id), msg.message_id)
        except Exception:
            LOG.exception("farm: failed to refresh message")


def _handle_water(call, chat_id, uid, username, display_name):
    state = _ensure_row(chat_id, uid, username, display_name)
    if not state["planted"]:
        bot.answer_callback_query(call.id, "🌱 Сначала посади семена.", show_alert=True)
        return
    if state["growth"] >= 100:
        bot.answer_callback_query(call.id, "🌾 Уже готово к сбору! Нажми «Посадить семена».", show_alert=True)
        return
    now = time.time()
    remaining = WATER_COOLDOWN - (now - state["last_water"]) if state["last_water"] else 0
    if remaining > 0:
        bot.answer_callback_query(call.id, f"🕐 Ещё рано. Осталось: {_fmt_wait(remaining)}", show_alert=True)
        return

    xp_mult = LEVELS[state["level"]]["xp_mult"]
    xp = _roll_xp(WATER_XP_BASE, xp_mult)
    new_growth = min(100, state["growth"] + GROWTH_STEP)
    _update(chat_id, uid, growth=new_growth, last_water=now)
    xp_total = add_xp(chat_id, uid, xp)

    state["growth"] = new_growth
    state["last_water"] = now
    bot.answer_callback_query(call.id, f"💧 +{xp} XP")
    flash = f"✨ Ты полил(а) грядку и получил(а) <b>+{xp} XP</b>"
    _refresh_message(call, display_name, state, xp_total, flash=flash)


def _handle_plant(call, chat_id, uid, username, display_name):
    state = _ensure_row(chat_id, uid, username, display_name)
    xp_mult = LEVELS[state["level"]]["xp_mult"]

    if not state["planted"]:
        _update(chat_id, uid, planted=1, growth=0)
        state["planted"] = True
        state["growth"] = 0
        xp_total = get_xp(chat_id, uid)
        bot.answer_callback_query(call.id, "🌱 Семена посажены!")
        _refresh_message(call, display_name, state, xp_total, flash="🌱 Семена посажены. Не забывай поливать!")
        return

    if state["growth"] < 100:
        bot.answer_callback_query(call.id, "🌱 Уже посажено. Поливай, чтобы вырастить.", show_alert=True)
        return

    xp = _roll_xp(HARVEST_XP_BASE, xp_mult)
    _update(chat_id, uid, planted=1, growth=0)
    xp_total = add_xp(chat_id, uid, xp)
    state["planted"] = True
    state["growth"] = 0
    bot.answer_callback_query(call.id, f"🌾 Урожай собран! +{xp} XP")
    flash = f"🌾 Урожай собран, ты получил(а) <b>+{xp} XP</b>. Новые семена уже в земле!"
    _refresh_message(call, display_name, state, xp_total, flash=flash)


def _handle_territory(call, chat_id, uid, username, display_name):
    state = _ensure_row(chat_id, uid, username, display_name)
    level = state["level"]
    if level >= MAX_LEVEL:
        bot.answer_callback_query(call.id, "📐 Уже максимальный уровень фермы.", show_alert=True)
        return

    cost = LEVELS[level]["upgrade_cost"]
    xp_total = get_xp(chat_id, uid)
    if xp_total < cost:
        bot.answer_callback_query(
            call.id,
            f"📐 Не хватает опыта: нужно {cost} XP, у тебя {xp_total} XP.",
            show_alert=True,
        )
        return

    if not spend_xp(chat_id, uid, cost):
        bot.answer_callback_query(call.id, "📐 Не получилось списать опыт, попробуй ещё раз.", show_alert=True)
        return

    new_level = level + 1
    _update(chat_id, uid, level=new_level)
    state["level"] = new_level
    xp_total = get_xp(chat_id, uid)
    plots = LEVELS[new_level]["plots"]
    bot.answer_callback_query(call.id, f"📐 Территория увеличена! Уровень {new_level}.")
    flash = f"📐 Территория увеличена до {plots} соток (уровень {new_level})"
    _refresh_message(call, display_name, state, xp_total, flash=flash)


def _dispatch_callback(call):
    parts = call.data.split("|")
    if len(parts) < 3:
        bot.answer_callback_query(call.id)
        return
    action, owner_id = parts[1], parts[2]
    if not _owner_only(call, owner_id):
        return
    user = call.from_user
    uid, username, display_name = _user_info(user)
    chat_id = call.message.chat.id

    if action == "water":
        _handle_water(call, chat_id, uid, username, display_name)
    elif action == "plant":
        _handle_plant(call, chat_id, uid, username, display_name)
    elif action == "territory":
        _handle_territory(call, chat_id, uid, username, display_name)
    else:
        bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith(f"{CB}|"))
def _on_farm_callback(call):
    try:
        _dispatch_callback(call)
    except Exception:
        LOG.exception("farm callback failed")
        try:
            bot.answer_callback_query(call.id, "⚠️ Что-то пошло не так.")
        except Exception:
            pass

# created 2026-09-20
