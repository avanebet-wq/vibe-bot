# -*- coding: utf-8 -*-
"""Тексты и клавиатуры меню настроек."""
from telebot import types

from utils import format_seconds
from settings_store import SYSTEM_MESSAGE_TYPES, WEEKDAYS, get_posts, get_post, get_captcha, get_deletion

CB = "cf"  # префикс callback_data, чтобы не путать с другими кнопками бота


def _cb(action, gid, *extra):
    parts = [CB, action, str(gid), *[str(e) for e in extra]]
    return "|".join(parts)


def _btn(text, action, gid, *extra):
    return types.InlineKeyboardButton(text, callback_data=_cb(action, gid, *extra))


def _kb(rows):
    kb = types.InlineKeyboardMarkup()
    for row in rows:
        kb.row(*row)
    return kb


# ------------------------------------------------------------------ ROOT ----

def root_text(chat_title):
    return (
        "📓 <b>ПАРАМЕТРЫ</b>\n"
        f"Группа: «{chat_title}»\n\n"
        "Выберите один из параметров которые хотите изменить👇"
    )


def root_kb(gid):
    return _kb([
        [_btn("🧠 Капча", "cap", gid)],
        [_btn("🕓 Повторяющиеся сообщения", "pst", gid)],
        [_btn("🗑️ Удаление сообщений", "del", gid)],
        [_btn("✅ Закрыть", "close", gid)],
    ])


# --------------------------------------------------------------- CAPTCHA ----

def captcha_text(gid):
    enabled = get_captcha(gid).get("enabled", False)
    status = "Вкл ✅" if enabled else "Выкл ❌"
    return (
        "🧠 <b>Капча</b>\n"
        "При активации капчи, когда пользователь входит в группу он не сможет "
        "отправлять сообщения, пока не подтвердит, что он не робот.\n\n"
        f"Статус: {status}"
    )


def captcha_kb(gid):
    enabled = get_captcha(gid).get("enabled", False)
    rows = []
    if enabled:
        rows.append([_btn("❌ Выключить", "capoff", gid)])
    else:
        rows.append([_btn("✅ Активировать", "capon", gid)])
    rows.append([_btn("⬅️ Назад", "back", gid, "root")])
    return _kb(rows)


# ------------------------------------------------------------------ POSTS ---

def posts_list_text(gid):
    posts = get_posts(gid)
    header = (
        "🕑 <b>Повторяющиеся сообщения</b>\n"
        "В этом меню вы можете настроить сообщения, которые будут отправляться "
        "в группе повторно каждые несколько минут/часов или каждые несколько сообщений.\n\n"
        "⏰ <b>Запланированные публикации:</b>\n"
    )
    if not posts:
        return header + "\n<i>пока нет ни одной публикации</i>"

    lines = []
    nums = "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣8️⃣9️⃣🔟"
    for pid, post in sorted(posts.items(), key=lambda x: int(x[0])):
        idx = int(pid) - 1
        emoji = nums[idx] if 0 <= idx < len(nums) else f"№{pid}"
        status = "Вкл ✅" if post.get("enabled") else "Выкл ❌"
        time_txt = post.get("time") or "не задано"
        rep_txt = format_seconds(post["interval_seconds"]) if post.get("interval_seconds") else "не задано"
        msg_txt = "установлено" if (post.get("text") or post.get("media")) else "не установлено"
        lines.append(
            f"🗯{emoji} • {status}\n"
            f" ├ Время: {time_txt}\n"
            f" ├ Каждые: {rep_txt}\n"
            f" └ Сообщение: {msg_txt}"
        )
    return header + "\n\n".join(lines)


def posts_list_kb(gid):
    posts = get_posts(gid)
    nums = "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣8️⃣9️⃣🔟"
    rows = [[_btn("➕ Добавить сообщение", "paddp", gid)]]
    for pid, post in sorted(posts.items(), key=lambda x: int(x[0])):
        idx = int(pid) - 1
        emoji = nums[idx] if 0 <= idx < len(nums) else f"№{pid}"
        toggle_emoji = "❌" if post.get("enabled") else "✅"
        rows.append([
            _btn(emoji, "popen", gid, pid),
            _btn(toggle_emoji, "ptoggle", gid, pid),
        ])
        rows.append([_btn("🗑️ Удалить публикацию", "pdel", gid, pid)])
    rows.append([_btn("⬅️ Назад", "back", gid, "root")])
    return _kb(rows)


def post_edit_text(gid, pid):
    post = get_post(gid, pid)
    status = "Вкл ✅" if post.get("enabled") else "Выкл ❌"
    time_txt = post.get("time") or "не задано"
    rep_txt = format_seconds(post["interval_seconds"]) if post.get("interval_seconds") else "не задано"
    pin_txt = "✔️" if post.get("pin") else "✖️"
    dellast_txt = "✔️" if post.get("delete_last") else "✖️"
    return (
        "🕑 <b>Повторяющиеся сообщения</b>\n\n"
        f"💡 Статус: {status}\n"
        f"🕑 Время: {time_txt}\n"
        f"🔁 Повторение: {rep_txt}\n"
        f"📌 Закрепить сообщение: {pin_txt}\n"
        f"♻️ Удалять последнее сообщение: {dellast_txt}"
    )


def post_edit_kb(gid, pid):
    post = get_post(gid, pid)
    pin_txt = "📌 Закрепить сообщение ✔️" if post.get("pin") else "📌 Закрепить сообщение ✖️"
    dellast_txt = "♻️ Удалять последнее ✔️" if post.get("delete_last") else "♻️ Удалять последнее ✖️"
    deltimer_txt = "♻️ Удалять по таймеру ✔️" if post.get("delete_timer_seconds") else "♻️ Удалять по таймеру ✖️"
    rows = [
        [_btn("✍️ Настроить сообщение", "ptxt", gid, pid), _btn("📸 Медиа", "pmedia", gid, pid)],
        [_btn("🔠 URL-кнопки", "pbtn", gid, pid)],
        [_btn("🕓 Время", "ptime", gid, pid), _btn("🔁 Повторение", "prep", gid, pid)],
        [_btn("🗓️ Дни недели", "pwd", gid, pid), _btn("📆 Дни месяца", "pmd", gid, pid)],
        [_btn("⏱️ Установить время", "pauto", gid, pid)],
        [_btn("⏲️ Дата начала", "psdate", gid, pid), _btn("⏲️ Дата окончания", "pedate", gid, pid)],
        [_btn(pin_txt, "ppin", gid, pid)],
        [_btn(dellast_txt, "pdellast", gid, pid)],
        [_btn(deltimer_txt, "pdeltimer", gid, pid)],
        [_btn("👀 Полный предпросмотр", "pprev", gid, pid)],
        [_btn("📂 Выбрать тему", "ptopic", gid, pid)],
        [_btn("⬅️ Назад", "back", gid, "pst")],
    ]
    return _kb(rows)


def text_prompt_kb(gid, pid, has_value):
    rows = []
    if has_value:
        rows.append([_btn("🚫 Удалить сообщение", "ptxtdel", gid, pid)])
    rows.append([_btn("❌ Отмена", "ptxtcancel", gid, pid)])
    return _kb(rows)


def media_prompt_kb(gid, pid, has_value):
    rows = []
    if has_value:
        rows.append([_btn("🚫 Удалить сообщение", "pmediadel", gid, pid)])
    rows.append([_btn("❌ Отмена", "pmediacancel", gid, pid)])
    return _kb(rows)


def buttons_prompt_kb(gid, pid, has_value):
    rows = []
    if has_value:
        rows.append([_btn("🚫 Удалить сообщение", "pbtndel", gid, pid)])
    rows.append([_btn("❌ Отмена", "pbtncancel", gid, pid)])
    return _kb(rows)


def weekdays_kb(gid, pid):
    post = get_post(gid, pid)
    picked = set(post.get("weekdays", []))
    row = []
    for key, label in WEEKDAYS:
        mark = "✅" if key in picked else "▫️"
        row.append(_btn(f"{mark} {label}", "pwdt", gid, pid, key))
    rows = [row[:4], row[4:]]
    rows.append([_btn("⬅️ Назад", "back", gid, "popen", pid)])
    return _kb(rows)


def monthdays_kb(gid, pid):
    post = get_post(gid, pid)
    picked = set(str(d) for d in post.get("monthdays", []))
    rows = []
    row = []
    for d in range(1, 32):
        mark = "✅" if str(d) in picked else str(d)
        row.append(_btn(mark, "pmdt", gid, pid, d))
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_btn("⬅️ Назад", "back", gid, "popen", pid)])
    return _kb(rows)


def back_kb(gid, target, pid=None):
    if pid:
        return _kb([[_btn("⬅️ Назад", "back", gid, target, pid)]])
    return _kb([[_btn("⬅️ Назад", "back", gid, target)]])


# -------------------------------------------------------------- DELETION ----

def deletion_text():
    return "🗑 <b>Удаление сообщений</b>\nКакие сообщения вы хотите удалить с помощью бота Лиза?"


def deletion_kb(gid):
    return _kb([
        [_btn("🤫 Полная тишина", "delsil", gid)],
        [_btn("💭 Системные сообщения", "delsys", gid)],
        [_btn("🤯 Массовое удаление", "delmass", gid)],
        [_btn("⬅️ Назад", "back", gid, "root")],
    ])


def silence_text(gid):
    enabled = get_deletion(gid).get("silence", False)
    status = "Вкл ✅" if enabled else "Выкл ❌"
    return (
        "🤫 <b>Полная тишина</b>\n"
        "Любое сообщение от пользователей (кроме Админов, Модераторов и Свободных) "
        "будет удалено.\n\n"
        f"Состояние: {status}"
    )


def silence_kb(gid):
    enabled = get_deletion(gid).get("silence", False)
    label = "❌ Выкл" if enabled else "✅ Вкл"
    return _kb([
        [_btn(label, "delsiltg", gid)],
        [_btn("⬅️ Назад", "back", gid, "del")],
    ])


def sysmsgs_text():
    return "💭 <b>Системные сообщения</b>\nВыберите, какие из них удалять автоматически:"


def sysmsgs_kb(gid):
    system = get_deletion(gid).get("system", {})
    rows = []
    for key, label in SYSTEM_MESSAGE_TYPES:
        on = system.get(key, False)
        status_btn = types.InlineKeyboardButton(
            "✅" if on else "❌", callback_data=_cb("delsystg", gid, key)
        )
        name_btn = types.InlineKeyboardButton(label, callback_data=_cb("noop", gid))
        rows.append([name_btn, status_btn])
    rows.append([_btn("⬅️ Назад", "back", gid, "del")])
    return _kb(rows)


def massdel_text():
    return (
        "🤯 <b>Массовое удаление</b>\n"
        "Удаляет последние сообщения в группе, которые Лиза видела и сохранила "
        "у себя (учитываются только сообщения, отправленные после её добавления в чат). "
        "Действие необратимо — используйте с осторожностью."
    )


def massdel_kb(gid):
    return _kb([
        [_btn("🗑️ УДАЛИТЬ ВСЕ 🗑️", "delmasscf", gid)],
        [_btn("⬅️ Назад", "back", gid, "del")],
    ])


# ------------------------------------------------------------ ADD-TO-GROUP --

def where_open_kb(gid):
    return _kb([
        [_btn("📍 Открыть здесь", "opnhere", gid), _btn("✉️ Открыть в личке бота", "opnpm", gid)],
    ])


def deeplink_kb(bot_username, gid):
    url = f"https://t.me/{bot_username}?start=cfg-{gid}"
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("✉️ Открыть личку с ботом", url=url))
    return kb
