# -*- coding: utf-8 -*-
"""Расширенная статистика чата: пользователи, периоды, часы, Лиза, модерация и графики."""
import io
import time
import logging
from datetime import datetime, timedelta

import logging
import matplotlib
logging.getLogger("matplotlib").setLevel(logging.WARNING)
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from runtime import bot
from config import TZ
from database import db_get, db_set, db_update_json

DAYS_KEPT = 30


def _store():
    return db_get("stats", {})


def _save(store):
    db_set("stats", store)


def _today_key():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _chat_data(cid):
    return _store().get(str(cid), {"days": {}, "hours": {}, "names": {}, "commands": {}, "liza": {}, "moderation": {}})


def _ensure_chat(store, cid):
    chat = store.setdefault(str(cid), {})
    chat.setdefault("days", {})
    chat.setdefault("hours", {})
    chat.setdefault("names", {})
    chat.setdefault("commands", {})
    chat.setdefault("liza", {"requests": 0, "replies": 0})
    chat.setdefault("moderation", {})
    chat.setdefault("liza_users", {})
    return chat


def _trim(chat):
    days = chat["days"]
    hours = chat["hours"]
    if len(days) > DAYS_KEPT + 5:
        for old_day in sorted(days)[:-DAYS_KEPT]:
            days.pop(old_day, None)
            hours.pop(old_day, None)


def record_message(message):
    """Учёт сообщения отдельно по чату, пользователю, дню и часу."""
    try:
        if not message.from_user:
            return
        cid = str(message.chat.id)
        uid = str(message.from_user.id)
        name = message.from_user.first_name or message.from_user.username or uid
        now = datetime.now(TZ)
        day = now.strftime("%Y-%m-%d")
        hour = str(now.hour)

        def mutate(store):
            chat = _ensure_chat(store, cid)
            chat["names"][uid] = name
            day_bucket = chat["days"].setdefault(day, {})
            day_bucket[uid] = day_bucket.get(uid, 0) + 1
            hour_bucket = chat["hours"].setdefault(day, {})
            hour_bucket[hour] = hour_bucket.get(hour, 0) + 1
            _trim(chat)
            return store

        db_update_json("stats", mutate, {})
    except Exception:
        logging.exception("stats.record_message failed")


def record_command(cid, command):
    try:
        key = str(command).strip().lower()[:40]
        if not key: return
        def mutate(store):
            chat = _ensure_chat(store, cid)
            chat["commands"][key] = int(chat["commands"].get(key, 0)) + 1
            return store
        db_update_json("stats", mutate, {})
    except Exception as e:
        logging.error(f"[record_command] {e}", exc_info=True)


def record_liza_request(cid, user_id=None):
    try:
        def mutate(store):
            chat = _ensure_chat(store, cid)
            chat["liza"]["requests"] = int(chat["liza"].get("requests", 0)) + 1
            if user_id is not None:
                u = chat["liza_users"].setdefault(str(user_id), {"requests": 0, "replies": 0})
                u["requests"] = int(u.get("requests", 0)) + 1
            return store
        db_update_json("stats", mutate, {})
    except Exception as e:
        logging.error(f"[record_liza_request] {e}", exc_info=True)


def record_liza_response(cid, user_id=None):
    try:
        def mutate(store):
            chat = _ensure_chat(store, cid)
            chat["liza"]["replies"] = int(chat["liza"].get("replies", 0)) + 1
            if user_id is not None:
                u = chat["liza_users"].setdefault(str(user_id), {"requests": 0, "replies": 0})
                u["replies"] = int(u.get("replies", 0)) + 1
            return store
        db_update_json("stats", mutate, {})
    except Exception as e:
        logging.error(f"[record_liza_response] {e}", exc_info=True)


def record_moderation(cid, action):
    try:
        def mutate(store):
            chat = _ensure_chat(store, cid)
            chat["moderation"][action] = int(chat["moderation"].get(action, 0)) + 1
            return store
        db_update_json("stats", mutate, {})
    except Exception as e:
        logging.error(f"[record_moderation] {e}", exc_info=True)



def _period_days(period):
    return {1: 1, 7: 7, 30: 30}.get(int(period), 7)


def _period_keys(days):
    today = datetime.now(TZ).date()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


def _aggregate_users(chat, days):
    result = {}
    for day in _period_keys(days):
        for uid, count in chat.get("days", {}).get(day, {}).items():
            result[uid] = result.get(uid, 0) + int(count)
    return result


def _aggregate_total(chat, days):
    return sum(_aggregate_users(chat, days).values())


def _hourly(chat, days):
    result = [0] * 24
    for day in _period_keys(days):
        for hour, count in chat.get("hours", {}).get(day, {}).items():
            try:
                result[int(hour)] += int(count)
            except (ValueError, TypeError, IndexError):
                pass
    return result


def _target_user_id(message, args_text=""):
    raw = (args_text or "").strip()
    if getattr(message, "reply_to_message", None) and not raw:
        return str(message.reply_to_message.from_user.id), message.reply_to_message.from_user.first_name or "пользователь"
    if raw.startswith("@"):
        username = raw.split()[0].lstrip("@").lower()
        try:
            member = bot.get_chat_member(message.chat.id, username)
            u = member.user
            return str(u.id), u.first_name or u.username or username
        except Exception:
            return None, username
    if raw.isdigit():
        return raw, raw
    return None, None


def user_stats_text(cid, uid, name=None, days=30):
    chat = _chat_data(cid)
    totals = _aggregate_users(chat, days)
    count = totals.get(str(uid), 0)
    if name is None:
        name = chat.get("names", {}).get(str(uid), str(uid))
    total = sum(totals.values())
    share = (count / total * 100) if total else 0
    return f"👤 <b>{name}</b>\nСообщений за {days} дн.: <b>{count}</b>\nДоля активности: <b>{share:.1f}%</b>"


def build_activity_chart(cid, chat_title="Чат", period=7):
    """PNG: динамика сообщений, топ участников и активность по часам за выбранный период."""
    days = _period_days(period)
    chat = _chat_data(cid)
    day_keys = _period_keys(days)
    totals_per_day = [sum(chat.get("days", {}).get(d, {}).values()) for d in day_keys]
    combined = _aggregate_users(chat, days)
    names = chat.get("names", {})
    top = sorted(combined.items(), key=lambda x: -x[1])[:8]
    top_labels = [names.get(uid, uid)[:14] for uid, _ in top] or ["нет данных"]
    top_values = [cnt for _, cnt in top] or [0]
    hourly = _hourly(chat, days)

    if not any(totals_per_day) and not any(top_values):
        return None

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 9))
    fig.patch.set_facecolor("#1e1f29")
    for ax in (ax1, ax2, ax3):
        ax.set_facecolor("#1e1f29")
        ax.tick_params(colors="#e6e6f0", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#3a3b4a")
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_color("#e6e6f0")

    labels = [d[5:] for d in day_keys]
    ax1.plot(labels, totals_per_day, marker="o", color="#ff5fa2", linewidth=2)
    ax1.set_title(f"Сообщения за {days} дн.", color="#ffffff", fontsize=11)
    ax1.set_ylabel("сообщений", color="#e6e6f0", fontsize=9)
    ax1.tick_params(axis="x", rotation=45)

    bars = ax2.barh(top_labels[::-1], top_values[::-1], color="#7c5cff")
    ax2.set_title(f"Топ участников — {chat_title}"[:50], color="#ffffff", fontsize=11)
    ax2.set_xlabel("сообщений", color="#e6e6f0", fontsize=9)
    for bar, val in zip(bars, top_values[::-1]):
        ax2.text(bar.get_width() + max(1, max(top_values) * .01), bar.get_y() + bar.get_height() / 2, str(val), va="center", color="#e6e6f0", fontsize=8)

    ax3.bar(range(24), hourly, color="#53c7ff")
    ax3.set_title("Активность по часам", color="#ffffff", fontsize=11)
    ax3.set_xlabel("час по Киеву", color="#e6e6f0", fontsize=9)
    ax3.set_ylabel("сообщений", color="#e6e6f0", fontsize=9)
    ax3.set_xticks(range(24))

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def _keyboard():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("24 часа", callback_data="stats:1"),
        InlineKeyboardButton("7 дней", callback_data="stats:7"),
        InlineKeyboardButton("30 дней", callback_data="stats:30"),
    )
    return kb


def _overview(cid, period=7):
    chat = _chat_data(cid)
    users = _aggregate_users(chat, period)
    total = sum(users.values())
    active = len([x for x in users.values() if x])
    names = chat.get("names", {})
    top = sorted(users.items(), key=lambda x: -x[1])[:5]
    lines = [f"📊 <b>Статистика за {period} дн.</b>", f"Сообщений: <b>{total}</b>", f"Активных участников: <b>{active}</b>"]
    if top:
        lines.append("\n🏆 <b>Топ:</b>")
        for i, (uid, count) in enumerate(top, 1):
            lines.append(f"{i}. {names.get(uid, uid)[:25]} — {count}")
    liza = chat.get("liza", {})
    lines.append(f"\n🤖 Обращений к Лизе: <b>{liza.get('requests', 0)}</b> · ответов: <b>{liza.get('replies', 0)}</b>")
    commands = chat.get("commands", {})
    if commands:
        top_cmd = sorted(commands.items(), key=lambda x: -int(x[1]))[:5]
        lines.append("⌨️ <b>Команды:</b> " + ", ".join(f"{k}: {v}" for k,v in top_cmd))
    mod = chat.get("moderation", {})
    if mod:
        mod_total = sum(int(v) for v in mod.values())
        lines.append(f"🛡️ Модерационных действий: <b>{mod_total}</b>")
    return "\n".join(lines)


def send_stats(cid, chat_title="Чат", period=7, reply_to=None):
    chart = build_activity_chart(cid, chat_title, period)
    caption = _overview(cid, period)
    if chart is None:
        if reply_to:
            bot.reply_to(reply_to, caption + "\n\n📉 За этот период данных пока нет.", reply_markup=_keyboard())
        else:
            bot.send_message(cid, caption + "\n\n📉 За этот период данных пока нет.", reply_markup=_keyboard())
        return
    bot.send_photo(cid, chart, caption=caption, reply_to_message_id=getattr(reply_to, "message_id", None), reply_markup=_keyboard())


def cmd_stats(message, args_text=""):
    raw = (args_text or "").strip().lower()
    if raw in ("24", "24ч", "сутки", "день", "1"):
        period = 1
    elif raw in ("30", "30д", "месяц", "месяц"):
        period = 30
    else:
        period = 7
    if raw.startswith("моя") or raw.startswith("мой"):
        send = user_stats_text(message.chat.id, message.from_user.id, message.from_user.first_name, period if period != 7 else 30)
        bot.reply_to(message, send)
        return
    if raw.startswith("пользователь") or raw.startswith("юзер"):
        uid, name = _target_user_id(message, raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else "")
        if not uid:
            return bot.reply_to(message, "🤔 Укажи @username, ID или ответь на сообщение пользователя.")
        bot.reply_to(message, user_stats_text(message.chat.id, uid, name, period if period != 7 else 30))
        return
    send_stats(message.chat.id, message.chat.title or "Чат", period, message)


@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("stats:")))
def stats_callback(call):
    try:
        period = int(call.data.split(":", 1)[1])
        if period not in (1, 7, 30):
            period = 7
        bot.answer_callback_query(call.id, f"Статистика: {period} дн.")
        send_stats(call.message.chat.id, getattr(call.message.chat, "title", None) or "Чат", period)
    except Exception as e:
        logging.error(f"[stats_callback] {e}")
