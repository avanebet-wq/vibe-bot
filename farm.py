# -*- coding: utf-8 -*-
"""Мини-игра «Ферма»: выращивание, полив и прокачка территории.

Команды «ферма» / «плантация» / «сад» и /game открывают одно и то же меню.
Повторный вызов команды удаляет предыдущее сообщение с фермой, чтобы не
засорять чат — весь прогресс и обратная связь живут внутри одного
редактируемого сообщения.

Экономика:
- Полив грядки даёт только опыт (XP) — ранг профиля.
- Сбор урожая даёт немного валюты «Луны» — доход зависит от уровня
  территории и числа посаженных кустов (грядок).
- Прокачка территории и грядок стоит Луны, а не опыта. Первые уровни
  дешёвые, чтобы прогресс в начале ощущался быстрым.
"""
import html
import logging
import time

from telebot import types

from database import conn, db_lock
from runtime import bot
from profile import get_xp, add_xp
from currency import get_balance, add_balance, spend_balance, fmt as fmt_money, CURRENCY_NAME, CURRENCY_ICON

LOG = logging.getLogger("farm")

WATER_COOLDOWN = 15 * 60  # 15 минут (4 полива = 1 час до полного созревания)
GROWTH_STEP = 25
CB = "farm"

# Обычные эмодзи (premium custom emoji боту недоступны без покупки
# username на Fragment — Telegram показывает такие теги простым текстом).
BUSH_EMOJI = "🌿"
MOON_EMOJI = "🌙"
TERRITORY_EMOJI = "📈"
UPGRADE_EMOJI = "📐"
SEED_EMOJI = "🌱"

# Уровни территории: сколько кустов травки помещается, множитель дохода
# и цена перехода на следующий уровень (в Лунах).
LEVELS = {
    1: {"plots": 1, "income_mult": 1.00, "upgrade_cost": 40},
    2: {"plots": 2, "income_mult": 1.15, "upgrade_cost": 90},
    3: {"plots": 3, "income_mult": 1.35, "upgrade_cost": 180},
    4: {"plots": 4, "income_mult": 1.60, "upgrade_cost": 350},
    5: {"plots": 6, "income_mult": 1.90, "upgrade_cost": 650},
    6: {"plots": 8, "income_mult": 2.25, "upgrade_cost": 1200},
    7: {"plots": 10, "income_mult": 2.70, "upgrade_cost": 0},
}
MAX_LEVEL = max(LEVELS)

# Цена посадки ещё одного куста травки на грядке (растёт с каждым кустом).
BUSH_BASE_COST = 25
BUSH_COST_STEP = 20

# Доход в Лунах за один куст за один цикл роста (до множителя уровня и сорта).
BUSH_BASE_YIELD = 3

# Сорта травки: множитель дохода и цена открытия (в Лунах). Первый сорт открыт
# по умолчанию и бесплатен. Цена и множитель растут с каждым следующим сортом.
STRAINS = [
    {"name": "OG Kush", "mult": 1.00, "cost": 0},
    {"name": "Gorilla Glue", "mult": 1.15, "cost": 150},
    {"name": "Cookies Gelato", "mult": 1.30, "cost": 350},
    {"name": "Royal Queen Seeds", "mult": 1.50, "cost": 650},
    {"name": "Royal Runtz", "mult": 1.70, "cost": 1100},
    {"name": "Zkittlez × Gelato", "mult": 1.95, "cost": 1800},
    {"name": "Rare Harvest", "mult": 2.25, "cost": 2800},
    {"name": "Wedding Cake", "mult": 2.60, "cost": 4200},
]
MAX_STRAIN_INDEX = len(STRAINS) - 1
STRAIN_EMOJI = "🧬"

WATER_XP_BASE = [(4, 0.50), (6, 0.28), (9, 0.14), (14, 0.06), (20, 0.02)]


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
                    bushes INTEGER NOT NULL DEFAULT 1,
                    last_water REAL NOT NULL DEFAULT 0,
                    last_message_id BIGINT,
                    updated_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(chat_id, user_id)
                )"""
            )
            # Миграция для баз, созданных до появления кустов травки.
            try:
                conn.execute("ALTER TABLE farm_state ADD COLUMN IF NOT EXISTS bushes INTEGER NOT NULL DEFAULT 1")
            except Exception:
                conn.rollback()
            # Миграция для баз, созданных до появления сортов.
            try:
                conn.execute("ALTER TABLE farm_state ADD COLUMN IF NOT EXISTS strain_index INTEGER NOT NULL DEFAULT 0")
            except Exception:
                conn.rollback()
            try:
                conn.execute("ALTER TABLE farm_state ADD COLUMN IF NOT EXISTS unlocked_strains TEXT NOT NULL DEFAULT '0'")
            except Exception:
                conn.rollback()
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


def _parse_unlocked(raw):
    try:
        result = {int(x) for x in str(raw or "0").split(",") if x.strip() != ""}
    except Exception:
        result = set()
    result.add(0)
    return result


def _dump_unlocked(unlocked_set):
    return ",".join(str(i) for i in sorted(unlocked_set))


def _row_to_state(row):
    if not row:
        return None
    level, growth, planted, bushes, last_water, last_message_id, strain_index, unlocked_strains = row
    return {
        "level": int(level),
        "growth": int(growth),
        "planted": bool(planted),
        "bushes": max(1, int(bushes or 1)),
        "last_water": float(last_water or 0),
        "last_message_id": last_message_id,
        "strain_index": max(0, min(MAX_STRAIN_INDEX, int(strain_index or 0))),
        "unlocked_strains": _parse_unlocked(unlocked_strains),
    }


def _get_state(chat_id, user_id):
    with db_lock:
        try:
            row = conn.execute(
                "SELECT level, growth, planted, bushes, last_water, last_message_id, "
                "strain_index, unlocked_strains "
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


def _roll(table):
    import random
    values = [x[0] for x in table]
    weights = [x[1] for x in table]
    return random.choices(values, weights=weights, k=1)[0]


def _roll_xp(table):
    return max(1, int(round(_roll(table))))


def _bush_cost(current_bushes):
    """Цена посадки следующего куста (current_bushes -> current_bushes+1)."""
    return BUSH_BASE_COST + BUSH_COST_STEP * max(0, current_bushes - 1)


def _harvest_income(level, bushes, strain_index=0):
    mult = LEVELS[level]["income_mult"]
    strain_mult = STRAINS[max(0, min(MAX_STRAIN_INDEX, strain_index))]["mult"]
    raw = BUSH_BASE_YIELD * bushes * mult * strain_mult
    return max(1, int(round(raw)))


def _mention(user_id, display_name):
    name = html.escape(display_name or str(user_id))
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def _bar(growth):
    filled = max(0, min(4, growth // GROWTH_STEP))
    return "🟩" * filled + "⬜" * (4 - filled)


def _cb_data(action, owner_id):
    return f"{CB}|{action}|{owner_id}"


def _keyboard(owner_id, level):
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("💧 Полить", callback_data=_cb_data("water", owner_id)))
    kb.row(types.InlineKeyboardButton(f"{SEED_EMOJI} Посадить семена", callback_data=_cb_data("plant", owner_id)))
    kb.row(types.InlineKeyboardButton(f"{BUSH_EMOJI} Посадить куст травки", callback_data=_cb_data("bush", owner_id)))
    if level < MAX_LEVEL:
        kb.row(types.InlineKeyboardButton(f"{UPGRADE_EMOJI} Увеличить территорию", callback_data=_cb_data("territory", owner_id)))
    kb.row(types.InlineKeyboardButton(f"{STRAIN_EMOJI} Купить сорт", callback_data=_cb_data("strains", owner_id)))
    return kb


def _strains_keyboard(owner_id, state):
    kb = types.InlineKeyboardMarkup()
    unlocked = state["unlocked_strains"]
    current = state["strain_index"]
    for idx, strain in enumerate(STRAINS):
        if idx == current:
            label = f"✅ {strain['name']} (x{strain['mult']:.2f})"
        elif idx in unlocked:
            label = f"🔓 {strain['name']} (x{strain['mult']:.2f}) — выбрать"
        else:
            label = f"🔒 {strain['name']} (x{strain['mult']:.2f}) — {fmt_money(strain['cost'])}"
        kb.row(types.InlineKeyboardButton(label, callback_data=_cb_data(f"strain{idx}", owner_id)))
    kb.row(types.InlineKeyboardButton("⬅️ Назад к ферме", callback_data=_cb_data("back", owner_id)))
    return kb


def _render_strains(uid, display_name, state, flash=None):
    unlocked = state["unlocked_strains"]
    current = state["strain_index"]
    lines = [
        f"{STRAIN_EMOJI} {_mention(uid, display_name)}, выбор сорта травки:",
        "",
        "Каждый сорт даёт свой множитель к доходу с урожая. Купленные сорта",
        "остаются открытыми навсегда — переключаться между ними можно бесплатно.",
        "",
    ]
    for idx, strain in enumerate(STRAINS):
        marker = "✅" if idx == current else ("🔓" if idx in unlocked else "🔒")
        price = "открыт" if idx in unlocked else fmt_money(strain["cost"])
        lines.append(f"{marker} <b>{strain['name']}</b> — x{strain['mult']:.2f} ({price})")
    lines.append("")
    if flash:
        lines.append(flash)
        lines.append("")
    return "\n".join(lines)


def _render(uid, display_name, state, flash=None):
    level = state["level"]
    info = LEVELS[level]
    bushes = state["bushes"]
    now = time.time()
    harvest_amount = _harvest_income(level, bushes, state["strain_index"])
    strain = STRAINS[state["strain_index"]]
    lines = [
        f"{BUSH_EMOJI} {_mention(uid, display_name)} ваша ферма:",
        "",
        f"{TERRITORY_EMOJI} Территория: <b>{level}</b> ур. ({info['plots']} соток занято под {bushes} куст.)",
        f"{STRAIN_EMOJI} Сорт: <b>{strain['name']}</b> (x{strain['mult']:.2f})",
        f"{MOON_EMOJI} Доход за урожай: <b>{harvest_amount}</b>",
        "",
    ]
    if not state["planted"]:
        lines.append(f"🟤 Грядка пустая. Нажми «{SEED_EMOJI} Посадить семена», чтобы начать растить.")
    else:
        growth = state["growth"]
        lines.append(f"{SEED_EMOJI} Рост: {_bar(growth)} <b>{growth}%</b>")
        if growth >= 100:
            lines.append(f"🌾 Урожай созрел! Нажми «{SEED_EMOJI} Посадить семена», чтобы собрать и посадить заново.")
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

    if bushes < info["plots"]:
        bcost = _bush_cost(bushes)
        lines.append(f"{BUSH_EMOJI} Посадить ещё куст травки: <b>{fmt_money(bcost)}</b> ({bushes}/{info['plots']})")

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
    text = _render(uid, display_name, state)
    msg = bot.send_message(chat_id, text, reply_markup=_keyboard(uid, state["level"]), parse_mode="HTML")
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
    status = f"{state['growth']}% {SEED_EMOJI}" if state["planted"] else "пусто"
    harvest_amount = _harvest_income(state["level"], state["bushes"], state["strain_index"])
    strain = STRAINS[state["strain_index"]]
    return (
        f"{BUSH_EMOJI} Ферма: ур. <b>{state['level']}</b> ({state['bushes']}/{info['plots']} кустов) — {status}\n"
        f"{STRAIN_EMOJI} Сорт: <b>{strain['name']}</b> (x{strain['mult']:.2f})\n"
        f"{MOON_EMOJI} Доход за урожай: <b>{harvest_amount}</b>"
    )


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


def _refresh_message(call, uid, display_name, state, flash=None):
    text = _render(uid, display_name, state, flash=flash)
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=_keyboard(str(call.from_user.id), state["level"]),
            parse_mode="HTML",
        )
    except Exception:
        # Сообщение могли удалить вручную — отправим новое и запомним его id.
        try:
            msg = bot.send_message(
                call.message.chat.id, text,
                reply_markup=_keyboard(str(call.from_user.id), state["level"]), parse_mode="HTML",
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

    xp = _roll_xp(WATER_XP_BASE)
    new_growth = min(100, state["growth"] + GROWTH_STEP)
    _update(chat_id, uid, growth=new_growth, last_water=now)
    add_xp(chat_id, uid, xp)

    state["growth"] = new_growth
    state["last_water"] = now
    bot.answer_callback_query(call.id, f"💧 +{xp} XP")
    flash = f"✨ Ты полил(а) грядку и получил(а) <b>+{xp} XP</b>"
    _refresh_message(call, uid, display_name, state, flash=flash)


def _handle_plant(call, chat_id, uid, username, display_name):
    state = _ensure_row(chat_id, uid, username, display_name)

    if not state["planted"]:
        _update(chat_id, uid, planted=1, growth=0)
        state["planted"] = True
        state["growth"] = 0
        bot.answer_callback_query(call.id, "🌱 Семена посажены!")
        _refresh_message(call, uid, display_name, state, flash=f"{SEED_EMOJI} Семена посажены. Не забывай поливать!")
        return

    if state["growth"] < 100:
        bot.answer_callback_query(call.id, "🌱 Уже посажено. Поливай, чтобы вырастить.", show_alert=True)
        return

    income = _harvest_income(state["level"], state["bushes"], state["strain_index"])
    _update(chat_id, uid, planted=1, growth=0)
    add_balance(chat_id, uid, income)
    state["planted"] = True
    state["growth"] = 0
    bot.answer_callback_query(call.id, f"🌾 Урожай собран! +{income} {CURRENCY_NAME}")
    flash = f"🌾 Урожай собран: <b>+{fmt_money(income)}</b>. Новые семена уже в земле!"
    _refresh_message(call, uid, display_name, state, flash=flash)


def _handle_bush(call, chat_id, uid, username, display_name):
    state = _ensure_row(chat_id, uid, username, display_name)
    level = state["level"]
    plots = LEVELS[level]["plots"]
    bushes = state["bushes"]

    if bushes >= plots:
        bot.answer_callback_query(call.id, "🌿 Все сотки уже заняты кустами. Увеличь территорию.", show_alert=True)
        return

    cost = _bush_cost(bushes)
    balance = get_balance(chat_id, uid)
    if balance < cost:
        bot.answer_callback_query(
            call.id,
            f"🌿 Не хватает Лун: нужно {cost}, у тебя {balance}.",
            show_alert=True,
        )
        return

    if not spend_balance(chat_id, uid, cost):
        bot.answer_callback_query(call.id, "🌿 Не получилось списать Луны, попробуй ещё раз.", show_alert=True)
        return

    new_bushes = bushes + 1
    _update(chat_id, uid, bushes=new_bushes)
    state["bushes"] = new_bushes
    bot.answer_callback_query(call.id, f"🌿 Куст посажен! ({new_bushes}/{plots})")
    flash = f"{BUSH_EMOJI} Посажен ещё один куст травки ({new_bushes}/{plots}) — доход с урожая вырос."
    _refresh_message(call, uid, display_name, state, flash=flash)


def _handle_territory(call, chat_id, uid, username, display_name):
    state = _ensure_row(chat_id, uid, username, display_name)
    level = state["level"]
    if level >= MAX_LEVEL:
        bot.answer_callback_query(call.id, "📐 Уже максимальный уровень фермы.", show_alert=True)
        return

    cost = LEVELS[level]["upgrade_cost"]
    balance = get_balance(chat_id, uid)
    if balance < cost:
        bot.answer_callback_query(
            call.id,
            f"📐 Не хватает Лун: нужно {cost}, у тебя {balance}.",
            show_alert=True,
        )
        return

    if not spend_balance(chat_id, uid, cost):
        bot.answer_callback_query(call.id, "📐 Не получилось списать Луны, попробуй ещё раз.", show_alert=True)
        return

    new_level = level + 1
    _update(chat_id, uid, level=new_level)
    state["level"] = new_level
    plots = LEVELS[new_level]["plots"]
    bot.answer_callback_query(call.id, f"📐 Территория увеличена! Уровень {new_level}.")
    flash = f"{UPGRADE_EMOJI} Территория увеличена до {plots} соток (уровень {new_level})"
    _refresh_message(call, uid, display_name, state, flash=flash)


def _handle_strains(call, chat_id, uid, username, display_name):
    state = _ensure_row(chat_id, uid, username, display_name)
    text = _render_strains(uid, display_name, state)
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=_strains_keyboard(str(call.from_user.id), state),
            parse_mode="HTML",
        )
    except Exception:
        LOG.exception("farm: failed to open strains menu")
    bot.answer_callback_query(call.id)


def _handle_strain_pick(call, chat_id, uid, username, display_name, strain_idx):
    state = _ensure_row(chat_id, uid, username, display_name)
    if strain_idx < 0 or strain_idx > MAX_STRAIN_INDEX:
        bot.answer_callback_query(call.id)
        return

    unlocked = state["unlocked_strains"]
    strain = STRAINS[strain_idx]

    if strain_idx in unlocked:
        _update(chat_id, uid, strain_index=strain_idx)
        state["strain_index"] = strain_idx
        bot.answer_callback_query(call.id, f"{STRAIN_EMOJI} Выбран сорт: {strain['name']}")
        flash = f"{STRAIN_EMOJI} Теперь ты выращиваешь <b>{strain['name']}</b>."
    else:
        cost = strain["cost"]
        balance = get_balance(chat_id, uid)
        if balance < cost:
            bot.answer_callback_query(
                call.id,
                f"{STRAIN_EMOJI} Не хватает Лун: нужно {cost}, у тебя {balance}.",
                show_alert=True,
            )
            return
        if not spend_balance(chat_id, uid, cost):
            bot.answer_callback_query(call.id, "⚠️ Не получилось списать Луны, попробуй ещё раз.", show_alert=True)
            return
        unlocked = set(unlocked)
        unlocked.add(strain_idx)
        _update(chat_id, uid, unlocked_strains=_dump_unlocked(unlocked), strain_index=strain_idx)
        state["unlocked_strains"] = unlocked
        state["strain_index"] = strain_idx
        bot.answer_callback_query(call.id, f"{STRAIN_EMOJI} Куплен и выбран сорт: {strain['name']}")
        flash = f"{STRAIN_EMOJI} Куплен новый сорт <b>{strain['name']}</b> (x{strain['mult']:.2f}) и выбран для выращивания."

    text = _render_strains(uid, display_name, state, flash=flash)
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=_strains_keyboard(str(call.from_user.id), state),
            parse_mode="HTML",
        )
    except Exception:
        LOG.exception("farm: failed to refresh strains menu")


def _handle_back(call, chat_id, uid, username, display_name):
    state = _ensure_row(chat_id, uid, username, display_name)
    bot.answer_callback_query(call.id)
    _refresh_message(call, uid, display_name, state)


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
    elif action == "bush":
        _handle_bush(call, chat_id, uid, username, display_name)
    elif action == "territory":
        _handle_territory(call, chat_id, uid, username, display_name)
    elif action == "strains":
        _handle_strains(call, chat_id, uid, username, display_name)
    elif action == "back":
        _handle_back(call, chat_id, uid, username, display_name)
    elif action.startswith("strain") and action[len("strain"):].isdigit():
        strain_idx = int(action[len("strain"):])
        _handle_strain_pick(call, chat_id, uid, username, display_name, strain_idx)
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

# updated 2026-09-20
