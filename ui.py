# -*- coding: utf-8 -*-
"""VIBE Bot — ui module."""
from runtime import *
from core import *
from general import *

def format_seconds_human(secs):
    if not secs or secs <= 0: return "выкл"
    h, rem = divmod(int(secs), 3600)
    m_, s_ = divmod(rem, 60)
    if h: return f"{h}ч" + (f" {m_}м" if m_ else "")
    if m_: return f"{m_}м"
    return f"{s_}с"

def chats_selection_kb():
    cache = db_get("chats_cache", {"-1004374303475": "Основная VIBE", "-1003514059820": "Вторая группа"})
    kb = types.InlineKeyboardMarkup(row_width=1)
    for cid_str, cname in cache.items():
        kb.add(types.InlineKeyboardButton(f"📢 {cname}", callback_data=f"ap:chat:{cid_str}"))
    return kb

def main_kb(cid, is_pv):
    intervene = get_v(cid, "intervene", True)
    del_sys = get_v(cid, "del_sys", False)
    freq = get_v(cid, "freq", 40)
    anger = get_v(cid, "anger", 40)
    random_reactions = get_v(cid, "random_reactions", True)
    butt_in = get_v(cid, "butt_in", False)
    butt_in_chance = get_v(cid, "butt_in_chance", 15)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"💬 Отвечать в чате: {'✅' if intervene else '❌'}", callback_data="m:toggle_intervene"))
    kb.add(types.InlineKeyboardButton(f"🗣 Встревать в диалог: {'✅' if butt_in else '❌'}", callback_data="m:toggle_butt_in"))
    if butt_in:
        kb.add(types.InlineKeyboardButton(f"🎚 Шанс вмешаться: {butt_in_chance}%", callback_data="m:butt_in_chance"))
    kb.add(types.InlineKeyboardButton(f"🔥 Случайные реакции: {'✅' if random_reactions else '❌'}", callback_data="m:toggle_reactions"))
    kb.add(types.InlineKeyboardButton(f"🗑 Чистить системку: {'✅' if del_sys else '❌'}", callback_data="m:toggle_sys"))
    kb.add(types.InlineKeyboardButton(f"📊 Частота ответов: {freq}%", callback_data="m:freq"))
    kb.add(types.InlineKeyboardButton(f"😠 Токсичность: {anger}%", callback_data="m:anger"))
    if is_pv:
        kb.add(types.InlineKeyboardButton("📢 Автопостинг", callback_data="m:autopost_list"))
    else:
        kb.add(types.InlineKeyboardButton("📢 Автопостинг (в ЛС)", callback_data="to_group_settings"))
    return kb

def autopost_list_kb(target_cid):
    data = db_get("autopost", {"posts": []})
    posts = [p for p in data.get("posts", []) if str(p.get("chat_id")) == str(target_cid)]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for p in posts:
        status = "✅" if p.get("enabled") else "⏸"
        kb.add(types.InlineKeyboardButton(trim_btn_text(f"{status} {p.get('name', 'Пост')}"), callback_data=f"ap:select:{p['id']}"))
    kb.add(types.InlineKeyboardButton("➕ Создать пост", callback_data=f"ap:create:{target_cid}"))
    if posts:
        kb.add(types.InlineKeyboardButton("🗑 Удалить пост", callback_data=f"ap:delmenu:{target_cid}"))
    kb.add(types.InlineKeyboardButton("« Назад", callback_data="m:autopost_list"))
    return kb

def post_text_view(pid):
    data = db_get("autopost", {"posts": []})
    post = next((p for p in data.get("posts", []) if p["id"] == pid), None)
    if not post: return "⚠️ Пост не найден (возможно, был удалён)."
    raw_text = (post.get("text") or "").strip()
    preview = html.escape(raw_text[:200]) + ("…" if len(raw_text) > 200 else "") if raw_text else "<i>пусто</i>"
    if post.get("interval"): schedule = f"каждые {format_seconds_human(post.get('interval', 0))}"
    elif post.get("daily_time"): schedule = f"ежедневно в {post.get('daily_time')}"
    else: schedule = "выключено"
    status_icon = "🟢" if post.get("enabled") else "🔴"
    status_text = "включён" if post.get("enabled") else "выключен"
    return (
        "━━━━━━━VIBE━━━━━━━\n"
        f"⚙️ <b>{html.escape(post.get('name', 'Пост'))}</b>\n"
        "───────────────\n\n"
        f"{status_icon} Статус: <b>{status_text}</b>\n"
        f"🕒 Расписание: {schedule}\n"
        f"📅 Старт: {post.get('start_date') or 'сразу'}\n"
        f"🖼 Фото: {'есть ✅' if post.get('photo') else 'нет ❌'}\n"
        f"🔘 Кнопок: {len(post.get('buttons', []))}\n"
        f"🧹 Автоудаление предыдущего: {'вкл ✅' if post.get('auto_delete_prev') else 'выкл ❌'}\n"
        f"📌 Закреплять после отправки: {'вкл ✅' if post.get('pin_after_send') else 'выкл ❌'}\n\n"
        "───────────────\n"
        f"📝 <b>Текст поста:</b>\n{preview}\n"
        "━━━━━━━VIBE━━━━━━━"
    )

def post_settings_kb(pid):
    data = db_get("autopost", {"posts": []})
    post = next((p for p in data.get("posts", []) if p["id"] == pid), None)
    chat_id = str(post.get("chat_id")) if post else "-1004374303475"
    kb = types.InlineKeyboardMarkup(row_width=2)
    if not post:
        kb.add(types.InlineKeyboardButton("« Назад", callback_data=f"ap:chat:{chat_id}"))
        return kb
    kb.add(
        types.InlineKeyboardButton(f"{'✅' if post.get('enabled') else '❌'} Включен", callback_data=f"ap:toggle:{pid}"),
        types.InlineKeyboardButton(f"🗑 Автоудаление: {'вкл' if post.get('auto_delete_prev') else 'выкл'}", callback_data=f"ap:autodel:{pid}")
    )
    kb.add(
        types.InlineKeyboardButton(f"📌 Закреплять: {'вкл' if post.get('pin_after_send') else 'выкл'}", callback_data=f"ap:pin:{pid}")
    )
    kb.add(
        types.InlineKeyboardButton("⏱ Интервал", callback_data=f"ap:int_menu:{pid}"),
        types.InlineKeyboardButton("🕑 Время", callback_data=f"ap:time_menu:{pid}")
    )
    kb.add(
        types.InlineKeyboardButton("📅 Дата старта", callback_data=f"ap:date_menu:{pid}"),
        types.InlineKeyboardButton("🖼 Фото", callback_data=f"ap:photo_menu:{pid}")
    )
    kb.add(
        types.InlineKeyboardButton("📝 Текст", callback_data=f"ap:text:{pid}"),
        types.InlineKeyboardButton(f"🔘 Кнопки ({len(post.get('buttons', []))})", callback_data=f"ap:btns_menu:{pid}")
    )
    kb.add(
        types.InlineKeyboardButton("🚀 Отправить сейчас", callback_data=f"ap:send:{pid}"),
        types.InlineKeyboardButton("👁 Превью", callback_data=f"ap:preview:{pid}")
    )
    kb.add(types.InlineKeyboardButton("« Назад к списку", callback_data=f"ap:chat:{chat_id}"))
    return kb

def build_post_user_kb(post):
    buttons = post.get("buttons") or []
    if not buttons: return None
    kb = types.InlineKeyboardMarkup(row_width=max((len(r) for r in buttons), default=1))
    for row in buttons:
        btn_row = []
        for btn in row:
            text = trim_btn_text(btn.get("text", "Кнопка"))
            if btn.get("url"):
                btn_row.append(types.InlineKeyboardButton(text, url=btn["url"]))
            elif btn.get("command"):
                btn_row.append(types.InlineKeyboardButton(text, callback_data=f"cmd_exec_{btn['command']}"))
            elif btn.get("callback_data"):
                btn_row.append(types.InlineKeyboardButton(text, callback_data=btn["callback_data"]))
        if btn_row: kb.row(*btn_row)
    return kb
