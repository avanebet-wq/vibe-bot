# -*- coding: utf-8 -*-
"""Настройки конструктора розыгрыша. UI хранит черновик отдельно от активной записи."""
import html
import time
from telebot import types
from runtime import bot
from database import db_get, db_set, db_update_json
from utils import is_chat_admin

KEY = "contest_configs"
PENDING_KEY = "contest_settings_pending"


def _store():
    return db_get(KEY, {}) or {}


def _save(data):
    db_set(KEY, data)


def _default():
    return {
        "text": "🎉 Новый розыгрыш!",
        "photo": None,
        "interval_seconds": 3600,
        "start_mode": "now",
        "start_time": None,
        "requirement": "invite",
        "required": 1,
    }


def get_config(gid):
    data = _store(); cfg = data.get(str(gid), {}) or {}
    base = _default(); base.update(cfg)
    return base


def save_config(gid, **changes):
    result={"cfg":None}
    def mutate(data):
        cfg=_default(); cfg.update(data.get(str(gid), {}) or {}); cfg.update(changes)
        data[str(gid)]=cfg; result["cfg"]=dict(cfg); return data
    db_update_json(KEY, mutate, {})
    return result["cfg"]


def _pending_store():
    return db_get(PENDING_KEY, {}) or {}


def _set_pending(chat_id, user_id, kind):
    def mutate(data): data[f"{chat_id}:{user_id}"]=kind; return data
    db_update_json(PENDING_KEY, mutate, {})


def _get_pending(chat_id, user_id):
    return _pending_store().get(f"{chat_id}:{user_id}")


def _clear_pending(chat_id, user_id):
    def mutate(data): data.pop(f"{chat_id}:{user_id}", None); return data
    db_update_json(PENDING_KEY, mutate, {})


def _esc(v):
    return html.escape(str(v or ""), quote=False)


def _interval_label(sec):
    if sec is None: return "Только один раз"
    sec = int(sec)
    if sec % 86400 == 0: return f"каждые {sec // 86400} дн."
    if sec % 3600 == 0: return f"каждые {sec // 3600} ч"
    return f"каждые {sec // 60} мин"


def _start_label(cfg):
    if cfg.get("start_mode") == "now": return "сразу после запуска"
    return cfg.get("start_time") or "не задано"


def _req_label(cfg):
    if cfg.get("requirement") == "none": return "без требований"
    return f"пригласить {int(cfg.get('required') or 1)} чел."


def settings_text(gid):
    cfg = get_config(gid)
    photo = "установлено 📷" if cfg.get("photo") else "нет"
    return (
        "⚙️ <b>НАСТРОЙКИ РОЗЫГРЫША</b>\n\n"
        f"📝 Текст: <b>{'задан' if cfg.get('text') else 'не задан'}</b>\n"
        f"📷 Фото: <b>{photo}</b>\n"
        f"🔁 Интервал: <b>{_interval_label(cfg.get('interval_seconds'))}</b>\n"
        f"⏰ Первая публикация: <b>{_esc(_start_label(cfg))}</b>\n"
        f"📋 Требование: <b>{_esc(_req_label(cfg))}</b>\n\n"
        "Настройки сохраняются как черновик. После проверки нажмите «🚀 Запустить»."
    )


def settings_kb(gid):
    cfg = get_config(gid)
    req = cfg.get("requirement") == "invite"
    return types.InlineKeyboardMarkup(row_width=2).add(
        types.InlineKeyboardButton("📝 Текст", callback_data=f"rgs|text|{gid}"),
        types.InlineKeyboardButton("📷 Фото", callback_data=f"rgs|photo|{gid}"),
        types.InlineKeyboardButton("🔁 Интервал", callback_data=f"rgs|interval|{gid}"),
        types.InlineKeyboardButton("⏰ Первая публикация", callback_data=f"rgs|start|{gid}"),
        types.InlineKeyboardButton("📋 Требования", callback_data=f"rgs|req|{gid}"),
        types.InlineKeyboardButton("👥 Количество людей" if req else "👥 Количество людей (выкл)", callback_data=f"rgs|count|{gid}"),
        types.InlineKeyboardButton("👤 Участники", callback_data=f"rgs|participants|{gid}"),
        types.InlineKeyboardButton("👀 Посмотреть сообщение", callback_data=f"rgs|preview|{gid}"),
        types.InlineKeyboardButton("🗑 Удалить фото", callback_data=f"rgs|photodel|{gid}"),
        types.InlineKeyboardButton("🚀 Запустить", callback_data=f"rgs|launch|{gid}"),
        types.InlineKeyboardButton("🛑 Остановить", callback_data=f"rgs|stop|{gid}"),
        types.InlineKeyboardButton("❌ Закрыть", callback_data=f"rgs|close|{gid}"),
    )


def _edit(call):
    try:
        bot.edit_message_text(settings_text(call.message.chat.id), call.message.chat.id, call.message.message_id,
                              reply_markup=settings_kb(call.message.chat.id), parse_mode="HTML")
    except Exception:
        pass


def open_settings(message):
    gid = message.chat.id
    if message.chat.type not in ("group", "supergroup"):
        return bot.reply_to(message, "⚠️ Настройки розыгрыша доступны только в группе.")
    if not is_chat_admin(gid, message.from_user.id):
        return bot.reply_to(message, "⛔ Только администратор может настраивать розыгрыш.")
    msg = bot.send_message(gid, settings_text(gid), reply_markup=settings_kb(gid), parse_mode="HTML")
    return msg


def _prompt(call, kind, text):
    _set_pending(call.message.chat.id, call.from_user.id, kind)
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text)


def _participants_view(gid):
    from contest import _get
    session = _get(gid)
    if not session or not session.get("active"):
        return ("👤 <b>УЧАСТНИКИ</b>\n\nℹ️ Активного розыгрыша сейчас нет.", types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("◀️ Назад", callback_data=f"rgs|back|{gid}")))
    participants = session.get("participants", {}) or {}
    lines = ["👤 <b>УЧАСТНИКИ</b>", ""]
    kb = types.InlineKeyboardMarkup(row_width=1)
    if participants:
        for i, (key, participant) in enumerate(participants.items(), 1):
            username = participant.get("username")
            name = participant.get("name") or participant.get("id") or "участник"
            label = f"@{username}" if username else str(name)
            lines.append(f"{i}. {_esc(label)}")
            button_label = f"{i}. {label} — ❌ Удалить"
            if len(button_label) > 60:
                button_label = button_label[:57] + "..."
            kb.add(types.InlineKeyboardButton(button_label, callback_data=f"rgs|pdel|{gid}|{key}"))
        lines += ["", "Нажмите на участника, чтобы удалить его из текущего списка."]
    else:
        lines.append("Список пока пуст.")
    kb.add(types.InlineKeyboardButton("➕ Добавить участника", callback_data=f"rgs|padd|{gid}"))
    if participants:
        kb.add(types.InlineKeyboardButton("🗑 Очистить список", callback_data=f"rgs|pclear|{gid}"))
    kb.add(types.InlineKeyboardButton("◀️ Назад", callback_data=f"rgs|back|{gid}"))
    return "\n".join(lines), kb


def _edit_participants(call, gid):
    text, kb = _participants_view(gid)
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


def _require_admin(call, gid):
    if not is_chat_admin(gid, call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Только администратор.", show_alert=True); return False
    return True


def _time_next(hhmm):
    import datetime
    now = datetime.datetime.now().astimezone()
    hh, mm = map(int, hhmm.split(":"))
    dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if dt <= now: dt += datetime.timedelta(days=1)
    return dt.timestamp()


def handle_callback(call):
    parts = (call.data or "").split("|")
    if len(parts) < 3 or parts[0] != "rgs": return False
    action = parts[1]
    try: gid = int(parts[2])
    except ValueError: return True
    if not _require_admin(call, gid): return True
    if action == "participants":
        bot.answer_callback_query(call.id)
        return _edit_participants(call, gid)
    if action == "padd":
        return _prompt(call, "participant_add", "➕ Введите username участника в формате <code>@username</code>.")
    if action == "pdel":
        if len(parts) < 4:
            bot.answer_callback_query(call.id, "Некорректный участник.", show_alert=True); return True
        from contest import remove_participant
        ok, text = remove_participant(gid, parts[3])
        bot.answer_callback_query(call.id, text, show_alert=True)
        return _edit_participants(call, gid)
    if action == "pclear":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=2).add(
            types.InlineKeyboardButton("✅ Да, очистить", callback_data=f"rgs|pclear_yes|{gid}"),
            types.InlineKeyboardButton("↩️ Отмена", callback_data=f"rgs|participants|{gid}"),
        )
        return bot.edit_message_text("🗑 <b>Очистить список участников?</b>\n\nБудут удалены только текущие записи участников. Счётчики приглашений останутся без изменений.", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    if action == "pclear_yes":
        from contest import clear_participants
        ok, text = clear_participants(gid)
        bot.answer_callback_query(call.id, text, show_alert=True)
        return _edit_participants(call, gid)
    if action == "back":
        bot.answer_callback_query(call.id)
        return _edit(call)
    if action == "text": return _prompt(call, "text", "📝 Отправьте новый текст сообщения розыгрыша одним сообщением.")
    if action == "photo": return _prompt(call, "photo", "📷 Отправьте фотографию для розыгрыша. Подпись у фото будет проигнорирована — текст берётся из настройки сообщения.")
    if action == "photodel":
        save_config(gid, photo=None); bot.answer_callback_query(call.id, "🗑 Фото удалено."); return _edit(call)
    if action == "interval":
        bot.answer_callback_query(call.id)
        kb = types.InlineKeyboardMarkup(row_width=2)
        vals = [(300,"5 мин"),(600,"10 мин"),(1800,"30 мин"),(3600,"1 час"),(7200,"2 часа"),(21600,"6 часов"),(43200,"12 часов"),(86400,"24 часа"),(None,"Один раз")]
        for val,label in vals:
            key = "once" if val is None else str(val)
            kb.add(types.InlineKeyboardButton(label, callback_data=f"rgs|iset|{gid}|{key}"))
        kb.add(types.InlineKeyboardButton("✏️ Своё значение", callback_data=f"rgs|intervalcustom|{gid}"))
        return bot.edit_message_text("🔁 <b>Интервал публикации</b>\nВыберите готовый вариант или задайте свой (например: 45м, 2ч).", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    if action == "iset":
        key = parts[3] if len(parts)>3 else ""
        val = None if key == "once" else int(key)
        save_config(gid, interval_seconds=val)
        bot.answer_callback_query(call.id, "✅ Интервал сохранён."); return _edit(call)
    if action == "intervalcustom": return _prompt(call, "interval", "🔁 Введите интервал: например <code>45м</code>, <code>2ч</code> или <code>1д</code>.")
    if action == "start":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("▶️ Сразу", callback_data=f"rgs|sset|{gid}|now"),
            types.InlineKeyboardButton("⏰ Указать время", callback_data=f"rgs|startcustom|{gid}"),
        )
        return bot.edit_message_text("⏰ <b>Время первой публикации</b>", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    if action == "sset":
        mode=parts[3] if len(parts)>3 else "now"; save_config(gid,start_mode=mode,start_time=None)
        bot.answer_callback_query(call.id,"✅ Время старта сохранено."); return _edit(call)
    if action == "startcustom": return _prompt(call,"start","⏰ Введите время первой публикации в формате <code>ЧЧ:ММ</code>, например <code>21:30</code>.")
    if action == "req":
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🚫 Без требований", callback_data=f"rgs|rset|{gid}|none"),
            types.InlineKeyboardButton("👥 Пригласить пользователя", callback_data=f"rgs|rset|{gid}|invite"),
        )
        return bot.edit_message_text("📋 <b>Требование для участия</b>\n\nМожно разрешить запись всем или потребовать пригласить указанное количество участников.",call.message.chat.id,call.message.message_id,reply_markup=kb,parse_mode="HTML")
    if action == "rset":
        req=parts[3] if len(parts)>3 else "invite"; save_config(gid,requirement=req)
        bot.answer_callback_query(call.id,"✅ Требование изменено."); return _edit(call)
    if action == "count":
        cfg=get_config(gid)
        if cfg.get("requirement") != "invite": return bot.answer_callback_query(call.id,"Сначала выберите «Пригласить пользователя».",show_alert=True)
        bot.answer_callback_query(call.id)
        kb=types.InlineKeyboardMarkup(row_width=3)
        for n in range(1,11): kb.add(types.InlineKeyboardButton(str(n),callback_data=f"rgs|cset|{gid}|{n}"))
        kb.add(types.InlineKeyboardButton("✏️ Своё число",callback_data=f"rgs|countcustom|{gid}"))
        return bot.edit_message_text("👥 <b>Сколько людей нужно пригласить?</b>",call.message.chat.id,call.message.message_id,reply_markup=kb,parse_mode="HTML")
    if action == "cset":
        n=max(1,min(100,int(parts[3]))); save_config(gid,required=n); bot.answer_callback_query(call.id,f"✅ Нужно пригласить: {n}"); return _edit(call)
    if action == "countcustom": return _prompt(call,"count","👥 Введите количество людей от 1 до 100.")
    if action == "preview":
        bot.answer_callback_query(call.id)
        from contest import preview_config
        return preview_config(gid, call.message.chat.id)
    if action == "launch":
        bot.answer_callback_query(call.id)
        from contest import start_from_config
        start_from_config(call.message, get_config(gid))
        return
    if action == "stop":
        bot.answer_callback_query(call.id)
        from contest import cmd_stop
        cmd_stop(call.message); return _edit(call)
    if action == "close":
        bot.answer_callback_query(call.id)
        try: bot.delete_message(call.message.chat.id,call.message.message_id)
        except Exception: pass
        return True
    return True


def handle_pending(message):
    if not getattr(message,"from_user",None): return False
    kind=_get_pending(message.chat.id,message.from_user.id)
    if not kind: return False
    if not is_chat_admin(message.chat.id,message.from_user.id): _clear_pending(message.chat.id,message.from_user.id); return False
    gid=message.chat.id
    if kind == "participant_add":
        raw = (message.text or "").strip()
        token = raw.split()[0] if raw else ""
        if not token.startswith("@") or len(token) < 2:
            bot.reply_to(message, "⚠️ Формат: <code>@username</code>.", parse_mode="HTML"); return True
        from contest import add_participant_by_username
        ok, text = add_participant_by_username(gid, token)
        _clear_pending(gid, message.from_user.id)
        bot.reply_to(message, text, parse_mode="HTML")
        return True
    if kind == "text":
        if not message.text: bot.reply_to(message,"⚠️ Нужен текст."); return True
        if len(message.text)>3900: bot.reply_to(message,"⚠️ Текст слишком длинный. Максимум 3900 символов."); return True
        save_config(gid,text=message.text); _clear_pending(gid,message.from_user.id); bot.reply_to(message,"✅ Текст сохранён."); return True
    if kind == "photo":
        if not getattr(message,"photo",None): bot.reply_to(message,"⚠️ Отправьте именно фотографию."); return True
        save_config(gid,photo={"file_id":message.photo[-1].file_id}); _clear_pending(gid,message.from_user.id); bot.reply_to(message,"✅ Фото сохранено."); return True
    if kind == "interval":
        raw=(message.text or "").strip().lower(); import re
        m=re.fullmatch(r"(\d+)\s*(м|min|мин|ч|h|час|часы|ч.)",raw) or re.fullmatch(r"(\d+)\s*(д|дн|day|days)",raw)
        if not m: bot.reply_to(message,"⚠️ Формат: 45м, 2ч или 1д."); return True
        val=int(m.group(1)); unit=raw[m.end(1):].strip()
        sec=val*86400 if unit.startswith(("д","day")) else val*3600 if unit.startswith(("ч","h","час")) else val*60
        if sec<60 or sec>7*86400: bot.reply_to(message,"⚠️ Интервал должен быть от 1 минуты до 7 дней."); return True
        save_config(gid,interval_seconds=sec); _clear_pending(gid,message.from_user.id); bot.reply_to(message,"✅ Интервал сохранён."); return True
    if kind == "start":
        import re
        raw=(message.text or "").strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",raw): bot.reply_to(message,"⚠️ Формат времени: ЧЧ:ММ, например 21:30."); return True
        save_config(gid,start_mode="time",start_time=raw); _clear_pending(gid,message.from_user.id); bot.reply_to(message,"✅ Время первой публикации сохранено."); return True
    if kind == "count":
        try: n=int((message.text or "").strip())
        except ValueError: n=0
        if not 1<=n<=100: bot.reply_to(message,"⚠️ Укажите число от 1 до 100."); return True
        save_config(gid,required=n); _clear_pending(gid,message.from_user.id); bot.reply_to(message,"✅ Количество приглашённых сохранено."); return True
    return False


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("rgs|"))
def _callback(call):
    try: handle_callback(call)
    except Exception:
        import logging; logging.getLogger("contest_settings").exception("contest settings callback failed")
        try: bot.answer_callback_query(call.id,"⚠️ Ошибка настройки.",show_alert=True)
        except Exception: pass
