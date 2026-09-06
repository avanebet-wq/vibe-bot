# -*- coding: utf-8 -*-
"""Статистика чата — учёт сообщений и настоящие картинки-графики (matplotlib)."""
import io
import time
import logging
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from runtime import bot
from config import TZ
from database import db_get, db_set

DAYS_KEPT = 30


def _store():
    return db_get("stats", {})


def _save(store):
    db_set("stats", store)


def _today_key():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def record_message(message):
    """Вызывается на каждое текстовое сообщение в группе."""
    try:
        cid = str(message.chat.id)
        uid = str(message.from_user.id)
        name = message.from_user.first_name or message.from_user.username or uid
        day = _today_key()

        store = _store()
        chat = store.setdefault(cid, {"days": {}, "names": {}})
        chat["names"][uid] = name
        day_bucket = chat["days"].setdefault(day, {})
        day_bucket[uid] = day_bucket.get(uid, 0) + 1

        # чистим старые дни, чтобы база не пухла
        if len(chat["days"]) > DAYS_KEPT + 5:
            for old_day in sorted(chat["days"].keys())[:-DAYS_KEPT]:
                chat["days"].pop(old_day, None)

        _save(store)
    except Exception as e:
        logging.error(f"[record_message] {e}")


def _chat_data(cid):
    store = _store()
    return store.get(str(cid), {"days": {}, "names": {}})


def build_activity_chart(cid, chat_title="Чат"):
    """Строит PNG-график: динамика сообщений за 14 дней + топ-8 участников. Возвращает BytesIO или None."""
    data = _chat_data(cid)
    days_map = data.get("days", {})
    names = data.get("names", {})

    if not days_map:
        return None

    last_days = sorted(days_map.keys())[-14:]
    totals_per_day = [sum(days_map[d].values()) for d in last_days]
    day_labels = [d[5:] for d in last_days]  # MM-DD

    # Топ пользователей за весь хранимый период
    combined = {}
    for d in days_map.values():
        for uid, cnt in d.items():
            combined[uid] = combined.get(uid, 0) + cnt
    top = sorted(combined.items(), key=lambda x: -x[1])[:8]
    top_labels = [names.get(uid, uid)[:12] for uid, _ in top]
    top_values = [cnt for _, cnt in top]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))
    fig.patch.set_facecolor("#1e1f29")

    for ax in (ax1, ax2):
        ax.set_facecolor("#1e1f29")
        ax.tick_params(colors="#e6e6f0", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#3a3b4a")

    ax1.plot(day_labels, totals_per_day, marker="o", color="#ff5fa2", linewidth=2)
    ax1.fill_between(range(len(day_labels)), totals_per_day, color="#ff5fa2", alpha=0.15)
    ax1.set_title("Активность по дням", color="#ffffff", fontsize=11)
    ax1.set_ylabel("сообщений", color="#e6e6f0", fontsize=9)
    ax1.tick_params(axis="x", rotation=45)

    bars = ax2.barh(top_labels[::-1], top_values[::-1], color="#7c5cff")
    ax2.set_title(f"Топ участников — {chat_title}"[:40], color="#ffffff", fontsize=11)
    ax2.set_xlabel("сообщений", color="#e6e6f0", fontsize=9)
    for bar, val in zip(bars, top_values[::-1]):
        ax2.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                  str(val), va="center", color="#e6e6f0", fontsize=8)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def cmd_stats(message):
    cid = message.chat.id
    chart = build_activity_chart(cid, message.chat.title or "Чат")
    if chart is None:
        return bot.reply_to(message, "📉 Пока маловато сообщений, чтобы построить график. Пишите больше!")
    bot.send_photo(
        cid, chart,
        caption="📊 Вот как выглядит жизнь этого чата в последнее время.",
        reply_to_message_id=message.message_id,
    )
