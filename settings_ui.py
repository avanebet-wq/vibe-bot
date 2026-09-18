# -*- coding: utf-8 -*-
"""Тексты и клавиатуры меню настроек."""
from telebot import types

from utils import format_seconds
from settings_store import SYSTEM_MESSAGE_TYPES, WEEKDAYS, get_posts, get_post, get_captcha, get_deletion, get_liza

_CAP_TYPE_LABELS = {
    "button": "Кнопка «Я не робот» в группе",
    "subscribe": "Проверка подписки на канал",
}

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
        "⚙️ <b>НАСТРОЙКИ</b>\n"
        f"Группа: «{chat_title}»\n\n"
        "Выберите, какие настройки открыть:"
    )


def root_kb(gid):
    return _kb([
        [_btn("👅 Настройки Лизы", "settings_liza", gid)],
        [_btn("⚙️ Настройки чата", "settings_chat", gid)],
        [_btn("🛡️ Безопасность", "security", gid)],
        [_btn("✅ Закрыть", "close", gid)],
    ])


def liza_settings_text(chat_title):
    return (
        "👅 <b>НАСТРОЙКИ ЛИЗЫ</b>\n"
        f"Группа: «{chat_title}»\n\n"
        "Здесь находятся все текущие настройки поведения и возможностей Лизы.\n\n"
        "👇 Выберите раздел:"
    )


def liza_settings_kb(gid):
    return _kb([
        [_btn("👅 Ответы и активность", "liza", gid)],
        [_btn("🧠 Память", "mem", gid), _btn("🎭 Характер", "pers", gid)],
        [_btn("🛡️ Модерация", "mod", gid)],
        [_btn("🎮 Развлечения", "fun", gid)],
        [_btn("⚙️ Функции чата", "chat", gid)],
        [_btn("📊 Статус Лизы", "status", gid)],
        [_btn("🔄 Сброс настроек", "reset", gid)],
        [_btn("⬅️ Выбор настроек", "back", gid, "root")],
    ])


def chat_settings_text(chat_title):
    return (
        "⚙️ <b>НАСТРОЙКИ ЧАТА</b>\n"
        f"Группа: «{chat_title}»\n\n"
        "Здесь находятся автоматические функции самого чата.\n\n"
        "👇 Выберите раздел:"
    )


def chat_settings_kb(gid):
    return _kb([
        [_btn("🕑 Повторяющиеся сообщения", "pst", gid)],
        [_btn("🗑️ Удаление сообщений", "del", gid)],
        [_btn("⬅️ Выбор настроек", "back", gid, "root")],
    ])


# ------------------------------------------------------------ SECURITY ----

def security_text(chat_title):
    return (
        "🛡️ <b>БЕЗОПАСНОСТЬ</b>\n"
        f"Группа: «{chat_title}»\n\n"
        "Здесь находятся функции защиты чата.\n\n"
        "👇 Выберите раздел:"
    )


def security_kb(gid):
    return _kb([
        [_btn("🧠 Капча", "cap", gid)],
        [_btn("⬅️ Выбор настроек", "back", gid, "root")],
    ])


# --------------------------------------------------------- LIZA BEHAVIOUR --

def _on(v):
    return "вкл ✅" if bool(v) else "выкл ❌"

def liza_text(gid):
    l = get_liza(gid)
    mode = {"everyone": "Отвечает всем", "mention": "Только по обращению", "silent": "Полностью молчит"}.get(l.get("reply_mode"), "Не задано")
    chance = int(round(float(l.get("chatter_chance", 0.05)) * 100))
    return (
        "👅 <b>Ответы и активность</b>\n\n"
        f"💬 Ответы: <b>{mode}</b>\n"
        f"🎲 Самостоятельная активность: <b>{_on(l.get('autoactivity'))}</b>\n"
        f"📈 Вероятность вмешаться: <b>{chance}%</b>\n"
        f"😴 Режим сна: {'вкл ✅' if __import__('mood').is_asleep(gid) else 'выкл ❌'}\n\n"
        "Кнопки ниже меняют настройки без лишних сообщений в чате."
    )

def liza_kb(gid):
    l = get_liza(gid)
    mode = l.get("reply_mode", "everyone")
    rows = [
        [_btn(("✅ " if mode == "everyone" else "▫️ ") + "Отвечать всем", "reply", gid, "everyone")],
        [_btn(("✅ " if mode == "mention" else "▫️ ") + "Только при обращении", "reply", gid, "mention")],
        [_btn(("✅ " if mode == "silent" else "▫️ ") + "Полная тишина", "reply", gid, "silent")],
        [_btn(("🟢 " if l.get("autoactivity") else "⚪ ") + "Автоактивность", "liza_toggle", gid, "autoactivity")],
        [_btn("📈 Уровень активности", "chance", gid)],
        [_btn("😴 Сон", "sleep", gid)],
        [_btn("⬅️ Назад", "back", gid, "root")],
    ]
    return _kb(rows)

def chance_text(gid):
    chance = int(round(float(get_liza(gid).get("chatter_chance", 0.05)) * 100))
    return ("📈 <b>Уровень активности</b>\n\n"
            "Это шанс, с которым Лиза сама вмешается в обычный разговор.\n\n"
            f"Текущее значение: <b>{chance}%</b>\n\n"
            "При активном разговоре интеллектуальная автоактивность дополнительно снижает шанс вмешательства.")

def chance_kb(gid):
    cur = int(round(float(get_liza(gid).get("chatter_chance", 0.05)) * 100))
    vals = [0, 2, 5, 10, 20, 35]
    rows=[]
    for i in range(0, len(vals), 3):
        rows.append([_btn(("✅ " if v == cur else "") + f"{v}%", "chance_set", gid, v) for v in vals[i:i+3]])
    rows.append([_btn("⬅️ Назад", "back", gid, "liza")])
    return _kb(rows)

def sleep_text(gid):
    import time
    from mood import sleep_until
    until = sleep_until(gid)
    status = "активен"
    if until > time.time():
        status = "до " + __import__('datetime').datetime.fromtimestamp(until).strftime("%d.%m %H:%M")
    return ("😴 <b>Режим сна</b>\n\n"
            f"Сейчас: <b>{status}</b>\n"
            "Во сне Лиза не отвечает на обычные сообщения. Команды управления остаются доступны.")

def sleep_kb(gid):
    return _kb([
        [_btn("😴 1 час", "sleep_set", gid, 3600), _btn("😴 6 часов", "sleep_set", gid, 21600)],
        [_btn("😴 24 часа", "sleep_set", gid, 86400)],
        [_btn("☀️ Разбудить", "sleep_set", gid, 0)],
        [_btn("⬅️ Назад", "back", gid, "liza")],
    ])


# ---------------------------------------------------------------- MEMORY --
def memory_text(gid):
    from utils import get_setting
    enabled = bool(get_setting(gid, "memory_enabled", True))
    return ("🧠 <b>Память</b>\n\n"
            f"Запоминание новых фактов: <b>{_on(enabled)}</b>\n"
            "Память хранится отдельно для каждого пользователя и чата.\n"
            "Отключение не удаляет уже сохранённые факты.")

def memory_kb(gid):
    from utils import get_setting
    enabled = bool(get_setting(gid, "memory_enabled", True))
    return _kb([
        [_btn(("❌ Выключить память" if enabled else "✅ Включить память"), "mem_toggle", gid)],
        [_btn("🧹 Очистить всю память чата", "mem_clear_confirm", gid)],
        [_btn("⬅️ Назад", "back", gid, "root")],
    ])

# -------------------------------------------------------------- FUN ------
def fun_text(gid):
    l=get_liza(gid); enabled=bool(l.get("minigames", True)); stories=bool(l.get("stories", True))
    return ("🎮 <b>Развлечения</b>\n\n"
            f"Мини-игры: <b>{_on(enabled)}</b>\n"
            f"Автоистории: <b>{_on(stories)}</b>\n"
            "Игра «Выпить»: <b>доступна вместе с мини-играми</b>\n\n"
            "Выкл. мини-игр блокирует игровые команды. Ручная команда «расскажи историю» остаётся доступной.")

def fun_kb(gid):
    l=get_liza(gid)
    return _kb([
        [_btn(("🟢 " if l.get("minigames") else "⚪ ")+"Мини-игры", "liza_toggle", gid, "minigames")],
        [_btn(("🟢 " if l.get("stories") else "⚪ ")+"Автоистории", "liza_toggle", gid, "stories")],
        [_btn("⬅️ Назад", "back", gid, "root")],
    ])

# --------------------------------------------------------------- CHAT -----
def chat_text(gid):
    from utils import get_setting
    l=get_liza(gid)
    return ("⚙️ <b>ФУНКЦИИ ЧАТА</b>\n\n"
            f"🎩 Вежливый стиль: <b>{_on(l.get('polite'))}</b>\n"
            f"😠 Злой режим: <b>{_on(l.get('angry'))}</b>\n"
            f"📖 Автоистории: <b>{_on(l.get('stories'))}</b>\n"
            f"🧹 Удаление нарушений: <b>{_on(get_setting(gid,'auto_delete',False))}</b>\n"
            f"🛡 Защита админов: <b>{_on(get_setting(gid,'protect_admins',True))}</b>")

def chat_kb(gid):
    l=get_liza(gid)
    return _kb([
        [_btn(("🟢 " if l.get("polite") else "⚪ ")+"Вежливый стиль", "liza_toggle", gid, "polite")],
        [_btn(("🟢 " if l.get("angry") else "⚪ ")+"Злой режим", "liza_toggle", gid, "angry")],
        [_btn("🛡️ Открыть модерацию", "mod", gid)],
        [_btn("🎭 Открыть характер", "pers", gid)],
        [_btn("⬅️ Назад", "back", gid, "root")],
    ])

def mod_text(gid):
    from moderation import _chat_bucket
    cfg=_chat_bucket(gid)["config"]
    return ("🛡️ <b>МОДЕРАЦИЯ</b>\n\n"
            f"⚠️ Лимит варнов: <b>{cfg['warn_limit']}</b>\n"
            f"⚡ Автодействие: <b>{cfg['warn_action']}</b>\n"
            f"🧹 Удаление нарушений: <b>{_on(cfg['auto_delete'])}</b>\n"
            f"👑 Защита админов: <b>{_on(cfg['protect_admins'])}</b>")

def mod_kb(gid):
    from moderation import _chat_bucket
    cfg=_chat_bucket(gid)["config"]
    actions=[("mute","🔇 Мут"),("ban","🔨 Бан"),("kick","👢 Кик")]
    return _kb([
        [_btn(("✅ " if cfg['auto_delete'] else "❌ ")+"Удалять нарушения", "modtoggle", gid, "auto_delete"),
         _btn(("✅ " if cfg['protect_admins'] else "❌ ")+"Защищать админов", "modtoggle", gid, "protect_admins")],
        [_btn(f"⚠️ Лимит: {cfg['warn_limit']}", "warnlimit", gid)],
        [_btn(("✅ " if cfg['warn_action']==a else "▫️ ")+label, "warnaction", gid, a) for a,label in actions],
        [_btn("⬅️ Назад", "back", gid, "root")],
    ])

_PERS_LABELS = {
    "humor": "Юмор", "sarcasm": "Сарказм", "friendliness": "Доброта",
    "rudeness": "Грубость", "seriousness": "Серьёзность", "verbosity": "Разговорчивость",
}

def personality_text(gid):
    from chat_personality import get
    p=get(gid)
    return ("🎭 <b>Характер Лизы</b>\n\n" + "\n".join(f"• {_PERS_LABELS.get(k,k)}: <b>{v}</b>/100" for k,v in p.items()) +
            "\n\nКаждая кнопка меняет параметр на 5 пунктов. Диапазон: 0–100.")

def personality_kb(gid):
    from chat_personality import get
    p=get(gid); rows=[]
    for key, value in p.items():
        label=_PERS_LABELS.get(key,key)
        rows.append([_btn("−5", "pers_adj", gid, key, -5), _btn(f"{label}: {value}", "noop", gid), _btn("+5", "pers_adj", gid, key, 5)])
    rows.append([_btn("⬅️ Назад", "back", gid, "root")])
    return _kb(rows)

def status_text(gid):
    l=get_liza(gid)
    mode={"everyone":"всем","mention":"только при обращении","silent":"полная тишина"}.get(l.get("reply_mode"),"—")
    from utils import get_setting
    return ("📊 <b>СТАТУС ЛИЗЫ</b>\n\n"
            f"💬 Ответы: <b>{mode}</b>\n"
            f"🎲 Автоактивность: <b>{_on(l.get('autoactivity'))}</b>\n"
            f"📈 Шанс вмешательства: <b>{int(round(float(l.get('chatter_chance',.05))*100))}%</b>\n"
            f"🧠 Память: <b>{_on(get_setting(gid,'memory_enabled',True))}</b>\n"
            f"🎮 Мини-игры: <b>{_on(l.get('minigames'))}</b>\n"
            f"📖 Автоистории: <b>{_on(l.get('stories'))}</b>\n"
            f"🎩 Вежливость: <b>{_on(l.get('polite'))}</b>\n"
            f"😠 Злость: <b>{_on(l.get('angry'))}</b>")

def status_kb(gid):
    return _kb([[_btn("🔄 Обновить", "status", gid)],[_btn("⬅️ Назад", "back", gid, "root")]])

def reset_text():
    return ("🔄 <b>СБРОС НАСТРОЕК</b>\n\n"
            "Сброс вернёт настройки Лизы к безопасным значениям по умолчанию.\n"
            "Посты, капча и накопленные данные пользователей не удаляются.")

def reset_kb(gid):
    return _kb([[_btn("✅ Сбросить настройки Лизы", "reset_confirm", gid)],[_btn("⬅️ Назад", "back", gid, "root")]])


# --------------------------------------------------------------- CAPTCHA ----

def captcha_text(gid):
    captcha = get_captcha(gid)
    enabled = captcha.get("enabled", False)
    status = "Вкл ✅" if enabled else "Выкл ❌"
    ctype = captcha.get("type", "button")
    type_label = _CAP_TYPE_LABELS.get(ctype, ctype)
    if ctype == "subscribe":
        type_label += f" ({captcha.get('channel_title') or 'канал не выбран'})"
    return (
        "🧠 <b>Капча</b>\n"
        "При активации капчи новые участники не смогут писать в чат, "
        "пока не пройдут проверку, что они не роботы.\n\n"
        f"Статус: {status}\n"
        f"Тип: {type_label}"
    )


def captcha_kb(gid):
    enabled = get_captcha(gid).get("enabled", False)
    rows = []
    if enabled:
        rows.append([_btn("❌ Выключить капчу", "capoff", gid)])
    else:
        rows.append([_btn("✅ Включить капчу", "capon", gid)])
    rows.append([_btn("🎚️ Тип капчи", "cap_type", gid)])
    rows.append([_btn("⬅️ Назад", "back", gid, "security")])
    return _kb(rows)


def cap_type_text(gid):
    captcha = get_captcha(gid)
    ctype = captcha.get("type", "button")
    if ctype == "subscribe":
        current = f"Проверка подписки на канал: <b>{captcha.get('channel_title') or 'не выбран'}</b>"
    else:
        current = "Кнопка «Я не робот» в группе"
    return (
        "🎚️ <b>Тип капчи</b>\n\n"
        f"Сейчас выбрано: {current}\n\n"
        "1️⃣ <b>Кнопка «Я не робот»</b> — при входе в группу Лиза упомянет пользователя "
        "и попросит нажать кнопку прямо в группе.\n"
        "2️⃣ <b>Проверка подписки на канал</b> — писать в чат смогут только подписчики "
        "выбранного канала."
    )


def cap_type_kb(gid):
    captcha = get_captcha(gid)
    ctype = captcha.get("type", "button")
    rows = [
        [_btn(("✅ " if ctype == "button" else "▫️ ") + "Кнопка «Я не робот»", "cap_type_set", gid, "button")],
        [_btn(("✅ " if ctype == "subscribe" else "▫️ ") + "Подписка на канал", "cap_channels", gid)],
        [_btn("⬅️ Назад", "back", gid, "cap")],
    ]
    return _kb(rows)


def channels_text(has_channels):
    if not has_channels:
        return (
            "📢 <b>Выбор канала</b>\n\n"
            "Каналов не найдено.\n\n"
            "Чтобы канал появился в этом списке, добавьте меня в него "
            "администратором — я покажу только каналы, где вы создатель "
            "или администратор."
        )
    return (
        "📢 <b>Выбор канала</b>\n\n"
        "Выберите канал, подписку на который нужно будет проверять:"
    )


def channels_kb(gid, channels):
    rows = []
    for cid, title in channels:
        rows.append([_btn(f"📢 {title}", "cap_channel_set", gid, cid)])
    rows.append([_btn("⬅️ Назад", "back", gid, "cap_type")])
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
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
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
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
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
        [_btn("✍️ Настроить сообщение", "pmsg", gid, pid)],
        [_btn("🕓 Время", "ptime", gid, pid), _btn("🔁 Повторение", "prep", gid, pid)],
        [_btn("🗓️ Дни недели", "pwd", gid, pid), _btn("📆 Дни месяца", "pmd", gid, pid)],
        [_btn("⏱️ Установить время", "pauto", gid, pid)],
        [_btn("⏲️ Дата начала", "psdate", gid, pid), _btn("⏲️ Дата окончания", "pedate", gid, pid)],
        [_btn(pin_txt, "ppin", gid, pid)],
        [_btn(dellast_txt, "pdellast", gid, pid)],
        [_btn(deltimer_txt, "pdeltimer", gid, pid)],
        [_btn("📂 Выбрать тему", "ptopic", gid, pid)],
        [_btn("⬅️ Назад", "back", gid, "pst")],
    ]
    return _kb(rows)


def post_message_text(gid, pid):
    post = get_post(gid, pid)
    text_txt = "установлен ✅" if post.get("text") else "не установлен ▫️"
    media_txt = "установлено ✅" if post.get("media") else "не установлено ▫️"
    btn_txt = "установлены ✅" if post.get("buttons") else "не установлены ▫️"
    return (
        "✍️ <b>Настроить сообщение</b>\n\n"
        f"📝 Текст: {text_txt}\n"
        f"📸 Медиа: {media_txt}\n"
        f"🔠 URL-кнопки: {btn_txt}\n\n"
        "Выберите, что настроить, или нажмите «Просмотр», чтобы увидеть, что уже установлено:"
    )


def post_message_kb(gid, pid):
    return _kb([
        [_btn("📝 Текст", "ptxt", gid, pid), _btn("👀 Просмотр", "ptxtprev", gid, pid)],
        [_btn("📸 Медиа", "pmedia", gid, pid), _btn("👀 Просмотр", "pmediaprev", gid, pid)],
        [_btn("🔠 URL-кнопки", "pbtn", gid, pid), _btn("👀 Просмотр", "pbtnprev", gid, pid)],
        [_btn("👀 Полный предпросмотр", "pprev", gid, pid)],
        [_btn("⬅️ Назад", "back", gid, "popen", pid)],
    ])


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


def buttons_prompt_kb(gid, pid, has_value, miniapp_url=None):
    rows = []
    if miniapp_url:
        rows.append([types.InlineKeyboardButton(
            "⚡Простое создание кнопок",
            url=miniapp_url,
        )])
    if has_value:
        rows.append([_btn("🚫Удалить URL-кнопки", "pbtndel", gid, pid)])
    rows.append([_btn("⬅️Назад", "pbtncancel", gid, pid)])
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
        [_btn("💭 Системные сообщения", "delsys", gid)],
        [_btn("🤯 Массовое удаление", "delmass", gid)],
        [_btn("⬅️ Назад", "back", gid, "root")],
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

# updated 2026-09-18
