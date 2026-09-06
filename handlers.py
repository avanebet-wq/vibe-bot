# -*- coding: utf-8 -*-
"""VIBE Bot — handlers module."""
from runtime import *
from ai import *
from cleanup import *
from core import *
from general import *
from triggers import *
from ui import *
from triggers import handle_trigger_command
from cleanup import (
    _handle_advanced_cleanup,
    _cleanup_dk_allowed,
    _cleanup_messages,
    _get_chat_setting,
    _parse_period_words,
    _record_join,
    _render_template,
    _save_welcome,
    _set_chat_rules,
)


def _local_antispam_join(m,new_user):
    cid=m.chat.id
    if not get_v(cid,"iris_antispam",True): return False
    spam=set(str(x) for x in (get_v(cid,"antispam_ids",[]) or []))
    if str(new_user.id) not in spam: return False
    try:
        bot.ban_chat_member(cid,new_user.id)
        bot.send_message(cid,f"🛡 Лиза заблокировала {get_user_mention(user_id=new_user.id,first_name=new_user.first_name)}: пользователь находится в локальной базе антиспама.")
    except Exception as e: logging.warning(f"[ANTISPAM] {e}")
    return True


def _antiraid_join(m, new_user):
    """Локальный анти-рейд: при всплеске входов удаляет новых участников."""
    cid = m.chat.id
    cfg = get_v(cid, "antiraid", {}) or {}
    if not cfg.get("enabled"):
        return False
    try:
        threshold = max(2, int(cfg.get("threshold", 5)))
        window = max(10, min(3600, int(cfg.get("window", 60))))
    except Exception:
        threshold, window = 5, 60
    now = time.time()
    data = db_get("antiraid_joins", {})
    rows = data.setdefault(str(cid), [])
    rows = [x for x in rows if now - float(x.get("at", 0)) <= window]
    rows.append({"id": int(new_user.id), "at": now, "name": new_user.first_name or "Участник"})
    data[str(cid)] = rows[-200:]
    db_set("antiraid_joins", data)
    if len(rows) < threshold:
        return False
    # Удаляем участников из текущего всплеска, кроме администраторов/создателя.
    removed = 0
    for item in list(rows):
        target = int(item.get("id"))
        try:
            member = bot.get_chat_member(cid, target)
            if member.status in ("creator", "administrator"):
                continue
            bot.ban_chat_member(cid, target)
            bot.unban_chat_member(cid, target, only_if_banned=True)
            removed += 1
        except Exception as e:
            logging.warning(f"[ANTIRAID] {cid}/{target}: {e}")
    data[str(cid)] = []
    db_set("antiraid_joins", data)
    if removed:
        try:
            bot.send_message(cid, f"🛡 <b>Антирейд</b>\nОбнаружен всплеск новых входов. Лиза применяет защитные меры. Удалено новых участников: <b>{removed}</b>.", parse_mode="HTML")
        except Exception:
            pass
    return removed > 0

def handle_system_messages(m):
    try:
        if getattr(m, "new_chat_members", None):
            for new_user in m.new_chat_members:
                if new_user.id == BOT_ID:
                    register_chat(m.chat)
                    try:
                        bot.send_message(
                            m.chat.id,
                            "🎉 <b>Лиза установлена!</b>\n\n"
                            "Выдайте мне права администратора, чтобы я могла полноценно работать.\n"
                            "После этого используйте <code>+Правила</code>, <code>+Приветствие</code>, <code>Настройки</code> или <code>Триггеры</code>.",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logging.error(f"[INSTALL] {e}", exc_info=True)
                    continue
                if new_user.is_bot:
                    continue
                # Глобальный бан сетки проверяется до локальной модерации.
                try:
                    grids = db_get("chat_grids", {}) or {}
                    grid = next((g for g in grids.values() if m.chat.id in g.get("chats", [])), None)
                    if grid and str(new_user.id) in (grid.get("global_bans", {}) or {}):
                        bot.ban_chat_member(cid, new_user.id)
                        bot.unban_chat_member(cid, new_user.id, only_if_banned=True)
                        bot.send_message(cid, f"🌐 <b>Сетка</b>\nЛиза отклонила вход {get_user_mention(user_id=new_user.id, first_name=new_user.first_name)}: пользователь находится в глобальном бане сетки.", parse_mode="HTML")
                        continue
                except Exception as e:
                    logging.warning(f"[GRID BAN JOIN] {cid}/{new_user.id}: {e}")
                if _local_antispam_join(m, new_user):
                    continue
                if _antiraid_join(m, new_user):
                    continue
                _record_join(m.chat.id, new_user)
                # Telegram service-message sender is the inviter in the common
                # add-to-chat case. Keep this fact for Iris-style inviter queries.
                try:
                    if getattr(m, "from_user", None) and m.from_user.id != new_user.id:
                        inv = db_get("chat_invites", {})
                        inv.setdefault(str(m.chat.id), {})[str(new_user.id)] = {
                            "inviter_id": m.from_user.id,
                            "inviter_name": m.from_user.first_name or "Пользователь",
                            "date": time.time(),
                        }
                        db_set("chat_invites", inv)
                except Exception:
                    pass
                welcome = _get_chat_setting(m.chat.id, "welcome", "")
                if welcome:
                    # Iris-style: приветствуем только впервые увиденного участника.
                    data = db_get("chat_activity", {})
                    row = data.setdefault(str(m.chat.id), {}).setdefault(str(new_user.id), {})
                    already_greeted = bool(row.get("greeted", False))
                    if not already_greeted:
                        try:
                            rendered = _render_template(welcome, new_user)
                            msg = bot.send_message(m.chat.id, rendered, parse_mode="HTML")
                            row["greeted"] = True
                            row["last_greet_message_id"] = msg.message_id
                            db_set("chat_activity", data)
                            ttl = int(_get_chat_setting(m.chat.id, "welcome_delete_after", 0) or 0)
                            if ttl > 0:
                                auto_del(msg, min(ttl, 86400))
                        except Exception as e:
                            logging.error(f"[WELCOME] {e}", exc_info=True)
        if getattr(m, "left_chat_member", None):
            u=m.left_chat_member
            if u and not u.is_bot:
                cfg=get_v(m.chat.id,"autokick",{}) or {}
                exits=cfg.get("exits",0)
                if exits:
                    data=db_get("chat_exits",{}); chat=data.setdefault(str(m.chat.id),{}); row=chat.setdefault(str(u.id),{"times":[]})
                    now=time.time(); window=int(cfg.get("window",3600)); row["times"]=[x for x in row.get("times",[]) if now-x<=window]; row["times"].append(now); db_set("chat_exits",data)
                    if len(row["times"])>=int(exits):
                        try:
                            if cfg.get("action","kick")=="ban": bot.ban_chat_member(m.chat.id,u.id)
                            else: bot.ban_chat_member(m.chat.id,u.id); bot.unban_chat_member(m.chat.id,u.id,only_if_banned=True)
                        except Exception as e: logging.warning(f"[AUTOKICK] {e}")
        if get_v(m.chat.id, "del_sys", False):
            try:
                bot.delete_message(m.chat.id, m.message_id)
            except Exception as e:
                if "message to delete not found" not in str(e):
                    logging.error(f"[SYS DEL] {e}")
    except Exception as e:
        logging.error(f"[SYSTEM MSG] {e}", exc_info=True)

def cmd_start(m):
    payload = None
    if m.text:
        text_parts = m.text.split(maxsplit=1)
        if len(text_parts) > 1:
            payload = text_parts[1].strip()

    if payload and (payload.startswith("regword_") or payload.startswith("unregword_")):
        return handle_word_game_registration(m, payload)

    if not check_access(m):
        return

    if m.chat.type == "private":
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("➕ Добавить Лизу в группу", url=f"https://t.me/{BOT_USERNAME}?startgroup=true"))
        kb.add(types.InlineKeyboardButton("📖 Базовая настройка", callback_data="liza_setup_help"))
        txt = (
            "👋 <b>Привет!</b>\n\n"
            "Я <b>Лиза</b> 🌿 — чат-менеджер для Telegram.\n"
            "Чтобы установить меня, нажми кнопку ниже, выбери группу и обязательно выдай мне права администратора.\n\n"
            "После установки настрой приветствие, правила и нужные функции прямо в группе."
        )
        msg = bot.send_message(m.chat.id, txt, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        finish_command(m, "start", msg, ttl=180)
        return

    # /start в группе — подтверждение установки в стиле Iris.
    register_chat(m.chat)
    try:
        member = bot.get_chat_member(m.chat.id, BOT_ID)
        status = member.status
    except Exception:
        status = "unknown"
    if status not in ("administrator", "creator"):
        txt = (
            "🌿 <b>Лиза ещё не установлена полностью.</b>\n\n"
            "Назначьте меня администратором группы и выдайте необходимые права — "
            "это нужно для приветствий, модерации, закрепов и очистки чата."
        )
    else:
        txt = (
            "🎉 <b>Лиза установлена!</b>\n\n"
            "Теперь можно настроить:\n"
            "• <code>+Правила</code>\n"
            "• <code>+Приветствие</code>\n"
            "• <code>Настройки</code>\n"
            "• <code>Триггеры</code>\n\n"
            "Все команды можно писать с префиксом <code>!</code>, <code>.</code>, <code>/</code> "
            "или со словом «Лиза»."
        )
    msg = bot.send_message(m.chat.id, txt, parse_mode="HTML")
    finish_command(m, "start", msg, ttl=180)

def cmd_settings(m):
    if not check_access(m): return
    if m.chat.type == "private":
        if get_admin_rank(m.chat.id, m.from_user.id) < 5:
            return reply_no_rights(m)
    elif get_admin_rank(m.chat.id, m.from_user.id) < 5:
        return reply_no_rights(m)
    msg = bot.send_message(m.chat.id, "🎛 <b>Панель управления Лизой</b> ✨\n\nНастрой поведение и функции бота под свой чат. Изменения сохраняются автоматически.", reply_markup=main_kb(m.chat.id, m.chat.type == 'private'), parse_mode='HTML')
    finish_command(m, "settings", msg)











def on_photo(m):
    try:
        if not check_access(m): return
        uid = m.from_user.id
        record_xp_and_stats(m)
    except Exception as e: logging.error(f"[ON PHOTO] {e}", exc_info=True)


# ============================================================
# Развлекательный блок Iris-style: рулетка, дуэли, кубы,
# шипперинг, случайные числа и повтор текста.
# ============================================================













# ============================================================
# IRIS: заметки и таймеры — реальные сохранение/доставка
# ============================================================





# ============================================================
# IRIS: отношения и браки — функциональный модуль
# ============================================================














# ============================================================
# IRIS: репутация и закладки — функциональные модули
# ============================================================



# IRIS: награды — реальные персональные награды с хранением в БД



# IRIS: круги — пользовательские сообщества внутри чата.







def _economy_command(m, t_lower, t):
    """Только базовая экономика: баланс, ежедневный бонус, перевод и топ."""
    if m.chat.type not in ("group", "supergroup", "private"):
        return None
    uid, cid = m.from_user.id, m.chat.id
    balances = db_get("iriski_balances", {}) or {}
    key = str(uid)
    balances.setdefault(key, 0)
    def save(): db_set("iriski_balances", balances)
    if t_lower in ("баланс", "мой баланс", "ириски", "монеты"):
        return finish_command(m, "balance", bot.send_message(cid, f"💰 <b>Баланс</b>\n\nУ тебя: <b>{int(balances.get(key,0))}</b> ирисок.", parse_mode="HTML"), ttl=30)
    if t_lower in ("бонус", "ежедневный бонус", "бонус дня"):
        cooldown = db_get("bonus_cooldowns", {}) or {}; now=time.time(); until=float(cooldown.get(key,0) or 0)
        if until > now:
            left=int(until-now); h=left//3600; mm=(left%3600)//60
            return finish_command(m,"bonus_wait",bot.send_message(cid,f"⏳ Бонус уже получен. Следующий через <b>{h}ч {mm}м</b>.",parse_mode="HTML"),ttl=20)
        import random as _random
        amount=_random.randint(50,150); balances[key]=int(balances.get(key,0))+amount; cooldown[key]=now+86400
        save(); db_set("bonus_cooldowns",cooldown)
        return finish_command(m,"bonus",bot.send_message(cid,f"🎁 <b>Ежедневный бонус</b>\n\nНачислено: <b>+{amount}</b> ирисок.\nБаланс: <b>{balances[key]}</b>.",parse_mode="HTML"),ttl=30)
    if t_lower.startswith("передать ириски ") or t_lower.startswith("подарить ириски "):
        parts=t.split(); nums=[x for x in parts if x.isdigit()]
        target_uid,target_name,_=extract_target_and_args(m, parts)
        if not target_uid or not nums: return finish_command(m,"transfer_err",bot.send_message(cid,"⚠️ Формат: <code>Передать ириски @user 100</code>",parse_mode="HTML"),ttl=15)
        amount=int(nums[-1])
        if amount<=0 or amount>int(balances.get(key,0)): return finish_command(m,"transfer_err",bot.send_message(cid,"⚠️ Недостаточно ирисок или сумма указана неверно."),ttl=15)
        if int(target_uid)==uid: return finish_command(m,"transfer_self",bot.send_message(cid,"⚠️ Нельзя переводить ириски самому себе."),ttl=15)
        balances[key]-=amount; balances[str(target_uid)]=int(balances.get(str(target_uid),0))+amount; save()
        return finish_command(m,"transfer",bot.send_message(cid,f"💸 {get_user_mention(m.from_user)} передал {get_user_mention(user_id=target_uid,first_name=target_name)} <b>{amount}</b> ирисок.",parse_mode="HTML"),ttl=20)
    if t_lower.startswith("топ ирисок") or t_lower in ("топ балансов","богачи"):
        rows=sorted(((int(v or 0),int(k)) for k,v in balances.items() if str(k).lstrip('-').isdigit()), reverse=True)[:10]
        txt="🏆 <b>ТОП ИРИСОК</b>\n\n"+"\n".join(f"{i}. {get_user_mention(user_id=uid2)} — <b>{amt}</b>" for i,(amt,uid2) in enumerate(rows,1))
        return finish_command(m,"top_iriski",bot.send_message(cid,txt,parse_mode="HTML"),ttl=45)
    return None

def _private_personal_command(m, t_lower):
    if m.chat.type != "private": return None
    uid=m.from_user.id
    if t_lower in ("помощь","команды","команды лиза","лиза помощь"):
        txt=("<b>📖 ЛИЗА — ЛИЧНЫЕ КОМАНДЫ</b>\n\n"
             "<code>Мой баланс</code> — текущий баланс ирисок.\n"
             "<code>Бонус</code> — ежедневное начисление.\n"
             "<code>Моя статистика</code> — активность в чатах, где работает Лиза.\n"
             "<code>Мои баны</code> — история зафиксированных банов.\n\n"
             "💡 Подсказка: в группе доступна расширенная справка командой <code>Помощь</code>.")
        return finish_command(m,"private_help",bot.send_message(m.chat.id,txt,parse_mode="HTML"),ttl=120)
    if t_lower in ("мои баны","мой бан"):
        history=db_get("ban_history",{}) or {}; rows=[]
        for cid,items in history.items():
            for v in items or []:
                if int((v or {}).get("target_uid",0) or 0)==uid:
                    dt=datetime.fromtimestamp(float(v.get("date",0) or 0),KYIV_TZ).strftime("%d.%m.%Y %H:%M")
                    rows.append((float(v.get("date",0) or 0),f"• {html.escape(str(v.get('chat_title') or cid))} — {html.escape(str(v.get('reason') or 'без причины'))} · {dt}"))
        rows.sort(reverse=True)
        return finish_command(m,"private_bans",bot.send_message(m.chat.id,"🔨 <b>МОИ БАНЫ</b>\n\n"+("\n".join(x[1] for x in rows[:30]) or "История банов пуста."),parse_mode="HTML"),ttl=60)
    if t_lower in ("моя стата","моя статистика","мой актив"):
        total=0; chats=0; last=0
        for cid,row in (db_get("chat_activity",{}) or {}).items():
            item=(row or {}).get(str(uid))
            if not item: continue
            chats+=1; total += sum(int(v or 0) for v in ((item.get("daily",{}) or {}).values())); last=max(last,float(item.get("last_seen",0) or 0))
        last_txt=datetime.fromtimestamp(last,KYIV_TZ).strftime("%d.%m.%Y %H:%M") if last else "нет данных"
        return finish_command(m,"private_stats",bot.send_message(m.chat.id,f"📊 <b>МОЯ СТАТИСТИКА</b>\n\n💬 Сообщений: <b>{total}</b>\n👥 Чатов: <b>{chats}</b>\n🕒 Последняя активность: <b>{last_txt}</b>",parse_mode="HTML"),ttl=60)
    return _economy_command(m,t_lower,t)

def text_handler(m):
    t=(m.text or "").strip(); t_lower=t.lower()
    private_result = _private_personal_command(m, t_lower)
    if private_result is not None:
        return private_result
    if t_lower.startswith(("позвать всех","созвать всех","тегнуть всех","упомянуть всех","массовое упоминание","созвать онлайн")):
        cid,uid=m.chat.id,m.from_user.id
        if get_admin_rank(cid,uid)<3: return reply_no_rights(m)
        online_only=t_lower.startswith("созвать онлайн")
        prefix="созвать онлайн" if online_only else ("созвать всех" if t_lower.startswith("созвать всех") else None)
        tail=t[len(prefix):].strip() if prefix else ""
        activity=db_get("chat_activity",{}).get(str(cid),{}) or {}
        members=db_get("chat_members",{}) or {}; chat=members.get(str(cid),{}) if isinstance(members,dict) else {}
        ids=[]; now=time.time()
        source=chat if isinstance(chat,dict) and chat else activity
        for suid,data in source.items():
            try: x=int(suid)
            except Exception: continue
            if x==uid: continue
            row=(activity.get(str(x),{}) or {})
            if online_only and now-float(row.get("last_seen",0) or 0)>15*60: continue
            name=(data or {}).get("name","пользователь") if isinstance(data,dict) else row.get("name","пользователь")
            ids.append((x,name))
        ids=ids[:30]
        if not ids: return finish_command(m,"mass_mention_empty",bot.send_message(cid,"ℹ️ Не удалось найти подходящих участников для созыва."),ttl=15)
        mentions=" ".join(get_user_mention(user_id=x,first_name=name) for x,name in ids)
        title="Созыв онлайн" if online_only else "Созыв участников"
        extra=("\n\n"+html.escape(tail[:500])) if tail else ""
        suffix="" if len(ids)<30 else "\n\n⚠️ Показаны первые 30 участников."
        return finish_command(m,"mass_mention",bot.send_message(cid,f"📣 <b>{title}</b>{extra}\n\n"+mentions+suffix,parse_mode="HTML"),ttl=30)
    try:
        # Нормализация Iris-style префиксов до разбора команд.
        if m.text:
            raw = m.text.strip()
            first, *rest = raw.split(maxsplit=1)
            if first.startswith(("!", ".", "/")) and len(first) > 1 and not first.startswith("//"):
                raw = first[1:] + ((" " + rest[0]) if rest else "")
            elif first.lower() == "лиза":
                raw = rest[0].strip() if rest else "помощь"
            m.text = raw
            _tr=handle_trigger_command(m)
            if _tr is not None: return
        if not check_access(m): return
        if m.text and m.chat.type in ["group", "supergroup"]:
            _ac=_handle_advanced_cleanup(m)
            if _ac: return
        uid, cid, t = m.from_user.id, m.chat.id, (m.text or "").strip()
        # Iris-style command prefixes and обращение к Лизе:
        # !команда, .команда, /команда и «Лиза команда» работают одинаково.
        if t:
            first, *rest = t.split(maxsplit=1)
            if first.startswith(("!", ".", "/")) and len(first) > 1 and not first.startswith("//"):
                first = first[1:]
                t = first + ((" " + rest[0]) if rest else "")
            elif first.lower() == "лиза":
                t = rest[0].strip() if rest else "помощь"
        fname, uname = m.from_user.first_name, m.from_user.username
        boss = get_admin_rank(cid, uid) >= 5

        # Track activity even for commands; this is the basis for cleanup tools.
        if m.chat.type in ["group", "supergroup"] and not m.from_user.is_bot:
            try: _record_join(cid, m.from_user)
            except Exception: pass

        t_lower = t.lower()

        # Единая справка Лизы: коротко, по разделам и без перегруза.
        if t_lower in ("помощь", "команды", "команды лиза", "лиза помощь") and m.chat.type in ("group", "supergroup"):
            txt=(
                "<b>📚 СПРАВКА ЛИЗЫ</b>\n\n"
                "Лиза — чат-менеджер. Здесь перечислены только функции текущей сборки.\n\n"
                "🛡 <b>Модерация</b>\n<code>Варн</code> · <code>Варны</code> · <code>Мут 30м</code> · <code>Муты</code>\n<code>Бан</code> · <code>Разбан</code> · <code>Банлист</code> · <code>Кик</code>\n<code>Кто админ</code> · <code>Повысить</code> · <code>Понизить</code> · <code>Модер лог</code>\n\n"
                "⚙️ <b>Настройки</b>\n<code>Базовая настройка</code> · <code>Настройки чата</code> · <code>Права рангов</code>\n<code>+Правила</code> · <code>Правила</code> · <code>+Приветствие</code> · <code>Приветствие</code>\n<code>+Автокик</code> · <code>+Минрег</code> · <code>+Каналы</code> · <code>+Инлайны</code>\n\n"
                "🧹 <b>Очистка и защита</b>\n<code>Пург 50</code> · <code>Закреп</code> · <code>Открепить</code> · <code>+Ссылки</code> · <code>+Антиспам ID</code>\n<code>Проверка безопасности</code> · <code>Тг права</code>\n\n"
                "📊 <b>Статистика</b>\n<code>Чат инфо</code> · <code>Чат стата</code> · <code>Чат стата 7</code> · <code>Топ</code> · <code>Кто онлайн</code> · <code>Моя статистика</code>\n\n"
                "👤 <b>Профили</b>\n<code>Моя анкета</code> · <code>Анкета @user</code> · <code>Ник</code> · <code>Звание</code> · <code>О себе</code>\n\n"
                "💰 <b>Экономика</b>\n<code>Баланс</code> · <code>Бонус</code> · <code>Передать ириски @user 100</code> · <code>Топ ирисок</code>\n\n"
                "💡 <b>Подсказка:</b> команды можно писать с <code>!</code>, <code>/</code> или <code>.</code>. Например, <code>!Мут 30м</code>. Для подробной инструкции напиши <code>Помощь модерация</code> или <code>Помощь настройки</code>."
            )
            return finish_command(m,"main_help",bot.send_message(cid,txt,parse_mode="HTML"),ttl=180)

        # Расширенная справка по разделам.
        help_sections = {
            "помощь модерация": ("🛡 <b>МОДЕРАЦИЯ</b>", [
                "<code>+Модер 1</code> — назначить ранг", "<code>Повысить</code> / <code>Понизить</code>",
                "<code>Варн</code> / <code>Варны</code> / <code>Мои варны</code>",
                "<code>Мут 30м</code> / <code>Муты</code>", "<code>Бан</code> / <code>Разбан</code> / <code>Банлист</code>",
                "<code>Кик</code> / <code>Кик тихо</code> / <code>Амнистия</code>", "<code>Кто админ</code> / <code>Модер лог</code>"]),
            "помощь экономика": ("💰 <b>ЭКОНОМИКА</b>", [
                "<code>Баланс</code> — твои ириски", "<code>Бонус</code> — ежедневная награда",
                "<code>Передать ириски @user 100</code> — перевод", "<code>Мои чеки</code> — созданные чеки",
                "<code>Где мои ириски</code> — история операций", "<code>Топ ирисок</code> — рейтинг",
                "<code>Курс Бкоина</code> / <code>Бкоин 1</code> — работа с Бкоинами"]),
            "помощь статистика": ("📊 <b>СТАТИСТИКА</b>", [
                "<code>Чат инфо</code> — информация о чате", "<code>Чат стата</code> — активность участников",
                "<code>Чат стата 7</code> — статистика за период", "<code>Стата по часам</code> — активность по времени",
                "<code>Топ 10</code> — самые активные", "<code>Кто онлайн</code> — активные участники",
                "<code>Олды</code> / <code>Новички</code> — списки участников"]),
            "помощь профиль": ("👤 <b>ПРОФИЛЬ</b>", [
                "<code>Моя анкета</code> — твой профиль", "<code>Анкета @user</code> — профиль участника",
                "<code>+Ник</code> / <code>Ник</code> — имя в чате", "<code>+Звание</code> / <code>Звание</code>",
                "<code>+Девиз</code> / <code>-Девиз</code>", "<code>Мой город</code> / <code>Мой др</code> / <code>О себе</code>"]),
        }
        if t_lower in help_sections and m.chat.type in ("group", "supergroup"):
            title, lines = help_sections[t_lower]
            txt = title + "\n\n" + "\n".join("• " + x for x in lines) + "\n\n💡 <i>Ответь на сообщение участника, если команда принимает цель.</i>"
            return finish_command(m, "section_help", bot.send_message(cid, txt, parse_mode="HTML"), ttl=90)

        # --- IRIS: завещание и внутренняя передача создателя ---
        # В Telegram бот не может сам передать реального владельца чата.
        # Поэтому эти команды управляют именно внутренним 5 рангом Лизы.
        if t_lower.startswith(("+завещание ", "+наследство ")):
            if get_admin_rank(cid, uid) <= 0:
                return reply_no_rights(m)
            target_uid, target_name, _ = extract_target_and_args(m, parts)
            if not target_uid or int(target_uid) == uid:
                return finish_command(m, "will_err", bot.send_message(cid, "⚠️ Укажи другого пользователя ответом, @username или ID."), ttl=10)
            wills = db_get("wills", {}) or {}
            wills[str(uid)] = {"beneficiary_id": int(target_uid), "beneficiary_name": target_name or "Пользователь", "created": int(time.time())}
            db_set("wills", wills)
            return finish_command(m, "will_set", bot.send_message(cid, f"📜 Завещание сохранено. Наследник: {get_user_mention(user_id=target_uid, first_name=target_name)}.", parse_mode="HTML"), ttl=20)

        if t_lower in ("моё завещание", "мое завещание", "моё наследство", "мое наследство"):
            will = (db_get("wills", {}) or {}).get(str(uid))
            if not will:
                return finish_command(m, "will_none", bot.send_message(cid, "📜 Завещание не оформлено."), ttl=15)
            dt = datetime.fromtimestamp(int(will.get("created", 0)), KYIV_TZ).strftime("%d.%m.%Y %H:%M")
            return finish_command(m, "will_view", bot.send_message(cid, f"📜 <b>ТВОЁ ЗАВЕЩАНИЕ</b>\n\nНаследник: {get_user_mention(user_id=int(will['beneficiary_id']), first_name=will.get('beneficiary_name','Пользователь'))}\nОформлено: {dt}", parse_mode="HTML"), ttl=30)

        if t_lower in ("-завещание", "-наследство"):
            wills = db_get("wills", {}) or {}
            if str(uid) not in wills:
                return finish_command(m, "will_none", bot.send_message(cid, "ℹ️ Завещание уже отсутствует."), ttl=10)
            wills.pop(str(uid), None); db_set("wills", wills)
            return finish_command(m, "will_delete", bot.send_message(cid, "🗑 Завещание аннулировано."), ttl=15)

        if t_lower.startswith(("вступить в наследство ", "принять наследство ")):
            target_uid, target_name, _ = extract_target_and_args(m, parts)
            if not target_uid or int(target_uid) == uid:
                return finish_command(m, "inherit_err", bot.send_message(cid, "⚠️ Укажи пользователя, оставившего завещание."), ttl=10)
            wills = db_get("wills", {}) or {}
            will = wills.get(str(target_uid))
            if not will or int(will.get("beneficiary_id", 0)) != uid:
                return finish_command(m, "inherit_denied", bot.send_message(cid, "⚠️ Для тебя нет действующего завещания от этого пользователя."), ttl=15)
            # Стоимость вступления по модели Iris: 50 ирисок.
            balances = db_get("iriski_balances", {}) or {}
            row = balances.setdefault(str(uid), {"balance": 0, "daily": 0, "earned": 0, "spent": 0})
            row.setdefault("balance", 0); row.setdefault("earned", 0); row.setdefault("spent", 0)
            if int(row.get("balance", 0)) < 50:
                return finish_command(m, "inherit_money", bot.send_message(cid, "💰 Для вступления в наследство нужно 50 ирисок."), ttl=15)
            row["balance"] -= 50; row["spent"] += 50; balances[str(uid)] = row
            tx = db_get("iriski_transactions", {}) or {}; stamp = str(time.time_ns()); now = int(time.time())
            tx[stamp] = {"uid": uid, "amount": 50, "direction": "out", "kind": "вступление в наследство", "peer_id": int(target_uid), "peer_name": target_name or will.get("beneficiary_name", "наследодатель"), "ts": now}
            db_set("iriski_balances", balances); db_set("iriski_transactions", tx)
            set_admin_rank(cid, uid, 5)
            wills.pop(str(target_uid), None); db_set("wills", wills)
            return finish_command(m, "inherit_ok", bot.send_message(cid, f"👑 Наследство принято. Внутренний ранг Лизы восстановлен до <b>5 — Создатель</b>.\n💰 Списано: 50 ирисок.\n\n⚠️ Реального владельца Telegram-чата эта команда не передаёт.", parse_mode="HTML"), ttl=30)

        if t_lower in ("!передать создателя", "передать создателя", "!передать владельца", "передать владельца"):
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            target_uid, target_name, _ = extract_target_and_args(m, parts)
            if not target_uid:
                return finish_command(m, "transfer_creator_err", bot.send_message(cid, "⚠️ Укажи нового создателя ответом, @username или ID."), ttl=10)
            if int(target_uid) == uid:
                return finish_command(m, "transfer_creator_err", bot.send_message(cid, "⚠️ Ты уже являешься создателем."), ttl=10)
            set_admin_rank(cid, uid, 4)
            set_admin_rank(cid, target_uid, 5)
            transfer = db_get("creator_transfers", {}) or {}
            transfer[str(cid)] = {"from_id": uid, "to_id": int(target_uid), "to_name": target_name or "Пользователь", "date": int(time.time())}
            db_set("creator_transfers", transfer)
            return finish_command(m, "transfer_creator", bot.send_message(cid, f"👑 Внутренний создатель Лизы передан: {get_user_mention(user_id=target_uid, first_name=target_name)}.\n\n⚠️ Для передачи реального владельца Telegram-чата используй штатную функцию Telegram.", parse_mode="HTML"), ttl=30)

        # --- Дополнительные команды Iris: модерация и управление доступом ---
        # Эти команды обрабатываются до общего диспетчера, чтобы поддерживать
        # многострочные алиасы Iris и варианты с префиксами ! . /.
        if t_lower in ("!снимаю полномочия", "снимаю полномочия", "ухожу в отставку", "разжаловать меня"):
            if get_admin_rank(cid, uid) <= 0:
                return reply_no_rights(m)
            set_admin_rank(cid, uid, 0)
            save_moderation_record(cid, "resign", uid, fname, uid, fname)
            return finish_command(m, "resign", bot.send_message(cid, f"📉 {get_user_mention(user_id=uid, first_name=fname)} снял с себя полномочия модератора." , parse_mode="HTML"))

        if t_lower in ("восстановить создателя", "восстановить владельца", "восстановить права", "хозяин вернулся", "хв"):
            try:
                member = bot.get_chat_member(cid, uid)
                if member.status != "creator":
                    return reply_no_rights(m)
            except Exception:
                return reply_no_rights(m)
            set_admin_rank(cid, uid, 5)
            return finish_command(m, "restore_creator", bot.send_message(cid, "👑 Права создателя восстановлены."), ttl=15)

        if t_lower in ("снять вышедших", "разжаловать вышедших"):
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            admins = db_get("chat_admins", {}).get(str(cid), {})
            removed = 0
            for auid in list(admins):
                try:
                    member = bot.get_chat_member(cid, int(auid))
                    if member.status in ("left", "kicked"):
                        set_admin_rank(cid, int(auid), 0); removed += 1
                except Exception:
                    # Если Telegram больше не разрешает получить участника,
                    # не снимаем роль автоматически.
                    continue
            return finish_command(m, "remove_left_admins", bot.send_message(cid, f"🧹 Снято полномочий у вышедших модераторов: <b>{removed}</b>.", parse_mode="HTML"), ttl=15)

        if t_lower in ("снять всех", "разжаловать всех", "!снять всех", "!разжаловать всех"):
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            try:
                creator_member = bot.get_chat_member(cid, uid)
                creator_id = uid if getattr(creator_member, "status", None) == "creator" else None
            except Exception:
                creator_id = uid
            admins = db_get("chat_admins", {}).get(str(cid), {})
            removed = 0
            for auid in list(admins):
                if int(auid) == uid:
                    continue
                if creator_id is not None and int(auid) == creator_id:
                    continue
                set_admin_rank(cid, int(auid), 0); removed += 1
            return finish_command(m, "remove_all_admins", bot.send_message(cid, f"🧹 Снято полномочий: <b>{removed}</b>.", parse_mode="HTML"), ttl=15)

        # Полный набор сокращённых способов назначения ранга из Iris.
        if t_lower.startswith("!!модер ") or t_lower.startswith("!!!модер ") or t_lower.startswith("!!!!модер "):
            bangs = len(t_lower.split()[0]) - 1
            rank = min(5, bangs)
            t = "+модер " + str(rank) + t[len(t_lower.split()[0]):]
            t_lower = t.lower()
            parts = t.split()
            cmd = parts[0].lower()
        if t_lower.startswith("+админ "):
            parts = t.split()
            if len(parts) > 1 and parts[1].isdigit():
                pass

        # Настройки срока варнов/мутов/банов, как в Iris.
        if t_lower.startswith("варны лимит "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            try: value = max(1, int(t_lower.split()[-1]))
            except Exception: return finish_command(m, "warn_limit_err", bot.send_message(cid, "⚠️ Укажи число предупреждений."), ttl=10)
            set_chat_setting(cid, "max_warns", value)
            return finish_command(m, "warn_limit", bot.send_message(cid, f"⚠️ Лимит предупреждений: <b>{value}</b>.", parse_mode="HTML"), ttl=15)
        if t_lower.startswith("варны чс "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            period = _parse_period_words(t_lower)
            if not period: return finish_command(m, "warn_ban_period_err", bot.send_message(cid, "⚠️ Формат: Варны ЧС 7 дней."), ttl=10)
            set_chat_setting(cid, "warn_ban_period", period)
            return finish_command(m, "warn_ban_period", bot.send_message(cid, f"🚫 Срок наказания по лимиту варнов: <b>{format_seconds_human(period)}</b>.", parse_mode="HTML"), ttl=15)
        if t_lower.startswith("варны период "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            period = _parse_period_words(t_lower)
            if not period: return finish_command(m, "warn_period_err", bot.send_message(cid, "⚠️ Формат: Варны период 7 дней."), ttl=10)
            set_chat_setting(cid, "warn_period", period)
            return finish_command(m, "warn_period", bot.send_message(cid, f"⏳ Срок хранения варна: <b>{format_seconds_human(period)}</b>.", parse_mode="HTML"), ttl=15)
        if t_lower.startswith("мут период "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            period = _parse_period_words(t_lower)
            if not period or period < 60: return finish_command(m, "mute_period_err", bot.send_message(cid, "⚠️ Минимальный срок мута по умолчанию — 1 минута."), ttl=10)
            set_chat_setting(cid, "mute_period", period)
            return finish_command(m, "mute_period", bot.send_message(cid, f"🔇 Срок мута по умолчанию: <b>{format_seconds_human(period)}</b>.", parse_mode="HTML"), ttl=15)
        if t_lower.startswith("бан период "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            period = _parse_period_words(t_lower)
            if not period: return finish_command(m, "ban_period_err", bot.send_message(cid, "⚠️ Формат: Бан период 30 дней."), ttl=10)
            set_chat_setting(cid, "ban_period", period)
            return finish_command(m, "ban_period", bot.send_message(cid, f"🚫 Срок бана по умолчанию: <b>{format_seconds_human(period)}</b>.", parse_mode="HTML"), ttl=15)

        # Управление уведомлениями о доступности команд.
        if t_lower in ("+команды", "-команды"):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            enabled = t_lower.startswith("+")
            set_chat_setting(cid, "command_notifications", enabled)
            return finish_command(m, "command_notifications", bot.send_message(cid, f"🔔 Оповещения о доступности команд {'включены' if enabled else 'выключены'}."), ttl=10)

        # Сброс глобального/раздельного ДК.
        if t_lower == "сброс команд":
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            data = db_get("command_access", {}); data.pop(str(cid), None); db_set("command_access", data)
            return finish_command(m, "reset_dk", bot.send_message(cid, "♻️ <b>Доступ команд сброшен</b>\n\nВсе ограничения ДК для этого чата возвращены к значениям по умолчанию."), ttl=15)
        if t_lower.startswith("сброс дк "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            section = t_lower.split(maxsplit=2)[2]
            data = db_get("command_access", {}); chat = data.get(str(cid), {})
            for key in list(chat):
                if key == section or key.startswith(section + " "):
                    chat.pop(key, None)
            data[str(cid)] = chat; db_set("command_access", data)
            return finish_command(m, "reset_dk_section", bot.send_message(cid, f"♻️ Настройки ДК раздела <code>{html.escape(section)}</code> сброшены.", parse_mode="HTML"), ttl=15)

        # Личные исключения ДК: просмотр и сброс.
        if t_lower == "все лдк":
            if not command_allowed_by_dk(cid, uid, "личный дк"): return reply_no_rights(m)
            data = db_get("personal_command_access", {}).get(str(cid), {})
            rows=[]
            users=db_get("users_data", {})
            for auid, commands in data.items():
                enabled=[c for c,v in commands.items() if v]
                disabled=[c for c,v in commands.items() if not v]
                if enabled or disabled:
                    name=users.get(str(auid),{}).get("name", f"ID:{auid}")
                    rows.append(f"• {get_user_mention(user_id=int(auid), first_name=name)}: +{', '.join(enabled) or '—'} / -{', '.join(disabled) or '—'}")
            txt="👤 <b>ЛИЧНЫЕ ДОСТУПЫ</b>\n\n"+("\n".join(rows) or "Исключений нет.")
            return finish_command(m, "all_ldk", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)
        if t_lower.startswith("сброс всех лдк"):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            data=db_get("personal_command_access", {}); data.pop(str(cid), None); db_set("personal_command_access", data)
            return finish_command(m, "reset_all_ldk", bot.send_message(cid, "♻️ <b>Личные доступы сброшены</b>\n\nИсключения ДК для всех участников удалены."), ttl=15)
        if t_lower.startswith("сброс лдк "):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            target_uid, target_name, _ = extract_target_and_args(m, parts)
            if not target_uid: return finish_command(m, "reset_ldk_err", bot.send_message(cid, "⚠️ <b>Не указан пользователь</b>\nОтветь на его сообщение, укажи @username или ID."), ttl=10)
            data=db_get("personal_command_access", {}); data.setdefault(str(cid), {}).pop(str(target_uid), None); db_set("personal_command_access", data)
            return finish_command(m, "reset_ldk", bot.send_message(cid, f"♻️ Личные доступы {get_user_mention(user_id=target_uid, first_name=target_name)} сброшены.", parse_mode="HTML"), ttl=15)

        # --- Настройка чата / правила / приветствие ---
        if t_lower in ("правила", "правила помощь"):
            if t_lower == "правила помощь":
                txt = ("📜 <b>ЛИЗА — ПРАВИЛА</b>\n\n"
                       "<code>+Правила</code>\nТекст правил со следующей строки\n\n"
                       "<code>Правила</code> — показать\n"
                       "<code>-Правила</code> или <code>Правила удалить</code> — удалить.")
                return finish_command(m, "rules_help", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)
            rules = _get_chat_setting(cid, "rules", "")
            if rules:
                try:
                    link = _get_chat_setting(cid, "chat_link", "")
                    rules = rules.replace("{ссылка}", link or "ссылка не установлена")
                except Exception:
                    pass
            txt = "📜 <b>ПРАВИЛА ЛИЗЫ</b>\n\n" + (html.escape(rules) if rules else "Правила ещё не установлены.")
            return finish_command(m, "rules", bot.send_message(cid, txt, parse_mode="HTML"), ttl=180)
        if t_lower == "приветствие":
            welcome = _get_chat_setting(cid, "welcome", "")
            txt = "👋 <b>ПРИВЕТСТВИЕ VIBE</b>\n\n" + (html.escape(welcome) if welcome else "Приветствие ещё не установлено.")
            return finish_command(m, "welcome_show", bot.send_message(cid, txt, parse_mode="HTML"), ttl=180)
        if t_lower.startswith("+правила"):
            if not _cleanup_dk_allowed(cid, uid, "правила"): return reply_no_rights(m)
            body = t.split("\n", 1)[1].strip() if "\n" in t else t[len("+правила"):].strip()
            if not body: return finish_command(m, "rules_err", bot.send_message(cid, "⚠️ После +правила укажи текст."), ttl=10)
            _set_chat_rules(cid, body)
            return finish_command(m, "rules_set", bot.send_message(cid, "✅ <b>Правила VIBE обновлены.</b>\nТеперь участники могут посмотреть их командой <code>правила</code>.", parse_mode="HTML"))
        if t_lower.startswith("+приветствие"):
            if not _cleanup_dk_allowed(cid, uid, "приветствие"): return reply_no_rights(m)
            body = t.split("\n", 1)[1].strip() if "\n" in t else t[len("+приветствие"):].strip()
            if not body: return finish_command(m, "welcome_err", bot.send_message(cid, "⚠️ После +приветствие укажи текст."), ttl=10)
            _save_welcome(cid, body)
            return finish_command(m, "welcome_set", bot.send_message(cid, "✅ Приветствие VIBE сохранено."))
        if t_lower in ("-правила", "правила удалить"):
            if not _cleanup_dk_allowed(cid, uid, "правила"): return reply_no_rights(m)
            _set_chat_rules(cid, "")
            return finish_command(m, "rules_off", bot.send_message(cid, "🗑 Правила Лизы удалены."))
        if t_lower in ("-приветствие", "приветствие удалить"):
            if not _cleanup_dk_allowed(cid, uid, "приветствие"): return reply_no_rights(m)
            _save_welcome(cid, "")
            return finish_command(m, "welcome_off", bot.send_message(cid, "🗑 Приветствие Лизы удалено."))
        if t_lower in ("приветствие помощь", "приветствие?"):
            txt = ("👋 <b>ЛИЗА — ПРИВЕТСТВИЕ</b>\n\n"
                   "<code>+Приветствие</code>\nТекст приветствия со следующей строки\n\n"
                   "<code>Приветствие</code> — показать\n"
                   "<code>Приветствие удалить</code> — удалить\n"
                   "<code>+Автоудаление приветствий 10 минут</code> — автоудаление.")
            return finish_command(m, "welcome_help", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)
        if t_lower.startswith("+автоудаление приветствий"):
            if not _cleanup_dk_allowed(cid, uid, "приветствие"): return reply_no_rights(m)
            period = _parse_period_words(t_lower) or 300
            period = max(5, min(period, 86400))
            set_chat_setting(cid, "welcome_delete_after", period)
            return finish_command(m, "welcome_delete_on", bot.send_message(cid, f"🧹 Автоудаление приветствий: <b>{period//60} мин.</b>", parse_mode="HTML"), ttl=10)
        if t_lower == "-автоудаление приветствий":
            if not _cleanup_dk_allowed(cid, uid, "приветствие"): return reply_no_rights(m)
            set_chat_setting(cid, "welcome_delete_after", 0)
            return finish_command(m, "welcome_delete_off", bot.send_message(cid, "🧹 Автоудаление приветствий выключено."), ttl=10)
        if t_lower in ("приветствуй", "поприветствуй") or t_lower.startswith(("приветствуй ", "поприветствуй ")):
            welcome = _get_chat_setting(cid, "welcome", "")
            if not welcome:
                return finish_command(m, "welcome_missing", bot.send_message(cid, "⚠️ Сначала установите приветствие: <code>+Приветствие</code>.", parse_mode="HTML"), ttl=15)
            target = m.reply_to_message.from_user if m.reply_to_message else None
            if target is None:
                return finish_command(m, "welcome_target", bot.send_message(cid, "⚠️ Используйте команду ответом на сообщение пользователя.", parse_mode="HTML"), ttl=15)
            rendered = _render_template(welcome, target)
            msg = bot.send_message(cid, rendered, parse_mode="HTML")
            return finish_command(m, "welcome_manual", msg, ttl=180)
        if t_lower in ("приветствуй всех снова", "приветствуй снова", "приветствуй всех по новой"):
            with state_lock:
                data = db_get("chat_activity", {})
                chat = data.setdefault(str(cid), {})
                for row in chat.values(): row["greeted"] = False
                db_set("chat_activity", data)
            return finish_command(m, "welcome_reset", bot.send_message(cid, "♻️ История приветствий Лизы сброшена."))

        # --- Закреп / откреп ---
        if t_lower.startswith(("!закреп ", "закреп ", "/pin ")) or t_lower in ["!закреп", "закреп", "!пин", "пин", "/pin"]:
            if not _cleanup_dk_allowed(cid, uid, "закреп"): return reply_no_rights(m)
            target = m.reply_to_message
            if target is None and len(t.split()) > 1 and t.split()[1].isdigit():
                try:
                    target = bot.forward_message(cid, cid, int(t.split()[1]))
                    try: bot.delete_message(cid, target.message_id)
                    except: pass
                    target_mid = int(t.split()[1])
                except Exception:
                    target_mid = None
            else:
                target_mid = target.message_id if target else None
            if not target_mid: return finish_command(m, "pin_err", bot.send_message(cid, "⚠️ Используй команду ответом на сообщение или укажи ID сообщения."), ttl=10)
            try:
                bot.pin_chat_message(cid, target_mid, disable_notification=True)
                return finish_command(m, "pin", bot.send_message(cid, "📌 <b>ЛИЗА-ПИН</b>\nСообщение закреплено.", parse_mode="HTML"), ttl=10)
            except Exception as e:
                logging.error(f"[PIN] {e}")
                return finish_command(m, "pin_err", bot.send_message(cid, "⚠️ Не удалось закрепить сообщение. Проверь права бота."), ttl=15)
        if t_lower.startswith(("!открепить ", "открепить ", "/unpin ")) or t_lower in ["!открепить", "открепить", "!анпин", "анпин", "/unpin"]:
            if not _cleanup_dk_allowed(cid, uid, "открепить"): return reply_no_rights(m)
            try:
                bot.unpin_chat_message(cid)
                return finish_command(m, "unpin", bot.send_message(cid, "📌 Закрепление снято."), ttl=10)
            except Exception:
                return finish_command(m, "unpin_err", bot.send_message(cid, "⚠️ Не удалось снять закрепление."), ttl=15)

        # --- Название и описание ---
        if t_lower.startswith("!название ") or t_lower.startswith("название "):
            if not _cleanup_dk_allowed(cid, uid, "название"): return reply_no_rights(m)
            new_title = t.split(" ", 1)[1].strip()
            try:
                bot.set_chat_title(cid, new_title)
                return finish_command(m, "title", bot.send_message(cid, f"✏️ Название VIBE изменено на <b>{html.escape(new_title)}</b>.", parse_mode="HTML"), ttl=15)
            except Exception:
                return finish_command(m, "title_err", bot.send_message(cid, "⚠️ Не удалось изменить название чата."), ttl=15)
        if t_lower.startswith("+описание чата"):
            if not _cleanup_dk_allowed(cid, uid, "описание"): return reply_no_rights(m)
            body = t.split("\n",1)[1].strip() if "\n" in t else t[len("+описание чата"):].strip()
            try:
                bot.set_chat_description(cid, body)
                return finish_command(m, "desc", bot.send_message(cid, "📝 Описание VIBE обновлено."), ttl=15)
            except Exception:
                return finish_command(m, "desc_err", bot.send_message(cid, "⚠️ Не удалось изменить описание."), ttl=15)
        if t_lower == "-описание чата":
            if not _cleanup_dk_allowed(cid, uid, "описание"): return reply_no_rights(m)
            try: bot.set_chat_description(cid, "")
            except Exception: pass
            return finish_command(m, "desc_off", bot.send_message(cid, "🗑 Описание VIBE удалено."), ttl=15)

        # --- Чат-ссылка в стиле Iris ---
        if t_lower in ("+чат ссылка по заявкам", "чат ссылка по заявкам"):
            if not _cleanup_dk_allowed(cid, uid, "чат ссылка"): return reply_no_rights(m)
            try:
                link = bot.create_chat_invite_link(cid, name="Лиза — заявки", creates_join_request=True)
                set_chat_setting(cid, "chat_link", link.invite_link)
                return finish_command(m, "chat_link_requests", bot.send_message(cid, f"🔗 <b>Ссылка по заявкам Лизы:</b>\n{html.escape(link.invite_link)}", parse_mode="HTML"), ttl=120)
            except Exception:
                return finish_command(m, "chat_link_err", bot.send_message(cid, "⚠️ Не удалось создать ссылку по заявкам. Проверь права Лизы."), ttl=15)
        if t_lower in ("+чат ссылка", "чат ссылка"):
            if not _cleanup_dk_allowed(cid, uid, "чат ссылка"): return reply_no_rights(m)
            try:
                link = bot.create_chat_invite_link(cid, name="Лиза")
                set_chat_setting(cid, "chat_link", link.invite_link)
                return finish_command(m, "chat_link", bot.send_message(cid, f"🔗 <b>Ссылка Лизы:</b>\n{html.escape(link.invite_link)}", parse_mode="HTML"), ttl=120)
            except Exception:
                return finish_command(m, "chat_link_err", bot.send_message(cid, "⚠️ Не удалось создать ссылку. Проверь права Лизы."), ttl=15)
        if t_lower == "-чат ссылка":
            if not _cleanup_dk_allowed(cid, uid, "чат ссылка"): return reply_no_rights(m)
            link = _get_chat_setting(cid, "chat_link", "")
            if link:
                try: bot.revoke_chat_invite_link(cid, link)
                except Exception: pass
            set_chat_setting(cid, "chat_link", "")
            return finish_command(m, "chat_link_off", bot.send_message(cid, "🗑 Ссылка чата удалена из настроек Лизы."), ttl=10)
        if t_lower in ("!сброс ссылок", "сброс ссылок"):
            if not _cleanup_dk_allowed(cid, uid, "чат ссылка"): return reply_no_rights(m)
            links = get_v(cid, "chat_links", []) or []
            if isinstance(links, str): links = [links] if links else []
            for link in links:
                try: bot.revoke_chat_invite_link(cid, link)
                except Exception: pass
            set_chat_setting(cid, "chat_links", [])
            set_chat_setting(cid, "chat_link", "")
            return finish_command(m, "chat_links_reset", bot.send_message(cid, "🗑 Ссылки Лизы сброшены."), ttl=10)
        if t_lower in ("базовая настройка", "настройки помощь", "помощь настройки"):
            txt = ("⚙️ <b>БАЗОВАЯ НАСТРОЙКА ЛИЗЫ</b>\n\n"
                   "<code>+Правила</code> / <code>+Приветствие</code>\n"
                   "<code>+Чат ссылка</code>\n"
                   "<code>+модер 1-5</code>\n"
                   "<code>ДК команда ранг</code>\n"
                   "<code>+Триггер событие ранг</code>\n"
                   "<code>+Автокик молчунов 7 дней</code>\n"
                   "<code>Кик неактив 2 месяца</code>\n"
                   "<code>+Ссылки</code> / <code>-Ссылки</code>\n\n"
                   "Все команды поддерживают !, . и /.")
            return finish_command(m, "setup_help", bot.send_message(cid, txt, parse_mode="HTML"), ttl=180)

        # --- Дополнительные настройки чата ---
        if t_lower.startswith("+автокик") or t_lower=="-автокик" or t_lower.startswith("-автокик"):
            if not _cleanup_dk_allowed(cid, uid, "автокик"): return reply_no_rights(m)
            if t_lower=="-автокик": set_chat_setting(cid,"autokick",{}); return finish_command(m,"autokick_off",bot.send_message(cid,"🧹 <b>Автокик отключён</b>\n\nАвтоматическое действие после серии выходов больше не применяется."),ttl=10)
            if t_lower.startswith("-автокик молчунов"): set_chat_setting(cid,"autokick_silent",0); return finish_command(m,"autokick_silent_off",bot.send_message(cid,"🧹 <b>Автокик молчунов отключён</b>\n\nНеактивные участники больше не будут обрабатываться автоматически."),ttl=10)
            nums=re.findall(r"\d+",t_lower); exits=int(nums[0]) if nums else 1; period=_parse_period_words(t_lower) or 3600; action="ban" if " бан" in t_lower else "kick"
            set_chat_setting(cid,"autokick",{"exits":max(1,exits),"window":period,"action":action}); return finish_command(m,"autokick_on",bot.send_message(cid,f"🛡 Автокик включён: <b>{exits}</b> выход(а) за <b>{period//60}</b> мин → <b>{action}</b>.",parse_mode="HTML"),ttl=15)
        if t_lower.startswith("+автокик молчунов"):
            if not _cleanup_dk_allowed(cid,uid,"автокик"): return reply_no_rights(m)
            period=_parse_period_words(t_lower) or 7*86400; set_chat_setting(cid,"autokick_silent",period); return finish_command(m,"autokick_silent_on",bot.send_message(cid,f"🤫 Автокик молчунов включён: <b>{period//86400}</b> дн.",parse_mode="HTML"),ttl=15)
        if t_lower in ("-входы","+входы","-выходы","+выходы","-входы-выходы","+входы-выходы") or t_lower.startswith("+выходы "):
            code="входы" if "входы" in t_lower and "выходы" not in t_lower else ("выходы" if "выходы" in t_lower and "входы" not in t_lower else "входы-выходы")
            if not _cleanup_dk_allowed(cid,uid,code): return reply_no_rights(m)
            if t_lower.startswith("+выходы "):
                nums=re.findall(r"\d+",t_lower); set_chat_setting(cid,"leave_notify_threshold",int(nums[0]) if nums else 0); set_chat_setting(cid,"notify_leaves",True); return finish_command(m,"leave_threshold",bot.send_message(cid,"👋 Порог уведомлений о выходе обновлён."),ttl=10)
            val=t_lower.startswith("+"); set_chat_setting(cid,"notify_joins",val if "входы" in t_lower else get_v(cid,"notify_joins",False)); set_chat_setting(cid,"notify_leaves",val if "выходы" in t_lower else get_v(cid,"notify_leaves",False)); return finish_command(m,"joinleave",bot.send_message(cid,"🔔 Уведомления о входах/выходах обновлены."),ttl=10)
        if t_lower.startswith("+минрег ") or t_lower=="-минрег" or t_lower=="минрег":
            if not _cleanup_dk_allowed(cid,uid,"минрег"): return reply_no_rights(m)
            if t_lower=="-минрег": set_chat_setting(cid,"minreg_days",0); return finish_command(m,"minreg_off",bot.send_message(cid,"🛡 <b>Минрег отключён</b>\n\nОграничение по сроку регистрации снято."),ttl=10)
            if t_lower=="минрег": return finish_command(m,"minreg_show",bot.send_message(cid,f"🛡 Минрег: <b>{get_v(cid,'minreg_days',0)}</b> дн.",parse_mode="HTML"),ttl=30)
            nums=re.findall(r"\d+",t_lower); days=int(nums[0]) if nums else 0; set_chat_setting(cid,"minreg_days",days); return finish_command(m,"minreg_on",bot.send_message(cid,f"🛡 Минимальная регистрация: <b>{days}</b> дн.",parse_mode="HTML"),ttl=10)
        if t_lower in ("+каналы","-каналы","+инлайны","-инлайны"):
            if not _cleanup_dk_allowed(cid,uid,"каналы" if "каналы" in t_lower else "инлайны"): return reply_no_rights(m)
            key="allow_channel_posts" if "каналы" in t_lower else "inline_notifications"; set_chat_setting(cid,key,t_lower.startswith("+")); return finish_command(m,"setting",bot.send_message(cid,"⚙️ Настройка сохранена."),ttl=10)
        if t_lower in ("+чат ссылка","-чат ссылка","чат-ссылка") or t_lower.startswith("+чат ссылка"):
            if not _cleanup_dk_allowed(cid,uid,"чат ссылка"): return reply_no_rights(m)
            if t_lower=="-чат ссылка":
                try: bot.revoke_chat_invite_link(cid, get_v(cid,"chat_link",""))
                except: pass
                set_chat_setting(cid,"chat_link",""); return finish_command(m,"chatlink_off",bot.send_message(cid,"🔗 Ссылка VIBE удалена."),ttl=10)
            try:
                link=bot.create_chat_invite_link(cid, creates_join_request=("по заявкам" in t_lower)).invite_link
                set_chat_setting(cid,"chat_link",link); return finish_command(m,"chatlink",bot.send_message(cid,f"🔗 <b>Ссылка на VIBE:</b> {html.escape(link)}",parse_mode="HTML"),ttl=60)
            except Exception:
                return finish_command(m,"chatlink_err",bot.send_message(cid,"⚠️ Не удалось создать ссылку. Проверь права бота."),ttl=15)
        if t_lower=="чат-ссылка":
            link=get_v(cid,"chat_link",""); return finish_command(m,"chatlink_show",bot.send_message(cid,"🔗 "+(html.escape(link) if link else "Ссылка ещё не создана."),parse_mode="HTML"),ttl=60)

        # --- Открытие/закрытие чата для обычных участников ---
        if t_lower in ["+чат", "-чат"]:
            if not _cleanup_dk_allowed(cid, uid, "чат"): return reply_no_rights(m)
            try:
                perms = types.ChatPermissions(can_send_messages=(t_lower == "+чат"), can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True)
                bot.set_chat_permissions(cid, perms)
                txt = "🔓 <b>VIBE ОТКРЫТ</b>\nУчастники снова могут писать." if t_lower == "+чат" else "🔒 <b>VIBE НА ПАУЗЕ</b>\nОбычным участникам временно запрещено писать."
                return finish_command(m, "chat_lock", bot.send_message(cid, txt, parse_mode="HTML"), ttl=15)
            except Exception:
                return finish_command(m, "chat_lock_err", bot.send_message(cid, "⚠️ Не удалось изменить режим чата. Проверь права бота."), ttl=15)

        # --- Доп. Telegram/форумные настройки ---
        if t_lower in ("!ветка", "ветка", "топик инфо", "ветка инфо"):
            thread_id=getattr(m,"message_thread_id",None)
            if not thread_id:
                return finish_command(m,"topic_info",bot.send_message(cid,"🧵 Команда работает внутри форумной ветки/топика."),ttl=15)
            try:
                topic=bot.get_forum_topic(cid, thread_id)
                title=getattr(topic,"name",None) or "Без названия"
            except Exception:
                title="Текущая ветка"
            return finish_command(m,"topic_info",bot.send_message(cid,f"🧵 <b>ВЕТКА</b>\nНазвание: <b>{html.escape(str(title))}</b>\nID: <code>{thread_id}</code>",parse_mode="HTML"),ttl=30)

        if t_lower in ("+тг тег", "-тг тег", "тг тег") or t_lower.startswith("+тг тег ") or t_lower.startswith("-тг тег "):
            if not _cleanup_dk_allowed(cid,uid,"настройки"): return reply_no_rights(m)
            if t_lower == "тг тег":
                tag=get_v(cid,"tg_tag","") or ""
                return finish_command(m,"tg_tag",bot.send_message(cid,f"🏷 Тег чата: <b>{html.escape(tag)}</b>" if tag else "🏷 Тег чата не задан.",parse_mode="HTML"),ttl=20)
            if t_lower == "-тг тег":
                set_chat_setting(cid,"tg_tag","")
                return finish_command(m,"tg_tag_off",bot.send_message(cid,"🏷 Тег чата очищен."),ttl=15)
            tag=t.split(" ",2)[2].strip() if len(t.split(" ",2))>=3 else ""
            tag=tag[:64]
            if not tag:
                return finish_command(m,"tg_tag_err",bot.send_message(cid,"⚠️ Формат: <code>+Тг тег Название</code>",parse_mode="HTML"),ttl=15)
            set_chat_setting(cid,"tg_tag",tag)
            return finish_command(m,"tg_tag_set",bot.send_message(cid,f"🏷 Тег чата установлен: <b>{html.escape(tag)}</b>",parse_mode="HTML"),ttl=15)

        if t_lower in ("инвайты", "инвайты чат", "кто приглашал"):
            invites=db_get("chat_invites",{}) or {}
            chat_inv=invites.get(str(cid),{}) or {}
            rows=[]
            for target,data in chat_inv.items():
                rows.append((int(data.get("date",0)),target,data))
            rows.sort(reverse=True)
            lines=[]
            for ts,target,data in rows[:20]:
                inv=get_user_mention(user_id=int(data.get("inviter_id",0)),first_name=data.get("inviter_name") or "пользователь")
                lines.append(f"• {inv} → <code>{target}</code>")
            return finish_command(m,"invites",bot.send_message(cid,"📨 <b>ПОСЛЕДНИЕ ИНВАЙТЫ</b>\n\n"+("\n".join(lines) if lines else "История приглашений пока пуста."),parse_mode="HTML"),ttl=45)

        # --- IRIS: форумные топики, короткие списки и анти-рейд ---
        if t_lower in ("антирейд", "антирейд помощь") or t_lower.startswith(("+антирейд", "-антирейд")):
            if not _cleanup_dk_allowed(cid, uid, "антирейд"):
                return reply_no_rights(m)
            if t_lower == "антирейд помощь":
                return finish_command(m, "antiraid_help", bot.send_message(cid, "🛡 <b>АНТИРЕЙД</b>\n\n<code>+Антирейд 5 60</code> — 5 входов за 60 секунд.\n<code>-Антирейд</code> — выключить.\n<code>Антирейд</code> — показать настройки.", parse_mode="HTML"), ttl=60)
            if t_lower == "-антирейд":
                set_chat_setting(cid, "antiraid", {"enabled": False})
                return finish_command(m, "antiraid_off", bot.send_message(cid, "🛡 Антирейд выключен."), ttl=15)
            if t_lower == "антирейд":
                cfg=get_v(cid,"antiraid",{}) or {}
                state="включён" if cfg.get("enabled") else "выключен"
                return finish_command(m,"antiraid_show",bot.send_message(cid,f"🛡 Антирейд: <b>{state}</b>\nПорог: <b>{int(cfg.get('threshold',5))}</b>\nОкно: <b>{int(cfg.get('window',60))} сек.</b>",parse_mode="HTML"),ttl=30)
            nums=[int(x) for x in re.findall(r"\d+", t_lower)]
            threshold=max(2,min(100,nums[0] if nums else 5)); window=max(10,min(3600,nums[1] if len(nums)>1 else 60))
            set_chat_setting(cid,"antiraid",{"enabled":True,"threshold":threshold,"window":window})
            return finish_command(m,"antiraid_on",bot.send_message(cid,f"🛡 Антирейд включён: <b>{threshold}</b> входов за <b>{window} сек.</b>",parse_mode="HTML"),ttl=15)

        if t_lower in ("+короткие списки", "-короткие списки", "короткие списки"):
            if not _cleanup_dk_allowed(cid, uid, "короткие списки"):
                return reply_no_rights(m)
            if t_lower == "короткие списки":
                enabled=bool(get_v(cid,"short_lists",False))
                return finish_command(m,"short_lists_show",bot.send_message(cid,f"📋 Короткие списки: <b>{'включены' if enabled else 'выключены'}</b>.",parse_mode="HTML"),ttl=20)
            enabled=t_lower.startswith("+")
            set_chat_setting(cid,"short_lists",enabled)
            return finish_command(m,"short_lists_set",bot.send_message(cid,f"📋 Короткие списки {'включены' if enabled else 'выключены'}.",parse_mode="HTML"),ttl=15)

        if t_lower.startswith("топик ") or t_lower.startswith("ветка ") or t_lower in ("топик название", "ветка название"):
            if not _cleanup_dk_allowed(cid, uid, "топик"):
                return reply_no_rights(m)
            words=t.split(None,2)
            if len(words) >= 3 and words[1].lower() == "название":
                title=words[2].strip()
            elif len(words) >= 2 and words[0].lower() in ("топик","ветка") and words[1].lower() != "название":
                title=" ".join(words[1:]).strip()
            else:
                title=""
            if not title or title.lower()=="название":
                return finish_command(m,"topic_name_err",bot.send_message(cid,"⚠️ Укажи новое название: <code>Топик название Новое имя</code>.",parse_mode="HTML"),ttl=15)
            if not title:
                return finish_command(m,"topic_name_err",bot.send_message(cid,"⚠️ Укажи название топика."),ttl=15)
            thread_id=getattr(m,"message_thread_id",None)
            if not thread_id:
                return finish_command(m,"topic_err",bot.send_message(cid,"⚠️ Эту команду нужно отправлять внутри форумного топика."),ttl=15)
            try:
                bot.edit_forum_topic(cid, thread_id, name=title)
                return finish_command(m,"topic_name",bot.send_message(cid,"🧵 Название топика изменено."),ttl=15)
            except Exception as e:
                logging.warning(f"[TOPIC] {e}")
                return finish_command(m,"topic_err",bot.send_message(cid,"⚠️ Не удалось изменить топик. Нужны права управления темами."),ttl=15)

        # --- Чистка сообщений ---
        if t_lower.startswith("-смс") or t_lower.startswith("!пург") or t_lower.startswith("пург"):
            code = "-смс" if t_lower.startswith("-смс") else "пург"
            if not _cleanup_dk_allowed(cid, uid, code): return reply_no_rights(m)
            quiet = "тихо" in parts[1:]
            nums = [int(x) for x in parts[1:] if x.isdigit()]
            target_count = nums[0] if nums else 1
            anchor = m.reply_to_message.message_id if (code == "пург" and m.reply_to_message) else m.message_id
            if code == "пург" and not m.reply_to_message:
                return finish_command(m, "purge_err", bot.send_message(cid, "⚠️ <code>Пург</code> нужно использовать ответом на сообщение.", parse_mode="HTML"), ttl=10)
            deleted = _cleanup_messages(cid, anchor, target_count + 1)
            if quiet: return
            txt = f"🧹 <b>Очистка чата</b>\nУдалено сообщений: <b>{deleted}</b>."
            return finish_command(m, "cleanup", bot.send_message(cid, txt, parse_mode="HTML"), ttl=10)

        parts = t.split()
        cmd = parts[0].lower() if parts else ""
        
        # --- СИСТЕМА УВАЖЕНИЯ ---
        if t == "+" and m.reply_to_message:
            target_uid = m.reply_to_message.from_user.id
            if target_uid == uid:
                auto_del(bot.reply_to(m, "🙅 Себе уважение оказывать нельзя! 😉"), 5)
                try: bot.delete_message(cid, m.message_id)
                except: pass
                return
            if m.reply_to_message.from_user.is_bot:
                try: bot.delete_message(cid, m.message_id)
                except: pass
                return 
                
            with state_lock:
                users = db_get("users_data", {})
                u_giver = users.setdefault(str(uid), {"xp": 0, "msgs": 0, "name": fname, "uname": uname, "first_seen": time.time(), "respects": 0, "given_respects": 0, "respect_reset": 0})
                u_target = users.setdefault(str(target_uid), {"xp": 0, "msgs": 0, "name": m.reply_to_message.from_user.first_name, "uname": m.reply_to_message.from_user.username, "first_seen": time.time(), "respects": 0, "given_respects": 0, "respect_reset": 0})
                
                now = time.time()
                if now > u_giver.get("respect_reset", 0):
                    u_giver["given_respects"] = 0
                    n_dt = datetime.now(KYIV_TZ)
                    n_mid = (n_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                    u_giver["respect_reset"] = n_mid.timestamp()
                    
                if u_giver.get("given_respects", 0) >= 3:
                    reset_time = datetime.fromtimestamp(u_giver["respect_reset"], KYIV_TZ).strftime('%H:%M')
                    msg = bot.send_message(m.chat.id, f"🛑 {get_user_mention(m.from_user)}, лимит исчерпан! Вы уже передали 3 очка уважения сегодня.\nЛимит обновится в {reset_time}.", parse_mode='HTML')
                    auto_del(msg, 10)
                else:
                    u_giver["given_respects"] = u_giver.get("given_respects", 0) + 1
                    u_target["respects"] = u_target.get("respects", 0) + 1
                    db_set("users_data", users)
                    msg = bot.send_message(m.chat.id, f"🤝 {get_user_mention(m.from_user)} выражает уважение {get_user_mention(m.reply_to_message.from_user)}!\n<i>(Осталось на сегодня: {3 - u_giver['given_respects']})</i>", parse_mode='HTML')
                    auto_del(msg, 15)
            try: bot.delete_message(cid, m.message_id)
            except: pass
            return

        # Текстовый аналог /settings.
        if t_lower in ("настройки", "настройка"):
            if m.chat.type == "private":
                return reply_no_rights(m)
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            msg = bot.send_message(cid, "🎛 <b>Панель управления Лизой</b> ✨\n\nНастрой поведение и функции бота под свой чат. Изменения сохраняются автоматически.",
                                   reply_markup=main_kb(cid, False), parse_mode="HTML")
            return finish_command(m, "settings", msg)

        # --- ЦЕНТР УПРАВЛЕНИЯ ЛИЗОЙ: расширенный просмотр настроек и ролей ---
        if t_lower in ("настройки чата", "настройки лизы", "статус настроек", "лиза статус"):
            if get_admin_rank(cid, uid) < 1:
                return reply_no_rights(m)
            settings = db_get("settings", {}).get(str(cid), {}) or {}
            def flag(key, default=False):
                return "включено" if bool(settings.get(key, default)) else "выключено"
            admins = db_get("chat_admins", {}).get(str(cid), {}) or {}
            rank_rows=[]
            users=db_get("users_data",{}) or {}
            for suid, rank in sorted(admins.items(), key=lambda x:int(x[1]), reverse=True):
                name=(users.get(str(suid),{}) or {}).get("name","Участник")
                rank_rows.append(f"• {get_user_mention(user_id=int(suid), first_name=name)} — <b>{html.escape(ADMIN_RANKS[int(rank)])}</b>")
            txt=("⚙️ <b>НАСТРОЙКИ ЛИЗЫ</b>\n\n"
                 f"📖 Правила: <b>{'заданы' if get_v(cid,'rules','') else 'не заданы'}</b>\n"
                 f"👋 Приветствие: <b>{'задано' if get_v(cid,'welcome_text','') else 'не задано'}</b>\n"
                 f"🔗 Ссылка чата: <b>{'задана' if get_v(cid,'chat_link','') else 'нет'}</b>\n"
                 f"📊 Статистика: <b>{flag('stats_enabled', True)}</b>\n"
                 f"🛡 Антиспам: <b>{flag('iris_antispam')}</b>\n"
                 f"🚨 Антирейд: <b>{'включён' if get_v(cid,'antiraid',{}) else 'выключен'}</b>\n"
                 f"🤫 Автокик молчунов: <b>{'включён' if get_v(cid,'autokick_silent',0) else 'выключен'}</b>\n"
                 f"🧹 Автокик: <b>{'включён' if get_v(cid,'autokick',{}) else 'выключен'}</b>\n"
                 f"🔔 Входы: <b>{flag('notify_joins')}</b> · выходы: <b>{flag('notify_leaves')}</b>\n"
                 f"🔐 ДК в ЛС: <b>{flag('dk_in_private')}</b>\n\n"
                 "👑 <b>Модераторы</b>\n" + ("\n".join(rank_rows[:20]) if rank_rows else "Модераторы ещё не назначены."))
            return finish_command(m,"settings_status",bot.send_message(cid,txt,parse_mode="HTML"),ttl=120)

        if t_lower in ("права рангов", "права модеров", "ранги", "модераторы права"):
            if get_admin_rank(cid, uid) < 1:
                return reply_no_rights(m)
            blocks=[]
            for r in range(1,6):
                perms=get_rank_permissions(cid,r)
                enabled=[name for key,name in PERM_NAMES.items() if perms.get(key,False)]
                blocks.append(f"<b>{r}. {html.escape(ADMIN_RANKS[r])}</b>\n" + (" · ".join(enabled) if enabled else "Нет доступных прав"))
            txt="🛡 <b>ПРАВА РАНГОВ</b>\n\n"+"\n\n".join(blocks)+"\n\n💡 Изменение: <code>Права 2 can_mute вкл</code>"
            return finish_command(m,"rank_permissions_all",bot.send_message(cid,txt,parse_mode="HTML"),ttl=120)

        if t_lower in ("модераторы", "список модераторов", "кто модер", "кто модеры"):
            if get_admin_rank(cid, uid) < 1:
                return reply_no_rights(m)
            admins=db_get("chat_admins",{}).get(str(cid),{}) or {}; users=db_get("users_data",{}) or {}
            rows=[]
            for suid,rank in sorted(admins.items(),key=lambda x:(-int(x[1]), int(x[0]))):
                name=(users.get(str(suid),{}) or {}).get("name","Участник")
                status=""
                try:
                    member=bot.get_chat_member(cid,int(suid)); status=member.status
                except Exception: status="unknown"
                rows.append(f"{len(rows)+1}. {get_user_mention(user_id=int(suid),first_name=name)}\n   🛡 <b>{html.escape(ADMIN_RANKS[int(rank)])}</b> · {('в чате' if status not in ('left','kicked') else 'вышел')}")
            txt="👥 <b>МОДЕРАТОРЫ ЧАТА</b>\n\n"+("\n".join(rows[:50]) if rows else "Список пока пуст.")
            return finish_command(m,"moderators_list",bot.send_message(cid,txt,parse_mode="HTML"),ttl=120)

        # --- ДОСТУП КОМАНД (IRIS-STYLE) ---
        if cmd == "мой" and len(parts) >= 3 and parts[1].lower() == "доступ" and parts[2].lower() in ["команд", "команды"]:
            cmd = "мой доступ команд"
        elif cmd in ["доступ", "дк", "мой", "мдк", "мой доступ", "мой дк"] and len(parts) > 1:
            if parts[1].lower() in ["команд", "команды"]:
                cmd = "доступ команд" if cmd not in ["мой", "мдк", "мой доступ", "мой дк"] else "мой доступ команд"
        if cmd in ["доступ команд", "/дк", "!дк", ".дк", "дк", "мой доступ команд", "мой дк", "мдк"]:
            if cmd in ["мой доступ команд", "мой дк", "мдк"]:
                if not command_allowed_by_dk(cid, uid, "мой доступ команд"):
                    return reply_no_rights(m)
                return finish_command(m, "my_dk", bot.send_message(cid, format_dk_list(cid, uid), parse_mode="HTML"), ttl=120)
            if len(parts) == 1:
                if not command_allowed_by_dk(cid, uid, "доступ команд"):
                    return reply_no_rights(m)
                return finish_command(m, "dk", bot.send_message(cid, format_dk_list(cid), parse_mode="HTML"), ttl=120)
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            sub = parts[1].lower()
            if sub in ["лог"]:
                rows = db_get("dk_log", {}).get(str(cid), [])[-30:]
                txt = "⚙️ <b>VIBE-ЛОГ ДК</b>\n\n"
                for r in reversed(rows):
                    dt = datetime.fromtimestamp(r["date"], KYIV_TZ).strftime("%d.%m %H:%M")
                    txt += f"• {dt} — {html.escape(r['name'])}: <code>{html.escape(r['command'])}</code> {r['old']} → {r['new']}\n"
                return finish_command(m, "dk_log", bot.send_message(cid, txt + ("\nЛог пуст." if not rows else ""), parse_mode="HTML"), ttl=120)
            if len(parts) >= 3:
                code = DK_ALIASES.get(sub, sub)
                try: new_rank = int(parts[2])
                except Exception:
                    return finish_command(m, "dk_err", bot.send_message(cid, "⚠️ Формат: <code>дк команда ранг</code>. Ранг: 0–6." , parse_mode="HTML"), ttl=15)
                if new_rank < 0 or new_rank > 6:
                    return finish_command(m, "dk_err", bot.send_message(cid, "⚠️ Ранг должен быть от 0 до 6."), ttl=10)
                old = get_command_threshold(cid, code)
                set_command_threshold(cid, code, new_rank)
                save_dk_log(cid, uid, fname, code, old, new_rank)
                return finish_command(m, "dk_ok", bot.send_message(cid, f"⚙️ <b>ДК обновлён</b>\n\nКоманда: <code>{html.escape(code)}</code>\nБыло: <b>{old}</b>\nСтало: <b>{new_rank}</b>\n\n0 — всем • 1–5 — с ранга • 6 — выключено", parse_mode="HTML"))
            return finish_command(m, "dk", bot.send_message(cid, format_dk_list(cid), parse_mode="HTML"), ttl=120)

        # Включение/отключение команды по рангу: +дк команда [ранг] / -дк команда [ранг]
        if cmd in ["+дк", "-дк"]:
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            if len(parts) < 2:
                return finish_command(m, "dk_err", bot.send_message(cid, "⚠️ Формат: +дк <команда> <0-6> или -дк <команда> <0-6>"), ttl=15)
            code = DK_ALIASES.get(parts[1].lower(), parts[1].lower())
            new_rank = 0 if cmd == "+дк" else 6
            if len(parts) >= 3 and parts[2].isdigit():
                new_rank = int(parts[2])
            if not 0 <= new_rank <= 6:
                return finish_command(m, "dk_err", bot.send_message(cid, "⚠️ Ранг должен быть от 0 до 6."), ttl=10)
            old = get_command_threshold(cid, code)
            set_command_threshold(cid, code, new_rank)
            save_dk_log(cid, uid, fname, code, old, new_rank)
            return finish_command(m, "dk_ok", bot.send_message(cid, f"⚙️ <b>ДК обновлён</b>\n<code>{html.escape(code)}</code>: <b>{old}</b> → <b>{new_rank}</b>\n\n0 — всем • 1–5 — с ранга • 6 — выключено", parse_mode="HTML"))

        # Личный доступ: +лдк команда @user / -лдк команда @user
        if cmd in ["+лдк", "-лдк"]:
            if not command_allowed_by_dk(cid, uid, "личный дк"):
                return reply_no_rights(m)
            target_uid, target_name, _ = extract_target_and_args(m, parts)
            if not target_uid or len(parts) < 2:
                return finish_command(m, "ldk_err", bot.send_message(cid, "⚠️ Формат: +лдк <команда> <пользователь> или -лдк <команда> <пользователь>"), ttl=15)
            code = DK_ALIASES.get(parts[1].lower(), parts[1].lower())
            enabled = cmd == "+лдк"
            set_personal_dk(cid, target_uid, code, enabled)
            return finish_command(m, "ldk", bot.send_message(cid, f"👤 <b>Личный доступ</b>\n{get_user_mention(user_id=target_uid, first_name=target_name)}: <code>{html.escape(code)}</code> — {'разрешено' if enabled else 'запрещено'}.", parse_mode="HTML"))

        # Импорт/расширенный просмотр доступа команд (локальная реализация Iris-style).
        if t_lower.startswith("импорт команд ") or t_lower.startswith("импорт дк "):
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            try:
                source_id = int(t.split(None, 2)[2].strip())
            except Exception:
                return finish_command(m, "dk_import_err", bot.send_message(cid, "⚠️ Формат: <code>Импорт команд ID_чата</code>.", parse_mode="HTML"), ttl=15)
            if source_id == cid:
                return finish_command(m, "dk_import_err", bot.send_message(cid, "⚠️ Нельзя импортировать настройки из этого же чата."), ttl=10)
            all_access = db_get("command_access", {})
            source = all_access.get(str(source_id))
            if source is None:
                return finish_command(m, "dk_import_err", bot.send_message(cid, "⚠️ Для указанного чата нет сохранённых пользовательских настроек ДК."), ttl=15)
            old = dict(all_access.get(str(cid), {}))
            all_access[str(cid)] = dict(source)
            db_set("command_access", all_access)
            save_dk_log(cid, uid, fname, "*import*", len(old), len(source))
            return finish_command(m, "dk_import", bot.send_message(cid, f"📥 <b>ДК импортирован</b>\nСкопировано настроек: <b>{len(source)}</b>.\nИсточник: <code>{source_id}</code>", parse_mode="HTML"), ttl=20)

        # Лог ДК: фильтрация по команде/автору, как в Iris.
        if t_lower.startswith("лог дк") or t_lower.startswith("лог доступа"):
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            q = t.split(None, 2)[2].strip().lower() if len(t.split(None, 2)) >= 3 else ""
            rows = db_get("dk_log", {}).get(str(cid), [])
            if q:
                if q.startswith("@"): q = q[1:]
                rows = [r for r in rows if q in str(r.get("command", "")).lower() or q in str(r.get("name", "")).lower() or q == str(r.get("uid", ""))]
            rows = rows[-50:]
            if not rows:
                text = "⚙️ <b>ЛОГ ДК</b>\n\nЗаписей не найдено."
            else:
                lines=[]
                for r in reversed(rows):
                    dt=datetime.fromtimestamp(float(r.get("date",0)),KYIV_TZ).strftime("%d.%m %H:%M")
                    lines.append(f"• {dt} — {html.escape(str(r.get('name','Пользователь')))}: <code>{html.escape(str(r.get('command','?')))}</code> {r.get('old','?')} → {r.get('new','?')}")
                text="⚙️ <b>ЛОГ ДК</b>\n\n"+"\n".join(lines)
            return finish_command(m,"dk_log_filter",bot.send_message(cid,text,parse_mode="HTML"),ttl=90)

        # Просмотр пользователей, которым выдано исключение для конкретной команды.
        if t_lower.startswith("лдк ") and len(t.split()) >= 2 and not t_lower.startswith(("лдк помощь",)):
            words=t.split()
            if len(words) == 2 and words[1].lower() not in ("@",):
                if get_admin_rank(cid, uid) < 5:
                    return reply_no_rights(m)
                code=DK_ALIASES.get(words[1].lower(), words[1].lower())
                pdata=db_get("personal_command_access", {}).get(str(cid), {})
                rows=[]
                for suid, rules in pdata.items():
                    if code in rules:
                        rows.append(f"• <code>{suid}</code> — {'разрешено' if rules[code] else 'запрещено'}")
                text=f"👤 <b>ЛДК: {html.escape(code)}</b>\n\n"+("\n".join(rows[:100]) if rows else "Исключений для этой команды нет.")
                return finish_command(m,"ldk_command_users",bot.send_message(cid,text,parse_mode="HTML"),ttl=60)

        # Проверка ДК для известных команд выполняется до их обработчика.
        dk_code = DK_ALIASES.get(cmd, DK_COMMANDS.get(cmd))
        if dk_code and not command_allowed_by_dk(cid, uid, dk_code):
            return reply_no_rights(m)

        # Дополнительные Iris-команды управления доступом.
        if cmd in ("!сброс команд", "сброс команд"):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            data=db_get("command_access", {}); old=data.pop(str(cid), {})
            db_set("command_access", data)
            save_dk_log(cid, uid, fname, "*", len(old), 0)
            return finish_command(m,"dk_reset",bot.send_message(cid,"♻️ Настройки доступа команд сброшены к значениям по умолчанию."),ttl=20)

        if cmd in ("+команды", "-команды"):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            enabled=cmd=="+команды"; set_chat_setting(cid,"dk_notifications",enabled)
            return finish_command(m,"dk_notifications",bot.send_message(cid,f"🔔 Оповещения о доступности команд {'включены' if enabled else 'выключены'}."),ttl=15)

        if cmd in ("+рп", "-рп", "+браки", "-браки"):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            key="rp_enabled" if cmd.endswith("рп") else "marriages_enabled"
            enabled=cmd.startswith("+"); set_chat_setting(cid,key,enabled)
            label="РП-команды" if key=="rp_enabled" else "Браки"
            return finish_command(m,"module_toggle",bot.send_message(cid,f"⚙️ {label} {'включены' if enabled else 'выключены'}."),ttl=15)

        if cmd in ("+дк в лс", "-дк в лс"):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            enabled=cmd.startswith("+"); set_chat_setting(cid,"dk_in_private",enabled)
            return finish_command(m,"dk_private",bot.send_message(cid,f"💬 Редактирование ДК через ЛС {'разрешено' if enabled else 'запрещено'}."),ttl=15)

        if cmd.startswith("лдк ") and cmd not in ("лдк помощь",):
            parts2=t.split()
            if len(parts2)==2:
                target_uid,target_name,_=extract_target_and_args(m,parts2)
                if target_uid:
                    rows=get_personal_dk(cid,target_uid)
                    lines=[f"• <code>{html.escape(str(k))}</code> — {'разрешено' if v else 'запрещено'}" for k,v in rows.items()]
                    text=f"👤 <b>ЛИЧНЫЙ ДОСТУП</b> {get_user_mention(user_id=target_uid,first_name=target_name)}\n\n"+("\n".join(lines) if lines else "Исключений нет.")
                    return finish_command(m,"ldk_view",bot.send_message(cid,text,parse_mode="HTML"),ttl=30)

        if cmd in ("все лдк", "!сброс всех лдк"):
            if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
            data=db_get("personal_command_access", {})
            if cmd=="!сброс всех лдк":
                data.pop(str(cid),None); db_set("personal_command_access",data)
                return finish_command(m,"ldk_reset_all",bot.send_message(cid,"♻️ Все личные доступы команд сброшены."),ttl=15)
            users=data.get(str(cid),{})
            if not users: text="👤 Личных исключений нет."
            else:
                text="👤 <b>ВСЕ ЛИЧНЫЕ ДОСТУПЫ</b>\n\n"+"\n".join(f"• <code>{u}</code>: {len(v)} настроек" for u,v in list(users.items())[:50])
            return finish_command(m,"ldk_all",bot.send_message(cid,text,parse_mode="HTML"),ttl=30)

        # --- СИСТЕМА АДМИНИСТРИРОВАНИЯ ---
        if cmd in ["+модер", "!модер", "+админ", "повысить", "понизить", "разжаловать", "снять", "снять всех", "пред", "варн", "/warn", "предупреждение", "варны", "мои варны", "варнлист", "снять варны", "снять все варны", "снять варн", "/unwarn", "мут", "/mute", "муты", "проверить мут", "снять мут", "/unmute", "бан", "/ban", "банлист", "разбан", "вернуть", "снятьбан", "/unban", "причина", "кик", "/kick", "кик тихо", "амнистия", "кто", "кто админ", "админы", "кто назначил", "созвать модеров", "позвать модеров", "модер лог", "мой модер лог", "права", "/rank_perms", "rank_perms", "дк", "/дк", "!дк", ".дк", "доступ", "мой дк", "мдк", "мой доступ команд", "доступ команд", "+дк", "-дк", "+лдк", "-лдк", "лог дк", "импорт команд", "импорт дк", "лог доступа", "твой модер лог", "модер лог от"]:
            if cmd == "кто" and len(parts) > 1 and parts[1].lower() == "админ": cmd = "кто админ"
            if cmd == "снять" and len(parts) > 1 and parts[1].lower() == "мут": cmd = "снять мут"
            elif cmd == "снять" and len(parts) > 1 and parts[1].lower() == "пред": cmd = "снять пред"
            elif cmd == "варн": cmd = "пред"
            elif cmd == "предупреждение": cmd = "пред"
            elif cmd == "разбан" or cmd == "вернуть": cmd = "снятьбан"
            elif cmd == "проверить" and len(parts) > 1 and parts[1].lower() == "мут": cmd = "проверить мут"
            elif cmd == "муты": cmd = "муты"
            elif cmd == "банлист": cmd = "банлист"
            elif cmd == "варнлист": cmd = "варнлист"
            elif cmd == "мои" and len(parts) > 1 and parts[1].lower() == "варны": cmd = "мои варны"
            elif cmd == "модер" and len(parts) > 1 and parts[1].lower() == "лог":
                cmd = "модер лог от" if len(parts) > 2 and parts[2].lower() == "от" else "модер лог"
            elif cmd == "твой" and len(parts) > 2 and parts[1].lower() == "модер" and parts[2].lower() == "лог": cmd = "твой модер лог"
            elif cmd == "кто" and len(parts) > 1 and parts[1].lower() == "назначил": cmd = "кто назначил"
            elif cmd in ["дк", "/дк", "!дк", ".дк"]: cmd = "доступ команд"
            elif cmd in ["мой дк", "мдк", "мой доступ команд"]: cmd = "мой доступ команд"
            elif cmd == "лог" and len(parts) > 1 and parts[1].lower() == "дк": cmd = "лог дк"
            
            if cmd in ["+модер", "!модер", "+админ"]:
                # Iris-compatible rank assignment: +модер [1-5] target
                rank = 1
                if len(parts) > 1 and parts[1].isdigit():
                    rank = max(1, min(5, int(parts[1])))
                target_uid, target_name, _ = extract_target_and_args(m, parts)
                if not target_uid:
                    return finish_command(m, "no_target", bot.send_message(cid, "⚠️ <b>Не указан пользователь</b>\nОтветь на его сообщение, укажи @username или ID."), ttl=10)
                caller_rank = get_admin_rank(cid, uid)
                if (caller_rank <= rank or not has_permission(cid, uid, "can_promote")):
                    return reply_no_rights(m)
                if target_uid == BOT_ID or (get_admin_rank(cid, target_uid) >= caller_rank):
                    return reply_no_rights(m)
                set_admin_rank(cid, target_uid, rank)
                save_moderation_record(cid, "rank", target_uid, target_name, uid, fname, "", rank)
                target = get_user_mention(user_id=target_uid, first_name=target_name)
                actor = get_user_mention(user_id=uid, first_name=fname)
                txt = f"👑 <b>VIBE-ПОВЫШЕНИЕ</b>\n{target} получает статус:\n<b>{moderation_rank_name(rank)}</b>\n🛡 Назначил: {actor}"
                return finish_command(m, "admin_change", bot.send_message(cid, txt, parse_mode="HTML"))

            if cmd in ["кто админ", "админы"]:
                admins = db_get("chat_admins", {}).get(str(cid), {})
                try:
                    for adm in bot.get_chat_administrators(cid):
                        if adm.status == 'creator' and str(adm.user.id) not in admins:
                            set_admin_rank(cid, adm.user.id, 5)
                            admins[str(adm.user.id)] = 5
                except: pass
                
                users_db = db_get("users_data", {})
                grouped = {5: [], 4: [], 3: [], 2: [], 1: []}
                for a_uid, rank in admins.items():
                    if rank in grouped:
                        name = users_db.get(str(a_uid), {}).get("name", "Неизвестный")
                        grouped[rank].append(get_user_mention(user_id=int(a_uid), first_name=name))
                        
                txt = "━━━━━━━VIBE━━━━━━━\n👑 <b>АДМИНИСТРАЦИЯ ЧАТА</b>\n\n"
                for r in sorted(ADMIN_RANKS.keys(), reverse=True):
                    if grouped[r]:
                        perms = get_rank_permissions(cid, r)
                        active_perms = [PERM_NAMES[p] for p, v in perms.items() if v]
                        perms_str = f" <i>({', '.join(active_perms)})</i>" if active_perms else ""
                        txt += f"<b>{ADMIN_RANKS[r]}</b>{perms_str}\n" + "\n".join(f"• {u}" for u in grouped[r]) + "\n\n"
                txt += "━━━━━━━VIBE━━━━━━━"
                return finish_command(m, "who_admin", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)

            elif cmd == "кто назначил":
                if not command_allowed_by_dk(cid, uid, "кто назначил"):
                    return reply_no_rights(m)
                target_uid, target_name, _ = extract_target_and_args(m, parts)
                if not target_uid:
                    return finish_command(m, "who_assigned_err", bot.send_message(cid, "⚠️ <b>Не указан пользователь</b>\nОтветь на его сообщение, укажи @username или ID."), ttl=10)
                rows = [r for r in db_get("moderation_log", {}).get(str(cid), []) if r.get("action") == "rank" and r.get("target_uid") == target_uid]
                rows.sort(key=lambda x: x.get("date", 0), reverse=True)
                if rows:
                    r = rows[0]
                    actor = get_user_mention(user_id=r.get("actor_uid"), first_name=r.get("actor_name", "Модератор"))
                    txt = f"👑 <b>КТО НАЗНАЧИЛ</b>\n\n{get_user_mention(user_id=target_uid, first_name=target_name)}\n🛡 Назначил: {actor}\n🏷 Ранг: <b>{moderation_rank_name(r.get('duration', 0))}</b>"
                else:
                    txt = f"ℹ️ История назначения {get_user_mention(user_id=target_uid, first_name=target_name)} не найдена."
                return finish_command(m, "who_assigned", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

            elif cmd in ["модер лог", "мой модер лог", "твой модер лог", "модер лог от"]:
                rows = db_get("moderation_log", {}).get(str(cid), [])
                if cmd == "мой модер лог":
                    rows = [r for r in rows if r.get("actor_uid") == uid]
                elif cmd == "твой модер лог":
                    target_uid, target_name, _ = extract_target_and_args(m, parts)
                    if not target_uid:
                        target_uid, target_name = uid, fname
                    rows = [r for r in rows if r.get("target_uid") == target_uid and r.get("action") == "rank"]
                elif cmd == "модер лог от":
                    target_uid, target_name, _ = extract_target_and_args(m, parts)
                    if not target_uid:
                        return finish_command(m, "modlog_err", bot.send_message(cid, "⚠️ Укажи модератора ответом или через @username/ID."), ttl=10)
                    rows = [r for r in rows if r.get("actor_uid") == target_uid]
                rows = rows[-30:][::-1]
                txt = "📜 <b>VIBE-МОДЕР ЛОГ</b>\n\n"
                for r in rows:
                    dt = datetime.fromtimestamp(r.get("date", time.time()), KYIV_TZ).strftime("%d.%m %H:%M")
                    action = html.escape(str(r.get("action", "—")))
                    target = html.escape(str(r.get("target_name", "—")))
                    actor = html.escape(str(r.get("actor_name", "—")))
                    txt += f"• {dt} — <b>{action}</b>: {target} ← {actor}\n"
                if not rows: txt += "Лог пока пуст."
                return finish_command(m, "modlog", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)

            elif cmd in ["варнлист", "мои варны"]:
                if not has_permission(cid, uid, "can_warn") and cmd == "варнлист":
                    return reply_no_rights(m)
                target_uid, target_name, _ = extract_target_and_args(m, parts)
                if cmd == "мои варны":
                    target_uid, target_name = uid, fname
                db = db_get("chat_warns", {}).get(str(cid), {})
                rows = []
                for a_uid, data in db.items():
                    if data.get("count", 0) > 0:
                        name = db_get("users_data", {}).get(str(a_uid), {}).get("name", f"ID:{a_uid}")
                        rows.append((data.get("count", 0), get_user_mention(user_id=int(a_uid), first_name=name)))
                if cmd == "мои варны":
                    data = db.get(str(target_uid), {"count": 0})
                    txt = f"⚠️ <b>Твои предупреждения</b>: {data.get('count', 0)}/{get_v(cid, 'max_warns', 3)}"
                else:
                    rows.sort(reverse=True)
                    txt = "⚠️ <b>VIBE-ВАРНЛИСТ</b>\n\n" + ("\n".join(f"• {u} — {n}" for n,u in rows[:50]) or "Пусто — чат чист.")
                return finish_command(m, "warnlist", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)

            elif cmd in ["снять варны", "снять все варны", "снять варн"]:
                if not has_permission(cid, uid, "can_warn"):
                    return reply_no_rights(m)
                target_uid, target_name, args = extract_target_and_args(m, parts)
                if not target_uid:
                    return finish_command(m, "no_target", bot.send_message(cid, "⚠️ <b>Не указан пользователь</b>\nОтветь на его сообщение, укажи @username или ID."), ttl=10)
                amount = None
                if cmd == "снять варны" and args and args[0].isdigit():
                    amount = int(args[0])
                removed, left = clear_warns(cid, target_uid, amount)
                target = get_user_mention(user_id=target_uid, first_name=target_name)
                txt = format_moderation_message("unwarn", get_user_mention(user_id=uid, first_name=fname), target, extra=f"🧹 Снято: {removed}\n⚠️ Осталось: {left}/{get_v(cid, 'max_warns', 3)}")
                save_moderation_record(cid, "unwarn", target_uid, target_name, uid, fname)
                return finish_command(m, "unwarn", bot.send_message(cid, txt, parse_mode="HTML"))

            elif cmd in ["муты", "проверить мут"]:
                if not has_permission(cid, uid, "can_mute"):
                    return reply_no_rights(m)
                if cmd == "проверить мут":
                    target_uid, target_name, _ = extract_target_and_args(m, parts)
                    if not target_uid:
                        target_uid, target_name = uid, fname
                    try:
                        member = bot.get_chat_member(cid, target_uid)
                        until = getattr(member, "until_date", None)
                        muted = getattr(member, "status", "") == "restricted" and until and until > int(time.time())
                        if muted:
                            left = format_seconds_human(until - int(time.time()))
                            txt = f"🔇 <b>VIBE-ПРОВЕРКА</b>\n{get_user_mention(user_id=target_uid, first_name=target_name)} сейчас в муте.\n⏱ Осталось: {left}"
                        else:
                            txt = f"🔊 <b>VIBE-ПРОВЕРКА</b>\n{get_user_mention(user_id=target_uid, first_name=target_name)} может говорить."
                    except Exception:
                        txt = "⚠️ Не удалось проверить ограничения пользователя."
                    return finish_command(m, "check_mute", bot.send_message(cid, txt, parse_mode="HTML"), ttl=30)
                db = db_get("chat_mutes", {}).get(str(cid), {})
                rows=[]
                now=time.time()
                for a_uid, until in db.items():
                    if until == 0 or until > now:
                        name=db_get("users_data", {}).get(str(a_uid), {}).get("name", f"ID:{a_uid}")
                        rows.append(f"• {get_user_mention(user_id=int(a_uid), first_name=name)} — {'навсегда' if until==0 else format_seconds_human(int(until-now))}")
                txt="🔇 <b>VIBE-МУТЫ</b>\n\n"+("\n".join(rows) or "Никто не замьючен.")
                return finish_command(m, "mutes", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)

            elif cmd == "банлист":
                if not has_permission(cid, uid, "can_ban"):
                    return reply_no_rights(m)
                db = db_get("chat_bans", {}).get(str(cid), {})
                now=time.time(); rows=[]
                for a_uid, data in db.items():
                    until=data.get("until", 0) if isinstance(data, dict) else data
                    if until == 0 or until > now:
                        name=(data.get("name") if isinstance(data,dict) else None) or db_get("users_data", {}).get(str(a_uid),{}).get("name",f"ID:{a_uid}")
                        rows.append(f"• {get_user_mention(user_id=int(a_uid), first_name=name)} — {'навсегда' if until==0 else format_seconds_human(int(until-now))}")
                txt="🚫 <b>VIBE-БАНЛИСТ</b>\n\n"+("\n".join(rows) or "Банлист пуст.")
                return finish_command(m, "banlist", bot.send_message(cid, txt, parse_mode="HTML"), ttl=120)

            elif cmd == "причина":
                if not has_permission(cid, uid, "can_ban"):
                    return reply_no_rights(m)
                target_uid, target_name, _ = extract_target_and_args(m, parts)
                if not target_uid:
                    return finish_command(m, "no_target", bot.send_message(cid, "⚠️ <b>Не указан пользователь</b>\nОтветь на его сообщение, укажи @username или ID."), ttl=10)
                db=db_get("chat_bans", {}).get(str(cid), {}).get(str(target_uid))
                if not db:
                    return finish_command(m, "ban_reason", bot.send_message(cid, "ℹ️ По этому пользователю нет сохранённой информации о бане."), ttl=20)
                actor=get_user_mention(user_id=db.get("by_uid"), first_name=db.get("by_name","Модератор")) if db.get("by_uid") else "неизвестен"
                txt=f"🚫 <b>Информация о бане</b>\n👤 {get_user_mention(user_id=target_uid, first_name=target_name)}\n📝 Причина: {html.escape(db.get('reason','Не указана'))}\n🛡 Модератор: {actor}"
                return finish_command(m, "ban_reason", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

            elif cmd == "амнистия":
                if not has_permission(cid, uid, "can_ban"):
                    return reply_no_rights(m)
                try:
                    for a_uid in list(db_get("chat_bans", {}).get(str(cid), {}).keys()):
                        bot.unban_chat_member(cid, int(a_uid), only_if_banned=True)
                    bans=db_get("chat_bans", {}); bans[str(cid)]={}; db_set("chat_bans", bans)
                    txt=format_moderation_message("unban", get_user_mention(user_id=uid, first_name=fname), "всех заблокированных", extra="♻️ Банлист очищен.")
                    return finish_command(m, "amnesty", bot.send_message(cid, txt, parse_mode="HTML"))
                except Exception:
                    return finish_command(m, "amnesty_err", bot.send_message(cid, "⚠️ Не удалось завершить амнистию. Проверь права бота."), ttl=15)

            elif cmd in ["/rank_perms", "rank_perms"]:
                if len(parts) < 2 or not parts[1].isdigit():
                    return finish_command(m, "rank_perms_err", bot.send_message(cid, "⚠️ Укажите ранг (1-5), например: /rank_perms 3"), ttl=10)
                r_num = int(parts[1])
                if r_num < 1 or r_num > 5:
                    return finish_command(m, "rank_perms_err", bot.send_message(cid, "⚠️ Ранг должен быть от 1 до 5."), ttl=10)
                
                perms = get_rank_permissions(cid, r_num)
                txt = f"⚙️ <b>ПРАВА РАНГА {r_num} ({ADMIN_RANKS[r_num]}):</b>\n\n"
                for p_key, p_name in PERM_NAMES.items():
                    status = "✅" if perms.get(p_key, False) else "❌"
                    txt += f"{status} <code>{p_key}</code> — {p_name}\n"
                return finish_command(m, "rank_perms", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

            elif cmd == "права":
                if get_admin_rank(cid, uid) < 5: return reply_no_rights(m)
                if len(parts) < 4:
                    return finish_command(m, "права_err", bot.send_message(cid, "⚠️ Формат: права <ранг> <permission> вкл|выкл\nПример: права 2 can_ban вкл"), ttl=15)
                r_num_str, p_key, p_val_str = parts[1], parts[2].lower(), parts[3].lower()
                if not r_num_str.isdigit() or not (1 <= int(r_num_str) <= 5):
                    return finish_command(m, "права_err", bot.send_message(cid, "⚠️ Ранг должен быть от 1 до 5."), ttl=10)
                if p_key not in PERM_NAMES:
                    return finish_command(m, "права_err", bot.send_message(cid, f"⚠️ Неизвестное право: {p_key}.\nДоступные: {', '.join(PERM_NAMES.keys())}"), ttl=15)
                if p_val_str not in ["вкл", "выкл"]:
                    return finish_command(m, "права_err", bot.send_message(cid, "⚠️ Значение должно быть 'вкл' или 'выкл'."), ttl=10)
                    
                r_num = int(r_num_str)
                new_val = (p_val_str == "вкл")
                set_rank_permission(cid, r_num, p_key, new_val)
                
                perms = get_rank_permissions(cid, r_num)
                txt = f"✅ <b>Права обновлены для ранга {r_num} ({ADMIN_RANKS[r_num]})</b>\n\n"
                for pk, p_name in PERM_NAMES.items():
                    status = "✅" if perms.get(pk, False) else "❌"
                    txt += f"{status} <code>{pk}</code> — {p_name}\n"
                return finish_command(m, "права_ok", bot.send_message(cid, txt, parse_mode="HTML"))

            elif cmd in CMD_PERM_MAP or cmd in ["варны", "/warns"]:
                req_perm = CMD_PERM_MAP.get(cmd)
                caller_rank = get_admin_rank(cid, uid)
                
                if req_perm and not has_permission(cid, uid, req_perm):
                    return reply_no_rights(m)
                    
                target_uid, target_name, args = extract_target_and_args(m, parts)
                
                if not target_uid:
                    if cmd in ["варны", "/warns"]:
                        target_uid, target_name = uid, fname
                    else:
                        return finish_command(m, "no_target", bot.send_message(cid, "⚠️ Укажите пользователя (ответом на сообщение или через @юзернейм/ID)."), ttl=10)
                        
                target_cur_rank = get_admin_rank(cid, target_uid)
                # Отдельно берём именно СОХРАНЁННЫЙ в базе ранг для текста
                # "повышен/понижен". get_admin_rank может вернуть 5 для
                # создателя чата (creator-фолбэк), даже если в базе у него
                # другой ранг — из-за этого сравнение new_rank>target_cur_rank
                # ломалось (всегда "понижен").
                with state_lock:
                    target_stored_rank = db_get("chat_admins", {}).get(str(cid), {}).get(str(target_uid), 0)
                
                if cmd not in ["варны", "/warns"]:
                    if target_uid == BOT_ID or (target_uid == uid and cmd not in ["снять мут", "/unmute", "снятьбан", "/unban"]):
                        msg = bot.send_message(cid, "⛔ Это действие нельзя применить к данной цели.")
                        return finish_command(m, "invalid_target", msg, ttl=5)
                        
                    if get_admin_rank(cid, uid) < 5:
                        if get_admin_rank(cid, target_uid) >= 5 or caller_rank <= target_cur_rank:
                            return reply_no_rights(m)

                if cmd in ["повысить", "понизить", "разжаловать"]:
                    if cmd == "разжаловать": new_rank = 0
                    else:
                        if args and args[0].isdigit(): new_rank = int(args[0])
                        else: new_rank = target_cur_rank + 1 if cmd == "повысить" else target_cur_rank - 1
                            
                    if new_rank > 5: new_rank = 5
                    if new_rank < 0: new_rank = 0
                    
                    if get_admin_rank(cid, uid) < 5:
                        if caller_rank <= new_rank and new_rank != 0: return reply_no_rights(m)
                            
                    set_admin_rank(cid, target_uid, new_rank)
                    save_moderation_record(cid, "rank", target_uid, target_name, uid, fname, "", new_rank)
                    if new_rank == 0: txt = f"📉 {get_user_mention(user_id=target_uid, first_name=target_name)} разжалован до обычного участника."
                    else:
                        rank_name = ADMIN_RANKS.get(new_rank, f"{new_rank} РАНГ")
                        act = "повышен" if new_rank >= target_stored_rank else "понижен"
                        icon = "📈" if act == "повышен" else "📉"
                        txt = f"{icon} {get_user_mention(user_id=target_uid, first_name=target_name)} {act} до должности:\n<b>{rank_name}</b>"
                    return finish_command(m, "admin_change", bot.send_message(cid, txt, parse_mode="HTML"))
                    
                elif cmd in ["бан", "/ban"]:
                    dur_secs, parsed, consumed = parse_duration_from_args(args)
                    if parsed:
                        reason = " ".join(args[consumed:]) if len(args) > consumed else "Не указана"
                        time_str = format_seconds_human(dur_secs) if dur_secs > 0 else "навсегда"
                    else:
                        reason = " ".join(args) if args else "Не указана"
                        dur_secs = int(get_v(cid, "ban_period", 0) or 0)
                        time_str = format_seconds_human(dur_secs) if dur_secs > 0 else "навсегда"
                        
                    until = int(time.time() + dur_secs) if dur_secs > 0 else 0
                    try:
                        bot.ban_chat_member(cid, target_uid, until_date=until)
                        with state_lock:
                            bans = db_get("chat_bans", {}); chat_bans = bans.setdefault(str(cid), {})
                            chat_bans[str(target_uid)] = {"until": until, "reason": reason, "by_uid": uid, "by_name": fname, "name": target_name, "date": time.time()}
                            db_set("chat_bans", bans)
                        mention_admin = get_user_mention(user_id=uid, first_name=fname)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = format_moderation_message("ban", mention_admin, mention_target, time_str, reason)
                        log_moderation_action(m.chat.title or str(cid), txt)
                        save_moderation_record(cid, "ban", target_uid, target_name, uid, fname, reason, dur_secs)
                        with state_lock:
                            history=db_get("ban_history", {}); rows=history.setdefault(str(cid), [])
                            rows.append({"target_uid":target_uid,"target_name":target_name,"by_uid":uid,"by_name":fname,"reason":reason,"duration":dur_secs,"date":time.time()})
                            db_set("ban_history", rows[-500:])
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "ban", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["снятьбан", "/unban"]:
                    try:
                        bot.unban_chat_member(cid, target_uid, only_if_banned=True)
                        with state_lock:
                            bans = db_get("chat_bans", {}); bans.setdefault(str(cid), {}).pop(str(target_uid), None); db_set("chat_bans", bans)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = f"✅ Снят бан с {mention_target}"
                        log_moderation_action(m.chat.title or str(cid), txt)
                        save_moderation_record(cid, "unban", target_uid, target_name, uid, fname, "", 0)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "unban", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["кик", "/kick"]:
                    silent = bool(args and args[0].lower() == "тихо")
                    reason = " ".join(args[1:] if silent else args) or "Не указана"
                    try:
                        bot.ban_chat_member(cid, target_uid, until_date=0)
                        bot.unban_chat_member(cid, target_uid, only_if_banned=True)
                        mention_admin = get_user_mention(user_id=uid, first_name=fname)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = format_moderation_message("kick", mention_admin, mention_target, reason=reason)
                        log_moderation_action(m.chat.title or str(cid), txt)
                        save_moderation_record(cid, "kick", target_uid, target_name, uid, fname, reason, 0)
                        if silent:
                            return finish_command(m, "kick_silent", None)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "kick", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["мут", "/mute"]:
                    dur_secs, parsed, consumed = parse_duration_from_args(args)
                    if parsed:
                        reason = " ".join(args[consumed:]) if len(args) > consumed else "Не указана"
                        time_str = format_seconds_human(dur_secs) if dur_secs > 0 else "навсегда"
                    else:
                        reason = " ".join(args) if args else "Не указана"
                        dur_secs = int(get_v(cid, "mute_period", 7 * 86400) or 0)
                        time_str = format_seconds_human(dur_secs) if dur_secs > 0 else "навсегда"
                        
                    until = int(time.time() + dur_secs) if dur_secs > 0 else 0
                    try:
                        bot.restrict_chat_member(cid, target_uid, until_date=until, permissions=ChatPermissions(can_send_messages=False))
                        with state_lock:
                            mutes = db_get("chat_mutes", {}); chat_mutes = mutes.setdefault(str(cid), {})
                            chat_mutes[str(target_uid)] = until
                            db_set("chat_mutes", mutes)
                        mention_admin = get_user_mention(user_id=uid, first_name=fname)
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        txt = format_moderation_message("mute", mention_admin, mention_target, time_str, reason)
                        log_moderation_action(m.chat.title or str(cid), txt)
                        save_moderation_record(cid, "mute", target_uid, target_name, uid, fname, reason, dur_secs)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "mute", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["снять мут", "/unmute"]:
                    try:
                        bot.restrict_chat_member(cid, target_uid, permissions=ChatPermissions(
                            can_send_messages=True, can_send_media_messages=True, 
                            can_send_other_messages=True, can_add_web_page_previews=True))
                        mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                        with state_lock:
                            mutes = db_get("chat_mutes", {}); mutes.setdefault(str(cid), {}).pop(str(target_uid), None); db_set("chat_mutes", mutes)
                        txt = format_moderation_message("unmute", get_user_mention(user_id=uid, first_name=fname), mention_target)
                        log_moderation_action(m.chat.title or str(cid), txt)
                    except: txt = "⚠️ Не удалось выполнить действие. Проверь права бота в этом чате."
                    return finish_command(m, "unmute", bot.send_message(cid, txt, parse_mode="HTML"))

                elif cmd in ["пред", "/warn"]:
                    warn_count = 1
                    if args and args[0].isdigit():
                        warn_count = int(args[0])
                        reason = " ".join(args[1:]) or "Не указана"
                    else:
                        reason = " ".join(args) or "Не указана"
                    issue_warn(cid, m.chat.title, target_uid, target_name, uid, fname, reason, m, warn_count)
                    return

                elif cmd in ["снять пред", "/unwarn"]:
                    with state_lock:
                        db = db_get("chat_warns", {})
                        cw = db.setdefault(str(cid), {})
                        uw = cw.setdefault(str(target_uid), {"count": 0, "history": []})
                        if uw["count"] > 0:
                            uw["count"] -= 1
                            if uw["history"]: uw["history"].pop()
                            count = uw["count"]
                            max_warns = get_v(cid, "max_warns", 3)
                            db_set("chat_warns", db)
                            mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                            txt = f"✅ Снято предупреждение с {mention_target} ({count}/{max_warns})"
                            log_moderation_action(m.chat.title or str(cid), txt)
                        else:
                            txt = "🤷‍♀️ У пользователя нет предупреждений."
                    return finish_command(m, "unwarn", bot.send_message(cid, txt, parse_mode="HTML"))
                    
                elif cmd in ["варны", "/warns"]:
                    with state_lock:
                        db = db_get("chat_warns", {})
                        cw = db.get(str(cid), {})
                        uw = cw.get(str(target_uid), {"count": 0, "history": []})
                        count = uw.get("count", 0)
                        history = uw.get("history", [])
                    
                    max_warns = get_v(cid, "max_warns", 3)
                    mention_target = get_user_mention(user_id=target_uid, first_name=target_name)
                    
                    txt = f"⚠️ <b>Предупреждения</b> {mention_target} ({count}/{max_warns}):\n"
                    if history:
                        for idx, item in enumerate(history[-5:], 1):
                            dt_str = datetime.fromtimestamp(item["date"], KYIV_TZ).strftime('%d.%m.%Y %H:%M')
                            txt += f"{idx}. {item['reason']} (выдал {html.escape(item['by_name'])} {dt_str})\n"
                    else:
                        txt += "\nИстория пуста."
                    return finish_command(m, "warns", bot.send_message(cid, txt, parse_mode="HTML"))

        security_result = _admin_security_command(m, t_lower, t)
        if security_result is not None:
            return security_result

        tg_result = _telegram_admin_command(m, t_lower, t)
        if tg_result is not None:
            return tg_result




        economy_result = _economy_command(m, t_lower, t)
        if economy_result is not None:
            return economy_result







        # --- IRIS: ники, баны и служебная статистика ---
        if t_lower == "ники" or t_lower.startswith("ники "):
            profiles = db_get("chat_profiles", {}).get(str(cid), {})
            page = 1
            parts_n = t_lower.split()
            if len(parts_n) > 1 and parts_n[1].isdigit(): page = max(1, int(parts_n[1]))
            rows=[]
            for suid, prof in profiles.items():
                nick=(prof or {}).get("nickname")
                if nick: rows.append((nick.lower(), int(suid), nick))
            rows.sort()
            per=20; start=(page-1)*per; chunk=rows[start:start+per]
            lines=[f"{start+i}. {get_user_mention(user_id=suid)} — <b>{html.escape(nick)}</b>" for i,( _,suid,nick) in enumerate(chunk,1)]
            total_pages=max(1,(len(rows)+per-1)//per)
            txt=f"🏷 <b>НИКИ</b> · страница {page}/{total_pages}\n\n" + ("\n".join(lines) if lines else "Ников пока нет.")
            return finish_command(m,"nicks_list",bot.send_message(cid,txt,parse_mode="HTML"),ttl=60)

        if t_lower == "!сброс ников":
            if get_admin_rank(cid,uid) < 5: return reply_no_rights(m)
            profiles=db_get("chat_profiles",{}); chat_profiles=profiles.setdefault(str(cid),{})
            removed=0
            for prof in chat_profiles.values():
                if isinstance(prof,dict) and prof.pop("nickname",None) is not None: removed+=1
            db_set("chat_profiles",profiles)
            return finish_command(m,"nicks_reset",bot.send_message(cid,f"🧹 Ники сброшены. Удалено: <b>{removed}</b>.",parse_mode="HTML"),ttl=20)

        if t_lower in ("мои баны", "мой бан"):
            history=db_get("ban_history",{}).get(str(cid),[]) or []
            mine=[v for v in history if int((v or {}).get("target_uid",0) or 0)==uid]
            mine.sort(key=lambda x: float((x or {}).get("date",0) or 0), reverse=True)
            lines=[]
            for i,v in enumerate(mine[:20],1):
                reason=html.escape(str(v.get("reason") or "без причины"))
                name=html.escape(str(v.get("target_name") or "пользователь"))
                dt=datetime.fromtimestamp(float(v.get("date",0) or 0),KYIV_TZ).strftime("%d.%m.%Y %H:%M")
                lines.append(f"{i}. <b>{name}</b> · {reason} · {dt}")
            return finish_command(m,"my_bans",bot.send_message(cid,"🔨 <b>МОИ БАНЫ</b>\n\n"+("\n".join(lines) if lines else "История банов пока пуста."),parse_mode="HTML"),ttl=45)

        if t_lower == "мой спам":
            spam=db_get("chat_spam",{}).get(str(cid),{})
            row=spam.get(str(uid),{}) if isinstance(spam,dict) else {}
            count=int(row.get("count",0) or 0) if isinstance(row,dict) else 0
            if not count:
                records=db_get("moderation_log",{}).get(str(cid),[]) or []
                count=sum(1 for r in records if int((r or {}).get("target_uid",0) or 0)==uid and str((r or {}).get("action","")).lower() in ("spam","antispam","спам"))
            return finish_command(m,"my_spam",bot.send_message(cid,f"🚫 <b>МОЙ СПАМ</b>\n\nЗафиксировано нарушений: <b>{count}</b>.",parse_mode="HTML"),ttl=30)

        # --- IRIS: расширенная статистика ---
        if t_lower in ("+стата", "+статистика"):
            set_v(cid, "stats_enabled", True)
            return finish_command(m, "stats_on", bot.send_message(cid, "📊 Статистика Лизы включена для этого чата."), ttl=15)
        if t_lower in ("-стата", "-статистика"):
            set_v(cid, "stats_enabled", False)
            return finish_command(m, "stats_off", bot.send_message(cid, "📊 Статистика Лизы выключена. История не удалена."), ttl=15)
        if t_lower == "!актив ириса":
            activity = db_get("chat_activity", {}).get(str(cid), {})
            active = sum(1 for row in activity.values() if time.time() - float(row.get("last_seen", 0) or 0) <= 15*60)
            total = len(activity)
            txt = f"📊 <b>АКТИВ ИРИСА</b>\n\n👥 Участников в статистике: <b>{total}</b>\n🟢 Активны за 15 минут: <b>{active}</b>"
            return finish_command(m, "iris_active", bot.send_message(cid, txt, parse_mode="HTML"), ttl=30)
        if t_lower == "чат инфо":
            try: member_count = bot.get_chat_member_count(cid)
            except Exception: member_count = len(db_get("chat_activity", {}).get(str(cid), {}))
            txt = f"ℹ️ <b>ИНФОРМАЦИЯ О ЧАТЕ</b>\n\nНазвание: <b>{html.escape(m.chat.title or 'Без названия')}</b>\nID: <code>{cid}</code>\nУчастников: <b>{member_count}</b>"
            return finish_command(m, "chat_info", bot.send_message(cid, txt, parse_mode="HTML"), ttl=45)
        if t_lower.startswith("чат стата"):
            parts2=t_lower.split()
            days=1
            if len(parts2)>2 and parts2[2].isdigit(): days=max(1,min(365,int(parts2[2])))
            since=time.time()-days*86400
            rows=[]
            for suid,row in db_get("chat_activity", {}).get(str(cid), {}).items():
                daily=row.get("daily",{}) or {}
                count=sum(int(v or 0) for k,v in daily.items() if datetime.strptime(k,"%Y-%m-%d").replace(tzinfo=KYIV_TZ).timestamp()>=since) if daily else 0
                if count: rows.append((count,int(suid),row.get("name","Участник")))
            rows.sort(reverse=True)
            total=sum(x[0] for x in rows)
            lines=[f"{i}. {get_user_mention(user_id=u, first_name=n)} — <b>{c}</b>" for i,(c,u,n) in enumerate(rows[:15],1)]
            txt=f"📈 <b>СТАТИСТИКА ЧАТА ЗА {days} ДН.</b>\n\nСообщений: <b>{total}</b>\n" + ("\n".join(lines) if lines else "Нет данных за этот период.")
            return finish_command(m,"chat_stats_period",bot.send_message(cid,txt,parse_mode="HTML"),ttl=90)
        # Топ N за период — совместимый локальный вариант Iris: сутки/неделя/месяц/вся.
        m_top_period = re.match(r"^топ\s+(\d+)(?:\s+)?(?:за\s+)?(сутки|день|неделю|неделя|месяц|всю|вся|всё|все)?$", t_lower)
        if m_top_period:
            limit = max(1, min(100, int(m_top_period.group(1))))
            period_word = (m_top_period.group(2) or "вся")
            period = 1 if period_word in ("сутки", "день") else 7 if period_word in ("неделю", "неделя") else 30 if period_word == "месяц" else 36500
            cutoff = datetime.fromtimestamp(time.time() - period * 86400, KYIV_TZ).date()
            activity = db_get("chat_activity", {}).get(str(cid), {})
            rows = []
            for suid, row in activity.items():
                daily = row.get("daily", {}) or {}
                count = 0
                for day, value in daily.items():
                    try:
                        if datetime.strptime(day, "%Y-%m-%d").date() >= cutoff:
                            count += int(value or 0)
                    except Exception:
                        continue
                if count:
                    rows.append((count, int(suid), row.get("name", "Участник")))
            rows.sort(key=lambda x: x[0], reverse=True)
            label = "сутки" if period == 1 else "неделю" if period == 7 else "месяц" if period == 30 else "всё время"
            lines = [f"{i}. {get_user_mention(user_id=u, first_name=n)} — <b>{count}</b>" for i, (count, u, n) in enumerate(rows[:limit], 1)]
            txt = f"🏆 <b>ТОП {limit} ЗА {label.upper()}</b>\n\n" + ("\n".join(lines) if lines else "Нет данных за этот период.")
            return finish_command(m, "top_period", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

        if t_lower in ("бтоп стата", "большой топ стата", "бтоп"):
            chat=db_get("chat_bcoins",{}).get(str(cid),{}) or {}; totals={}
            for item in (chat.get("history",[]) or []):
                suid=int(item.get("uid",0) or 0); totals[suid]=totals.get(suid,0)+int(item.get("amount",0) or 0)
            rows=sorted([(v,u) for u,v in totals.items()],reverse=True)
            lines=[f"{i}. {get_user_mention(user_id=u)} — <b>{v}</b> Бкоин" for i,(v,u) in enumerate(rows[:50],1)]
            return finish_command(m,"big_stats_top",bot.send_message(cid,"🏦 <b>БОЛЬШОЙ ТОП БКОИНОВ ЧАТА</b>\n\n"+("\n".join(lines) if lines else "В этом чате ещё не было депозитов."),parse_mode="HTML"),ttl=90)

        if t_lower in ("стата по часам", "стата по часам я", "чат стата по часам") or t_lower.startswith("стата по часам "):
            target_uid, target_name, _ = extract_target_and_args(m, parts)
            target_uid=target_uid or uid
            row=db_get("chat_activity", {}).get(str(cid), {}).get(str(target_uid),{})
            hourly=row.get("hourly",{}) or {}
            vals=[(int(hourly.get(f"{h:02d}",0) or 0),h) for h in range(24)]
            vals.sort(reverse=True)
            lines=[f"{h:02d}:00–{h:02d}:59 — <b>{c}</b>" for c,h in vals if c][:10]
            title="ТВОЯ СТАТИСТИКА ПО ЧАСАМ" if target_uid==uid else f"СТАТИСТИКА ПО ЧАСАМ — {html.escape(target_name or 'пользователь')}"
            txt=f"🕐 <b>{title}</b>\n\n" + ("\n".join(lines) if lines else "Пока недостаточно данных.")
            return finish_command(m,"hour_stats",bot.send_message(cid,txt,parse_mode="HTML"),ttl=60)
        if t_lower in ("стата сутки", "стата день", "стата за сутки") or t_lower in ("стата неделя", "стата за неделю") or t_lower in ("стата месяц", "стата за месяц") or t_lower in ("стата вся", "стата всё"):
            period=1 if "сут" in t_lower or "день" in t_lower else 7 if "нед" in t_lower else 30 if "меся" in t_lower else 36500
            row=db_get("chat_activity", {}).get(str(cid), {}).get(str(uid),{})
            daily=row.get("daily",{}) or {}
            cutoff=datetime.fromtimestamp(time.time()-period*86400,KYIV_TZ).date()
            count=sum(int(v or 0) for k,v in daily.items() if datetime.strptime(k,"%Y-%m-%d").date()>=cutoff)
            label="сутки" if period==1 else "неделю" if period==7 else "месяц" if period==30 else "всё время"
            return finish_command(m,"my_period_stats",bot.send_message(cid,f"📊 {get_user_mention(m.from_user)} за <b>{label}</b>: <b>{count}</b> сообщений.",parse_mode="HTML"),ttl=30)
        if t_lower in ("олды", "новички"):
            activity=db_get("chat_activity", {}).get(str(cid), {})
            rows=[]; now=time.time()
            for suid,row in activity.items():
                first=float(row.get("first_seen",now) or now)
                last=float(row.get("last_seen",0) or 0)
                rows.append((first,last,int(suid),row.get("name","Участник")))
            rows.sort(key=lambda x:x[0], reverse=(t_lower=="новички"))
            title="НОВИЧКИ" if t_lower=="новички" else "ОЛДЫ"
            lines=[]
            for i,(ts,last,u,n) in enumerate(rows[:30],1):
                flame=" 🔥" if t_lower=="олды" and last and now-last <= 30*86400 else ""
                lines.append(f"{i}. {get_user_mention(user_id=u,first_name=n)}{flame} — с {datetime.fromtimestamp(ts,KYIV_TZ).strftime('%d.%m.%Y')}")
            return finish_command(m,"olds_new",bot.send_message(cid,f"👥 <b>{title}</b>\n\n"+("\n".join(lines) if lines else "Нет данных."),parse_mode="HTML"),ttl=60)
        if t_lower == "!население":
            activity=db_get("chat_activity", {}).get(str(cid), {})
            return finish_command(m,"population",bot.send_message(cid,f"👥 В локальной статистике чата: <b>{len(activity)}</b> участников.",parse_mode="HTML"),ttl=30)

        if t_lower in ("кто вип", "кто не вип", "кто випы", "кто не випы"):
            activity=db_get("chat_activity",{}).get(str(cid),{})
            vips=db_get("user_vip",{}) or {}; now=time.time()
            want_vip=t_lower in ("кто вип","кто випы")
            rows=[]
            for suid,row in activity.items():
                active=float((vips.get(str(suid),{}) or {}).get("until",0) or 0)>now
                if active==want_vip: rows.append((int(suid),row.get("name","Участник")))
            title="VIP УЧАСТНИКИ" if want_vip else "УЧАСТНИКИ БЕЗ VIP"
            lines=[f"• {get_user_mention(user_id=u,first_name=n)}" for u,n in rows[:40]]
            return finish_command(m,"vip_users",bot.send_message(cid,f"💎 <b>{title}</b>\n\n"+("\n".join(lines) if lines else "Никого не найдено."),parse_mode="HTML"),ttl=60)

        if t_lower.startswith("список неактив") or t_lower.startswith("список молчунов") or t_lower.startswith("список по смс"):
            parts2=t_lower.split(); days=7
            if len(parts2)>2 and parts2[-1].isdigit(): days=max(1,min(365,int(parts2[-1])))
            activity=db_get("chat_activity",{}).get(str(cid),{}); now=time.time()
            if t_lower.startswith("список по смс"):
                # Iris-style: «Список по смс N период» — N участников с наименьшей активностью за период.
                nums=[int(x) for x in parts2[3:] if x.isdigit()]
                limit=max(1,min(100,nums[0] if nums else 30))
                period_days=max(1,min(365,nums[1] if len(nums)>1 else days))
                cutoff=datetime.fromtimestamp(time.time()-period_days*86400,KYIV_TZ).date()
                rows=[]
                for s,r in activity.items():
                    daily=r.get("daily",{}) or {}; count=0
                    for day,val in daily.items():
                        try:
                            if datetime.strptime(day,"%Y-%m-%d").date()>=cutoff: count+=int(val or 0)
                        except Exception: pass
                    rows.append((count,int(s),r.get("name","Участник")))
                rows.sort(key=lambda x:(x[0],x[1]))
                title="УЧАСТНИКИ ПО КОЛИЧЕСТВУ СООБЩЕНИЙ"
                lines=[f"{i}. {get_user_mention(user_id=s,first_name=n)} — <b>{c}</b>" for i,(c,s,n) in enumerate(rows[:limit],1)]
                days=period_days
            else:
                rows=[]
                for s,r in activity.items():
                    last=float(r.get("last_seen",0) or 0)
                    if not last or now-last>=days*86400: rows.append((last,int(s),r.get("name","Участник")))
                rows.sort(key=lambda x:x[0])
                title="МОЛЧУНЫ" if t_lower.startswith("список молчунов") else "НЕАКТИВНЫЕ"
                lines=[]
                for _,s,n in rows[:30]: lines.append(f"• {get_user_mention(user_id=s,first_name=n)}")
            return finish_command(m,"inactive_lists",bot.send_message(cid,f"📋 <b>{title}</b>\nПериод: {days} дн.\n\n"+("\n".join(lines) if lines else "Никого не найдено."),parse_mode="HTML"),ttl=60)

        if t_lower == "мой актив":
            row=db_get("chat_activity",{}).get(str(cid),{}).get(str(uid),{})
            last=float(row.get("last_seen",0) or 0); first=float(row.get("first_seen",last) or last)
            return finish_command(m,"my_active",bot.send_message(cid,f"📈 <b>МОЙ АКТИВ</b>\n\nСообщений: <b>{int(row.get('msgs',0) or 0)}</b>\nПервое появление: <b>{datetime.fromtimestamp(first,KYIV_TZ).strftime('%d.%m.%Y %H:%M') if first else '—'}</b>\nПоследняя активность: <b>{datetime.fromtimestamp(last,KYIV_TZ).strftime('%d.%m.%Y %H:%M') if last else '—'}</b>",parse_mode="HTML"),ttl=30)

        # --- IRIS: расширенные алиасы статистики ---
        if re.fullmatch(r"(?:топ|стата)(?:\s+\d+)?(?:\s+(?:сутки|день|неделя|месяц|вся|всё))?", t_lower):
            parts_stat=t_lower.split()
            limit=10
            period_word="вся"
            for part in parts_stat[1:]:
                if part.isdigit(): limit=max(1,min(100,int(part)))
                elif part in ("сутки","день"): period_word="сутки"
                elif part=="неделя": period_word="неделя"
                elif part=="месяц": period_word="месяц"
                elif part in ("вся","всё"): period_word="вся"
            days={"сутки":1,"день":1,"неделя":7,"месяц":30,"вся":36500}.get(period_word,36500)
            since=time.time()-days*86400 if days < 36500 else 0
            activity=db_get("chat_activity",{}).get(str(cid),{})
            rows=[]
            for suid,row in activity.items():
                count=0
                daily=row.get("daily",{}) or {}
                for k,v in daily.items():
                    try:
                        ts=datetime.strptime(k,"%Y-%m-%d").replace(tzinfo=KYIV_TZ).timestamp()
                        if ts >= since: count += int(v or 0)
                    except Exception: pass
                if count: rows.append((count,int(suid),row.get("name","Участник")))
            rows.sort(key=lambda x:(x[0],x[1]), reverse=True)
            lines=[f"{i}. {get_user_mention(user_id=u,first_name=n)} — <b>{c}</b>" for i,(c,u,n) in enumerate(rows[:limit],1)]
            return finish_command(m,"top_period",bot.send_message(cid,f"📊 <b>ТОП {limit} · {period_word.upper()}</b>\n\n"+("\n".join(lines) if lines else "Нет данных за период."),parse_mode="HTML"),ttl=60)

        if t_lower in ("кто граждане","кто гражданин","все граждане"):
            profiles=db_get("chat_profiles",{}).get(str(cid),{})
            activity=db_get("chat_activity",{}).get(str(cid),{})
            rows=[]
            for suid,prof in profiles.items():
                if isinstance(prof,dict) and prof.get("citizen"):
                    rows.append((int(suid),activity.get(str(suid),{}).get("name","Участник")))
            lines=[f"• {get_user_mention(user_id=u,first_name=n)}" for u,n in rows[:100]]
            return finish_command(m,"citizens",bot.send_message(cid,"🏡 <b>ГРАЖДАНЕ ЧАТА</b>\n\n"+("\n".join(lines) if lines else "Граждан пока нет."),parse_mode="HTML"),ttl=60)

        if t_lower == "+гражданство":
            profiles=db_get("chat_profiles",{}); chat=profiles.setdefault(str(cid),{}); prof=chat.setdefault(str(uid),{})
            prof["citizen"]=True; prof["citizen_date"]=time.time(); db_set("chat_profiles",profiles)
            return finish_command(m,"citizenship_on",bot.send_message(cid,"🏡 Гражданство чата установлено."),ttl=15)

        # --- IRIS: статистика и онлайн ---
        if t_lower in ("статистика", "моя статистика", "моя стат", "стата"):
            flush_stats(uid)
            users = db_get("users_data", {})
            u = users.get(str(uid), {})
            activity = db_get("chat_activity", {}).get(str(cid), {}).get(str(uid), {})
            msgs = int(u.get("msgs", 0) or 0)
            first_seen = activity.get("first_seen", u.get("first_seen", time.time()))
            last_seen = activity.get("last_seen", u.get("last_seen", time.time()))
            days = max(0, int((time.time() - first_seen) / 86400))
            last = datetime.fromtimestamp(last_seen, KYIV_TZ).strftime("%d.%m.%Y %H:%M")
            txt = (
                "📊 <b>СТАТИСТИКА ЛИЗЫ</b>\n\n"
                f"👤 {get_user_mention(m.from_user)}\n"
                f"💬 Сообщений: <b>{msgs}</b>\n"
                f"⭐ XP: <b>{int(u.get('xp', 0) or 0)}</b>\n"
                f"📅 В чате: <b>{days} дн.</b>\n"
                f"🕒 Последняя активность: <b>{last}</b>\n\n"
                "<i>Статистика ведётся отдельно для каждого чата.</i>"
            )
            return finish_command(m, "stats", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

        if t_lower in ("топ", "топ чата", "топ сообщений", "статистика чата"):
            activity = db_get("chat_activity", {}).get(str(cid), {})
            rows = []
            for suid, data in activity.items():
                try: count = int(data.get("msgs", 0) or 0)
                except Exception: count = 0
                if count:
                    rows.append((count, int(suid), data.get("name", "Участник")))
            rows.sort(reverse=True)
            lines = []
            for i, (count, suid, name) in enumerate(rows[:10], 1):
                icon = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else "▫️"
                lines.append(f"{icon} <b>{i}.</b> {get_user_mention(user_id=suid, first_name=name)} — <b>{count}</b>")
            txt = "📊 <b>ТОП УЧАСТНИКОВ ЧАТА</b>\n\n" + ("\n".join(lines) if lines else "Пока статистики недостаточно.")
            return finish_command(m, "chat_top", bot.send_message(cid, txt, parse_mode="HTML"), ttl=90)

        if t_lower in ("онлайн", "кто онлайн", "мой онлайн"):
            activity = db_get("chat_activity", {}).get(str(cid), {})
            now = time.time()
            if t_lower == "мой онлайн":
                last = activity.get(str(uid), {}).get("last_seen", 0)
                state = "🟢 недавно активен" if last and now-last <= 15*60 else "⚪️ давно не активен"
                try:
                    member = bot.get_chat_member(cid, uid)
                    if getattr(member, "status", "") == "creator" or getattr(member, "status", "") == "administrator":
                        state = "🟢 в чате"
                except Exception:
                    pass
                txt = f"<b>МОЙ ОНЛАЙН</b>\n\n{get_user_mention(m.from_user)} — {state}"
                return finish_command(m, "online", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)
            candidates=[]
            for suid,data in activity.items():
                last=float(data.get("last_seen",0) or 0)
                if last and now-last <= 30*60:
                    candidates.append((last,int(suid),data.get("name","Участник")))
            candidates.sort(reverse=True)
            lines=[]
            for _,u,n in candidates[:30]:
                status_label="🟢 недавно активен"
                try:
                    member=bot.get_chat_member(cid,u)
                    st=getattr(member,"status","")
                    if st=="online": status_label="🟢 онлайн"
                    elif st=="recently": status_label="🟢 недавно был в сети"
                    elif st in ("creator","administrator","member"): status_label="🟢 активен"
                    elif st in ("left","kicked"): continue
                except Exception:
                    pass
                lines.append(f"• {get_user_mention(user_id=u, first_name=n)} — {status_label}")
            txt = "🟢 <b>АКТИВНЫЕ УЧАСТНИКИ</b>\n\n" + ("\n".join(lines) if lines else "Нет данных об активности за последние 30 минут.")
            return finish_command(m, "online", bot.send_message(cid, txt, parse_mode="HTML"), ttl=60)

        # --- IRIS: история приглашений ---
        if t_lower in ("кто меня добавил", "кто добавил меня") or t_lower.startswith("кто добавил "):
            target_uid, target_name, _ = extract_target_and_args(m)
            if t_lower in ("кто меня добавил", "кто добавил меня"):
                target_uid = uid
            if not target_uid:
                return finish_command(m, "inviter_err", bot.send_message(cid, "⚠️ Укажи пользователя через @username, ID или reply."), ttl=10)
            rec = db_get("chat_invites", {}).get(str(cid), {}).get(str(target_uid))
            if not rec:
                return finish_command(m, "inviter_none", bot.send_message(cid, "ℹ️ Лиза не видит, кто добавил этого пользователя. Telegram не передал данные о пригласившем."), ttl=20)
            inviter_id = int(rec.get("inviter_id", 0) or 0)
            inviter_name = rec.get("inviter_name") or "Пользователь"
            when = datetime.fromtimestamp(float(rec.get("date", 0) or 0), KYIV_TZ).strftime("%d.%m.%Y %H:%M")
            return finish_command(m, "inviter", bot.send_message(cid, f"👤 {get_user_mention(user_id=target_uid, first_name=target_name)} добавил(а): {get_user_mention(user_id=inviter_id, first_name=inviter_name)}\n🕒 {when}", parse_mode="HTML"), ttl=30)

        # --- IRIS: базовые идентификаторы и навигация ---
        if t_lower in ("смс ид", "!смс ид", ".смс ид", "/смс ид"):
            if not m.reply_to_message:
                return finish_command(m, "sms_id_err", bot.send_message(cid, "⚠️ Используй команду ответом на нужное сообщение."), ttl=10)
            mid = m.reply_to_message.message_id
            return finish_command(m, "sms_id", bot.send_message(cid, f"🆔 ID сообщения: <code>{mid}</code>", parse_mode="HTML"), ttl=20)

        if t_lower.startswith("перейти к смс "):
            try: mid = int(t_lower.split()[-1])
            except Exception: return finish_command(m, "goto_sms_err", bot.send_message(cid, "⚠️ Укажи числовой ID сообщения."), ttl=10)
            try:
                link = f"https://t.me/c/{str(cid).replace('-100','')}/{mid}" if str(cid).startswith("-100") else None
                if link:
                    return finish_command(m, "goto_sms", bot.send_message(cid, f"🔗 <a href=\"{link}\">Перейти к сообщению {mid}</a>", parse_mode="HTML"), ttl=30)
            except Exception: pass
            return finish_command(m, "goto_sms_err", bot.send_message(cid, "⚠️ Не удалось сформировать ссылку для этого чата."), ttl=10)

        if t_lower in ("чат ид", "!чат ид", ".чат ид", "/чат ид"):
            return finish_command(m, "chat_id", bot.send_message(cid, f"🆔 ID чата: <code>{cid}</code>", parse_mode="HTML"), ttl=20)

        if t_lower in ("код чата", "код беседы"):
            # Локальный код безопасно привязан к Telegram ID и подходит для будущей сетки.
            code = str(abs(cid))
            return finish_command(m, "chat_code", bot.send_message(cid, f"🔑 Код чата: <code>{code}</code>", parse_mode="HTML"), ttl=30)

        if t_lower in ("кто я", "профиль", "анкета", "моя анкета"):
            finish_command(m, "profile")
            return handle_profile_request(m, uid, m.from_user)

        if t_lower.startswith("кто ты ") and not m.reply_to_message:
            target_uid, _, _ = extract_target_and_args(m, parts)
            if target_uid:
                finish_command(m, "who_are_you")
                return handle_profile_request(m, target_uid)

        # Небольшие поля анкеты Iris-style. Хранятся отдельно для каждого чата.
        if t_lower.startswith("+ник ") or t_lower == "ник удалить" or t_lower == "-ник":
            chat_users = db_get("chat_profiles", {})
            profile = chat_users.setdefault(str(cid), {}).setdefault(str(uid), {})
            if t_lower in ("ник удалить", "-ник"):
                profile.pop("nickname", None)
                db_set("chat_profiles", chat_users)
                return finish_command(m, "nick_del", bot.send_message(cid, "✅ Ник удалён."), ttl=10)
            value = t.split(maxsplit=1)[1].strip()[:30]
            profile["nickname"] = value
            db_set("chat_profiles", chat_users)
            return finish_command(m, "nick_set", bot.send_message(cid, f"✅ Твой ник: <b>{html.escape(value)}</b>", parse_mode="HTML"), ttl=15)

        if t_lower in ("ник", "+ник"):
            prof = db_get("chat_profiles", {}).get(str(cid), {}).get(str(uid), {})
            return finish_command(m, "nick_show", bot.send_message(cid, f"🏷 Твой ник: <b>{html.escape(prof.get('nickname','не установлен'))}</b>", parse_mode="HTML"), ttl=20)

        if t_lower.startswith("+звание ") or t_lower == "звание удалить" or t_lower == "-звание":
            chat_users = db_get("chat_profiles", {})
            profile = chat_users.setdefault(str(cid), {}).setdefault(str(uid), {})
            if t_lower in ("звание удалить", "-звание"):
                profile.pop("title", None)
                db_set("chat_profiles", chat_users)
                return finish_command(m, "title_del", bot.send_message(cid, "✅ Звание удалено."), ttl=10)
            value = t.split(maxsplit=1)[1].strip()[:30]
            profile["title"] = value
            db_set("chat_profiles", chat_users)
            return finish_command(m, "title_set", bot.send_message(cid, f"🎖 Твоё звание: <b>{html.escape(value)}</b>", parse_mode="HTML"), ttl=15)

        if t_lower in ("звание", "+звание"):
            prof = db_get("chat_profiles", {}).get(str(cid), {}).get(str(uid), {})
            return finish_command(m, "title_show", bot.send_message(cid, f"🎖 Твоё звание: <b>{html.escape(prof.get('title','не установлено'))}</b>", parse_mode="HTML"), ttl=20)

        if t_lower.startswith("+девиз ") or t_lower == "-девиз" or t_lower == "девиз":
            chat_users = db_get("chat_profiles", {})
            profile = chat_users.setdefault(str(cid), {}).setdefault(str(uid), {})
            if t_lower == "-девиз":
                profile.pop("motto", None); db_set("chat_profiles", chat_users)
                return finish_command(m, "motto_del", bot.send_message(cid, "✅ Девиз удалён."), ttl=10)
            if t_lower == "девиз":
                return finish_command(m, "motto_show", bot.send_message(cid, f"💬 Девиз: <i>{html.escape(profile.get('motto','не установлен'))}</i>", parse_mode="HTML"), ttl=20)
            value=t.split(maxsplit=1)[1].strip()[:100]
            profile["motto"]=value; db_set("chat_profiles", chat_users)
            return finish_command(m, "motto_set", bot.send_message(cid, "✅ Девиз сохранён."), ttl=10)

        # --- IRIS: СЕТКА ЧАТОВ ---
        # Сетка хранится как общий объект с владельцем и списком chat_id.
        # Команды намеренно требуют создателя текущего чата, чтобы нельзя было
        # самовольно присоединить чужой чат к чужой сетке.
        if t_lower in ("чаты", "сетка чаты"):
            grids = db_get("chat_grids", {})
            my_grids = [g for g in grids.values() if cid in g.get("chats", [])]
            if not my_grids:
                return finish_command(m, "grid_none", bot.send_message(cid, "🌐 Этот чат пока не состоит ни в одной сетке."), ttl=20)
            g = my_grids[0]
            lines = []
            for gcid in g.get("chats", []):
                try:
                    ch = bot.get_chat(gcid)
                    title = ch.title or str(gcid)
                    desc = ch.description or "Описание не задано"
                except Exception:
                    title, desc = str(gcid), "Нет доступа к информации"
                lines.append(f"• <b>{html.escape(title)}</b>\n  <i>{html.escape(desc[:120])}</i>")
            txt = "🌐 <b>СЕТКА ЧАТОВ</b>\n\n" + "\n".join(lines)
            return finish_command(m, "grid_chats", bot.send_message(cid, txt, parse_mode="HTML"), ttl=90)

        if t_lower.startswith("+сетка") or t_lower.startswith("-сетка"):
            if get_admin_rank(cid, uid) < 5:
                return reply_no_rights(m)
            grids = db_get("chat_grids", {})
            # +Сетка создаёт сетку из текущего чата; +Сетка <код/ID> добавляет чат.
            if t_lower.startswith("+сетка"):
                arg = t.strip()[6:].strip()
                existing = None
                for gid, g in grids.items():
                    if cid in g.get("chats", []):
                        existing = (gid, g); break
                if existing:
                    gid, g = existing
                else:
                    gid = str(random.randint(10000000, 99999999))
                    while gid in grids: gid = str(random.randint(10000000, 99999999))
                    g = {"owner": uid, "chats": [cid], "global_mods": {}, "global_admins": {}, "created": time.time()}
                    grids[gid] = g
                if arg:
                    try:
                        target_cid = int(arg)
                    except Exception:
                        target_cid = None
                    if target_cid and target_cid not in g["chats"]:
                        # Добавлять можно только чат, где этот бот уже установлен.
                        try:
                            me = bot.get_chat_member(target_cid, BOT_ID)
                            if me.status not in ("administrator", "creator"):
                                return finish_command(m, "grid_err", bot.send_message(cid, "⚠️ В указанном чате Лиза не является администратором."), ttl=15)
                            g["chats"].append(target_cid)
                        except Exception:
                            return finish_command(m, "grid_err", bot.send_message(cid, "⚠️ Не удалось проверить указанный чат."), ttl=15)
                db_set("chat_grids", grids)
                return finish_command(m, "grid_ok", bot.send_message(cid, f"🌐 <b>Сетка сохранена.</b>\nКод: <code>{gid}</code>\nЧатов: <b>{len(g['chats'])}</b>", parse_mode="HTML"), ttl=30)
            else:
                my = next(((gid,g) for gid,g in grids.items() if cid in g.get("chats", []) and g.get("owner") == uid), None)
                if not my:
                    return reply_no_rights(m)
                gid,g=my
                arg=t.strip()[6:].strip()
                try: target=int(arg)
                except Exception: target=None
                if target and target in g.get("chats",[]) and target != cid:
                    g["chats"].remove(target)
                elif target is None:
                    if len(g.get("chats",[])) > 1:
                        g["chats"].remove(cid)
                    else:
                        grids.pop(gid,None)
                db_set("chat_grids", grids)
                return finish_command(m, "grid_off", bot.send_message(cid, "🌐 Чат удалён из сетки."), ttl=15)

        # Диагностика и список чатов сетки.
        if t_lower in ("сетка", "сетка статус", "сетка чаты", "сетка инфо"):
            grids = db_get("chat_grids", {}) or {}
            found = next(((gid, g) for gid, g in grids.items() if cid in g.get("chats", [])), None)
            if not found:
                return finish_command(m, "grid_none", bot.send_message(cid, "🌐 Этот чат пока не подключён к сетке."), ttl=15)
            gid, g = found
            if t_lower == "сетка чаты":
                lines = []
                for gcid in g.get("chats", []):
                    try:
                        chat = bot.get_chat(gcid)
                        name = chat.title or str(gcid)
                    except Exception:
                        name = str(gcid)
                    lines.append(f"• <b>{html.escape(name)}</b> <code>{gcid}</code>")
                text = "🌐 <b>ЧАТЫ СЕТКИ</b>\n\n" + "\n".join(lines)
            else:
                text = (f"🌐 <b>СЕТКА</b>\n\nКод: <code>{html.escape(str(gid))}</code>\n"
                        f"Чатов: <b>{len(g.get('chats', []))}</b>\n"
                        f"Глобальных модераторов: <b>{len(g.get('global_mods', {}))}</b>\n"
                        f"Глобальных администраторов: <b>{len(g.get('global_admins', {}))}</b>\n"
                        f"Глобальных банов: <b>{len(g.get('global_bans', {}))}</b>")
            return finish_command(m, "grid_info", bot.send_message(cid, text, parse_mode="HTML"), ttl=60)

        # Глобальные модераторы/администраторы сетки.
        if t_lower in ("сетка модеры", "сетка модераторы"):
            grids=db_get("chat_grids",{}); g=next((g for g in grids.values() if cid in g.get("chats",[])),None)
            if not g: return finish_command(m,"grid_none",bot.send_message(cid,"🌐 Чат не состоит в сетке."),ttl=15)
            rows=[]
            for suid,rank in g.get("global_mods",{}).items(): rows.append(f"• {get_user_mention(user_id=int(suid), first_name=db_get('users_data',{}).get(str(suid),{}).get('name','Участник'))} — ранг {rank}")
            for suid in g.get("global_admins",{}): rows.append(f"• {get_user_mention(user_id=int(suid), first_name=db_get('users_data',{}).get(str(suid),{}).get('name','Участник'))} — гладмин")
            return finish_command(m,"grid_mods",bot.send_message(cid,"🌐 <b>ГЛОБАЛЬНАЯ МОДЕРАЦИЯ</b>\n\n"+("\n".join(rows) if rows else "Пока никого нет."),parse_mode="HTML"),ttl=60)

        if t_lower.startswith("+глмодер") or t_lower.startswith("-глмодер") or t_lower.startswith("+гладмин") or t_lower.startswith("-гладмин"):
            grids=db_get("chat_grids",{}); found=next(((gid,g) for gid,g in grids.items() if cid in g.get("chats",[])),None)
            if not found: return finish_command(m,"grid_none",bot.send_message(cid,"🌐 Сначала создайте или подключите чат к сетке."),ttl=15)
            gid,g=found
            is_owner=g.get("owner")==uid
            is_gladmin=str(uid) in g.get("global_admins",{})
            if not (is_owner or is_gladmin): return reply_no_rights(m)
            target_uid,target_name,_=extract_target_and_args(m,parts)
            if not target_uid: return finish_command(m,"grid_target",bot.send_message(cid,"⚠️ <b>Не указан пользователь</b>\nОтветь на его сообщение, укажи @username или ID."),ttl=10)
            add=t_lower.startswith("+")
            if "гладмин" in t_lower:
                if not is_owner: return reply_no_rights(m)
                if add: g.setdefault("global_admins",{})[str(target_uid)]=time.time()
                else: g.setdefault("global_admins",{}).pop(str(target_uid),None)
                msg="назначен гладмином" if add else "снят с глобальных администраторов"
            else:
                if add:
                    rank=1
                    if len(parts)>1 and parts[1].isdigit(): rank=max(1,min(5,int(parts[1])))
                    g.setdefault("global_mods",{})[str(target_uid)]=rank; msg=f"назначен глобальным модератором {rank} ранга"
                else:
                    g.setdefault("global_mods",{}).pop(str(target_uid),None); msg="снят с глобальной модерации"
            db_set("chat_grids",grids)
            return finish_command(m,"grid_role",bot.send_message(cid,f"🌐 {get_user_mention(user_id=target_uid,first_name=target_name)} {msg}.",parse_mode="HTML"),ttl=20)

        # Глобальный бан/разбан/кик в сетке.
        if t_lower.startswith("глобан") or t_lower.startswith("глоразбан") or t_lower.startswith("сетка баны") or t_lower.startswith("сетка кик"):
            grids=db_get("chat_grids",{}); found=next(((gid,g) for gid,g in grids.items() if cid in g.get("chats",[])),None)
            if not found: return finish_command(m,"grid_none",bot.send_message(cid,"🌐 Чат не состоит в сетке."),ttl=15)
            gid,g=found
            if g.get("owner")!=uid and str(uid) not in g.get("global_admins",{}): return reply_no_rights(m)
            if t_lower=="сетка баны":
                bans=g.get("global_bans",{})
                rows=[f"• {get_user_mention(user_id=int(s), first_name=db_get('users_data',{}).get(s,{}).get('name','Участник'))}" for s in bans]
                return finish_command(m,"grid_bans",bot.send_message(cid,"🌐 <b>ГЛОБАЛЬНЫЕ БАНЫ</b>\n\n"+("\n".join(rows) if rows else "Список пуст."),parse_mode="HTML"),ttl=60)
            target_uid,target_name,_=extract_target_and_args(m,parts)
            if not target_uid: return finish_command(m,"grid_target",bot.send_message(cid,"⚠️ Укажи пользователя."),ttl=10)
            if t_lower.startswith("глобан"):
                g.setdefault("global_bans",{})[str(target_uid)]={"name":target_name,"date":time.time()}
                action="заблокирован во всей сетке"
            elif t_lower.startswith("глоразбан"):
                g.setdefault("global_bans",{}).pop(str(target_uid),None); action="разблокирован в сетке"
            else:
                action="исключён из всех чатов сетки"
            if t_lower.startswith("глобан") or t_lower.startswith("глоразбан") or t_lower.startswith("сетка кик"):
                for gcid in g.get("chats",[]):
                    try:
                        if t_lower.startswith("глобан"): bot.ban_chat_member(gcid,target_uid)
                        elif t_lower.startswith("глоразбан"): bot.unban_chat_member(gcid,target_uid,only_if_banned=True)
                        else:
                            bot.ban_chat_member(gcid,target_uid); bot.unban_chat_member(gcid,target_uid,only_if_banned=True)
                    except Exception: pass
            db_set("chat_grids",grids)
            return finish_command(m,"grid_action",bot.send_message(cid,f"🌐 {get_user_mention(user_id=target_uid,first_name=target_name)} {action}.",parse_mode="HTML"),ttl=30)

        # --- IRIS: РАСШИРЕННАЯ АНКЕТА ---
        if t_lower in ("моя анкета", "анкета", "кто я", "профиль") or t_lower.startswith("анкета ") or t_lower.startswith("профиль "):
            target_uid,target_name,_=extract_target_and_args(m,parts)
            target_uid=target_uid or uid
            users=db_get("users_data",{}); u=users.get(str(target_uid),{})
            profiles=db_get("user_profiles",{}); prof=profiles.get(str(target_uid),{})
            visible=prof.get("visible",True)
            if target_uid != uid and not visible:
                return finish_command(m,"profile_private",bot.send_message(cid,"🔒 Пользователь скрыл свою анкету."),ttl=15)
            name=prof.get("nickname") or u.get("name") or target_name or "Участник"
            lines=[f"👤 <b>{html.escape(name)}</b>"]
            if prof.get("description"): lines.append(f"📝 {html.escape(prof['description'])}")
            if prof.get("title"): lines.append(f"🎖 {html.escape(prof['title'])}")
            if prof.get("motto"): lines.append(f"💬 {html.escape(prof['motto'])}")
            if prof.get("gender"): lines.append(f"⚧ Пол: {html.escape(prof['gender'])}")
            if prof.get("city"): lines.append(f"📍 Город: {html.escape(prof['city'])}")
            if prof.get("birthday"): lines.append(f"🎂 Дата рождения: {html.escape(prof['birthday'])}")
            if prof.get("citizen"): lines.append("🏡 Гражданин этого чата")
            lines.append(f"⭐ XP: <b>{int(u.get('xp',0) or 0)}</b> • сообщений: <b>{int(u.get('msgs',0) or 0)}</b>")
            return finish_command(m,"profile_full",bot.send_message(cid,"\n".join(lines),parse_mode="HTML"),ttl=60)

        # visibility and profile attributes
        if t_lower in ("-гражданство", "гражданство удалить", "снять гражданство"):
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            p.pop("citizen", None); db_set("user_profiles", profiles)
            return finish_command(m, "citizenship_del", bot.send_message(cid, "✅ Статус гражданина удалён."), ttl=10)
        if t_lower in ("гражданство", "мой гражданство", "моё гражданство"):
            prof=db_get("user_profiles",{}).get(str(uid),{})
            status="гражданин этого чата" if prof.get("citizen") else "статус не установлен"
            return finish_command(m, "citizenship_show", bot.send_message(cid, f"🏡 <b>Гражданство</b>\n\nСтатус: {status}." , parse_mode="HTML"), ttl=15)

        if t_lower in ("+анкета","-анкета"):
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            p["visible"]=t_lower=="+анкета"; db_set("user_profiles",profiles)
            return finish_command(m,"profile_visibility",bot.send_message(cid,"👤 Анкета теперь "+("видна другим пользователям." if p["visible"] else "скрыта от других пользователей.")),ttl=15)
        if t_lower.startswith("мой пол ") or t_lower=="-мой пол":
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            if t_lower=="-мой пол": p.pop("gender",None); msg="Пол удалён."
            else:
                val=parts[-1].lower();
                if val not in ("м","ж","др"): return finish_command(m,"profile_err",bot.send_message(cid,"⚠️ Укажи: м, ж или др."),ttl=10)
                p["gender"]={"м":"мужской","ж":"женский","др":"другое"}[val]; msg="Пол сохранён."
            db_set("user_profiles",profiles); return finish_command(m,"profile_gender",bot.send_message(cid,"✅ "+msg),ttl=10)
        if t_lower.startswith("!мой город ") or t_lower.startswith("мой город ") or t_lower=="-мой город":
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            if t_lower=="-мой город": p.pop("city",None); msg="Город удалён."
            else: p["city"]=t.split(None,2)[2][:80]; msg="Город сохранён."
            db_set("user_profiles",profiles); return finish_command(m,"profile_city",bot.send_message(cid,"✅ "+msg),ttl=10)
        if t_lower.startswith("мой др ") or t_lower=="-мой др":
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            if t_lower=="-мой др": p.pop("birthday",None); msg="Дата рождения удалена."
            else:
                value=parts[2] if len(parts)>2 else ""
                if not re.match(r'^\d{2}\.\d{2}\.\d{2,4}$',value): return finish_command(m,"profile_err",bot.send_message(cid,"⚠️ Формат: Мой др ДД.ММ.ГГГГ"),ttl=10)
                p["birthday"]=value; msg="Дата рождения сохранена."
            db_set("user_profiles",profiles); return finish_command(m,"profile_bday",bot.send_message(cid,"✅ "+msg),ttl=10)
        if t_lower.startswith("о себе") or t_lower.startswith("описание ") or t_lower=="-о себе":
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{})
            if t_lower=="-о себе": p.pop("description",None); msg="Описание удалено."
            elif t_lower.startswith("о себе"):
                value=t.split("\n",1)[1].strip() if "\n" in t else t[len("о себе"):].strip()
                if not value: return finish_command(m,"profile_err",bot.send_message(cid,"⚠️ Напиши описание после команды, лучше с новой строки."),ttl=10)
                p["description"]=value[:3800]; msg="Описание сохранено."
            else:
                target_uid,target_name,_=extract_target_and_args(m,parts); value=t.split("\n",1)[1].strip() if "\n" in t else ""
                if target_uid and value and get_admin_rank(cid,uid)>=5:
                    tp=profiles.setdefault(str(target_uid),{}); tp["description"]=value[:3800]; msg="Описание пользователя изменено."
                else: return finish_command(m,"profile_err",bot.send_message(cid,"⚠️ Для своей анкеты используй «О себе» и текст ниже команды."),ttl=10)
            db_set("user_profiles",profiles); return finish_command(m,"profile_desc",bot.send_message(cid,"✅ "+msg),ttl=10)
        if t_lower.startswith("+гражданство"):
            profiles=db_get("user_profiles",{}); p=profiles.setdefault(str(uid),{}); p["citizen"]=True; db_set("user_profiles",profiles)
            return finish_command(m,"citizen",bot.send_message(cid,"🏡 Ты стал гражданином этого чата."),ttl=10)
        if t_lower in ("все граждане","кто гражданин","кто граждане"):
            profiles=db_get("user_profiles",{}); rows=[]
            for suid,p in profiles.items():
                if p.get("citizen"): rows.append(f"• {get_user_mention(user_id=int(suid),first_name=db_get('users_data',{}).get(suid,{}).get('name','Участник'))}")
            return finish_command(m,"citizens",bot.send_message(cid,"🏡 <b>ГРАЖДАНЕ ЧАТА</b>\n\n"+("\n".join(rows) if rows else "Пока никто не отметил гражданство."),parse_mode="HTML"),ttl=60)
        if t_lower in ("!ид","ид","!id","id") or t_lower.startswith("!ид "):
            target_uid,target_name,_=extract_target_and_args(m,parts)
            target_uid=target_uid or uid
            return finish_command(m,"user_id",bot.send_message(cid,f"🆔 ID пользователя: <code>{target_uid}</code>",parse_mode="HTML"),ttl=20)
        if t_lower.startswith("!рег ") or t_lower.startswith("рег ") or t_lower=="регистрация":
            target_uid,target_name,_=extract_target_and_args(m,parts); target_uid=target_uid or uid
            u=db_get("users_data",{}).get(str(target_uid),{}); ts=u.get("first_seen")
            txt="ℹ️ Пользователь ещё не зарегистрирован в статистике." if not ts else f"📅 Впервые замечен: <b>{datetime.fromtimestamp(ts,KYIV_TZ).strftime('%d.%m.%Y %H:%M')}</b>"
            return finish_command(m,"registration",bot.send_message(cid,txt,parse_mode="HTML"),ttl=30)

        # --- ОБЩИЕ КОМАНДЫ ---
        if t_lower == "мой профиль":
            finish_command(m, "my_profile")
            return handle_profile_request(m, uid, m.from_user)
            
        if t_lower.startswith("кто ты"):
            if m.reply_to_message:
                finish_command(m, "who_are_you")
                return handle_profile_request(m, m.reply_to_message.from_user.id, m.reply_to_message.from_user)
            target_uid, _, _ = extract_target_and_args(m, parts)
            if target_uid:
                finish_command(m, "who_are_you")
                return handle_profile_request(m, target_uid)

        record_xp_and_stats(m)

        with state_lock: is_safe_active = cid in active_safes
        if is_safe_active and re.fullmatch(r'\d{3}', t):
            with state_lock:
                if cid in active_safes and t == active_safes[cid]["code"]:
                    del active_safes[cid]
                    ldrs = db_get("safe_leaders", {})
                    ldrs.setdefault(str(uid), {"name": m.from_user.first_name, "wins": 0})["wins"] += 1
                    db_set("safe_leaders", ldrs)
                    msg = bot.send_message(cid, f"🎉 СЕЙФ ВЗЛОМАН\nМастер {get_user_mention(m.from_user)} подобрал код: {t}", parse_mode='HTML')
                    auto_del(msg, 180)
            return

        is_words_active = False

        if m.chat.type in ['group', 'supergroup'] and not is_words_active and not is_safe_active and not t.startswith('/'):
            executor.submit(maybe_react_randomly, m)

        if m.chat.type in ['group', 'supergroup'] and get_admin_rank(cid, uid) < 5:
            if m.content_type in ('sticker', 'animation'): run_triggers(m,'стикеры')
            if m.text:
                letters=[c for c in m.text if c.isalpha()]
                if len(letters)>=int(get_v(cid,'caps_min_length',5)) and sum(1 for c in letters if c.isupper())/max(1,len(letters))*100>=float(get_v(cid,'caps_percent',80)): run_triggers(m,'капс')
                if re.search(r'(https?://|t\.me/|telegram\.me/|www\.)',m.text,re.I): run_triggers(m,'ссылки')
            has_bad_word = any(bad_word in t_lower for bad_word in MUTES)
            if has_bad_word: run_triggers(m,'маты')
            if has_bad_word:
                try: bot.delete_message(cid, m.message_id)
                except: pass
                issue_warn(cid, m.chat.title, uid, fname, BOT_ID, "Автомодератор", "Автомодерация: нецензурная лексика", None)
                return

            # Не отправляем каждое сообщение в LLM: это было главным источником
            # очереди и задержек. Сначала дешёвая эвристика, затем AI только при подозрении.
            threat_text = (m.text or "").lower()
            threat_gate = any(x in threat_text for x in (
                "докс", "доксинг", "сват", "сватинг", "адрес", "слив",
                "найду тебя", "найду где живешь", "найду где живёшь",
                "убью", "приеду к тебе", "взломаю", "разнесу", "застрелю"
            ))
            if threat_gate:
                def threat_check():
                    try:
                        if is_threat(m.text):
                            until = int(time.time()) + 300
                            bot.restrict_chat_member(cid, uid, until_date=until, permissions=ChatPermissions(can_send_messages=False))
                            mention = get_user_mention(m.from_user)
                            bosses_ping = ""
                            alert_msg = f"{bosses_ping} 🛡 Обнаружена угроза от {mention}. Пользователь автоматически заглушен на 5 минут для проверки."
                            bot.send_message(cid, alert_msg, parse_mode='HTML')
                    except Exception as e: logging.error(f"[THREAT ACTION] {e}", exc_info=True)
                ai_executor.submit(threat_check)

        direct = m.chat.type == 'private' or (m.reply_to_message and m.reply_to_message.from_user.id == BOT_ID) or "лиза" in t_lower or f"@{BOT_USER}" in t_lower
        conflict_hit = any(w in t_lower for w in CONFL)

        if direct or conflict_hit:
            
            if not get_v(cid, "intervene", True) or random.randint(1, 100) > get_v(cid, "freq", 40): return
            prompt = f"В чате ссора: {m.reply_to_message.text} -> {m.text}. Резюме:" if (conflict_hit and m.reply_to_message) else m.text
            def ai_task():
                try:
                    bot.send_chat_action(cid, 'typing')
                    ans = call_ai([{"role": "system", "content": SYS_PROMPT}, {"role": "user", "content": prompt}])
                    if m.chat.type == 'private': bot.send_message(cid, ans, parse_mode='HTML')
                    else: bot.send_message(cid, f"{get_user_mention(m.from_user)}, {ans}", parse_mode='HTML')
                except Exception as e:
                    logging.error(f"[AI TASK] {e}", exc_info=True)
            ai_executor.submit(ai_task)

        elif (m.chat.type in ['group', 'supergroup'] and not is_words_active and not is_safe_active
              and not t.startswith('/') and get_v(cid, "butt_in", False)
              and random.randint(1, 100) <= get_v(cid, "butt_in_chance", 15)):
            def butt_in_task():
                try:
                    bot.send_chat_action(cid, 'typing')
                    prompt = (
                        f"В чате кто-то написал сообщение: '{m.text}'. Тебя никто не звал и не упоминал. "
                        "Просто сама реши встрять в разговор одной короткой живой репликой по теме сообщения, "
                        "как будто случайно зацепилась взглядом за эту фразу в общем чате:"
                    )
                    ans = call_ai([{"role": "system", "content": SYS_PROMPT}, {"role": "user", "content": prompt}])
                    bot.send_message(cid, f"{get_user_mention(m.from_user)}, {ans}", parse_mode='HTML')
                except Exception as e:
                    logging.error(f"[BUTT IN TASK] {e}", exc_info=True)
            ai_executor.submit(butt_in_task)
    except Exception as e:
        logging.error(f"[TEXT HANDLER] {e}", exc_info=True)


# ============================================================
# Telegram integration: реальные права/заявки/проверка участников
# ============================================================

def _admin_security_command(m, t_lower, t):
    """Расширенные локальные инструменты безопасности и диагностики."""
    cid, uid = m.chat.id, m.from_user.id
    if m.chat.type not in ('group', 'supergroup'):
        return None

    # Локальная база антиспама.
    if t_lower in ('антиспам', 'антиспам список', 'ирис антиспам'):
        if get_admin_rank(cid, uid) < 3:
            return reply_no_rights(m)
        ids = [str(x) for x in (get_v(cid, 'antispam_ids', []) or [])]
        enabled = bool(get_v(cid, 'iris_antispam', True))
        lines = [f"🛡 <b>АНТИСПАМ</b>", f"Система: <b>{'включена' if enabled else 'выключена'}</b>", f"В базе: <b>{len(ids)}</b>"]
        if ids:
            names = db_get('users_data', {}) or {}
            for x in ids[:50]:
                row = names.get(x, {}) or {}
                lines.append(f"• <code>{x}</code> — {html.escape(str(row.get('name') or 'пользователь'))}")
            if len(ids) > 50:
                lines.append(f"… ещё {len(ids)-50}")
        else:
            lines.append('Список пуст.')
        return finish_command(m, 'antispam_list', bot.send_message(cid, '\n'.join(lines), parse_mode='HTML'), ttl=60)

    if t_lower.startswith(('+антиспам ', '-антиспам ')):
        if get_admin_rank(cid, uid) < 5:
            return reply_no_rights(m)
        parts = t.split()
        target = parts[1] if len(parts) > 1 else ''
        target_uid = None
        if m.reply_to_message and len(parts) == 1:
            target_uid = m.reply_to_message.from_user.id
        elif target.lstrip('-').isdigit():
            target_uid = int(target)
        elif target.startswith('@'):
            try:
                member = bot.get_chat_member(cid, target[1:])
                target_uid = member.user.id
            except Exception:
                target_uid = None
        if not target_uid:
            return finish_command(m, 'antispam_target', bot.send_message(cid, '⚠️ Укажи ID/@username или используй ответ на сообщение.', parse_mode='HTML'), ttl=15)
        ids = [str(x) for x in (get_v(cid, 'antispam_ids', []) or [])]
        add = t_lower.startswith('+')
        if add and str(target_uid) not in ids:
            ids.append(str(target_uid))
        if not add:
            ids = [x for x in ids if x != str(target_uid)]
        set_chat_setting(cid, 'antispam_ids', ids)
        action = 'добавлен в' if add else 'удалён из'
        return finish_command(m, 'antispam_edit', bot.send_message(cid, f"🛡 Пользователь <code>{target_uid}</code> {action} локальной базы антиспама."), ttl=15)

    # Реальная диагностика прав указанного пользователя в Telegram.
    if t_lower.startswith(('тг права ', 'тг права @', 'тг разрешения ')):
        if get_admin_rank(cid, uid) < 1:
            return reply_no_rights(m)
        parts = t.split()
        target_uid, target_name, _ = extract_target_and_args(m, parts)
        if not target_uid:
            return finish_command(m, 'tg_user_perms_err', bot.send_message(cid, '⚠️ <b>Не указан пользователь</b>\nОтветь на его сообщение, укажи @username или ID.'), ttl=10)
        try:
            member = bot.get_chat_member(cid, int(target_uid))
            labels = (
                ('can_manage_chat', 'управление чатом'),
                ('can_delete_messages', 'удаление сообщений'),
                ('can_restrict_members', 'блокировка участников'),
                ('can_pin_messages', 'закрепление'),
                ('can_invite_users', 'приглашения'),
                ('can_manage_topics', 'темы'),
                ('can_promote_members', 'назначение админов'),
                ('can_change_info', 'изменение информации'),
                ('can_post_messages', 'публикация сообщений'),
                ('can_edit_messages', 'редактирование сообщений'),
            )
            lines = [f"👤 <b>TELEGRAM-ПРАВА</b>\n{get_user_mention(user_id=int(target_uid), first_name=target_name or getattr(member.user, 'first_name', 'Пользователь'))}", f"Статус: <b>{html.escape(str(member.status))}</b>"]
            for attr, label in labels:
                value = getattr(member, attr, None)
                if value is not None:
                    lines.append(f"{'✅' if value else '❌'} {label}")
            return finish_command(m, 'tg_user_perms', bot.send_message(cid, '\n'.join(lines), parse_mode='HTML'), ttl=45)
        except Exception as e:
            logging.warning(f'[TG USER PERMS] {e}')
            return finish_command(m, 'tg_user_perms_err', bot.send_message(cid, '⚠️ Не удалось получить права пользователя.'), ttl=15)

    # Диагностика безопасности Лизы: полезно после установки/изменения прав.
    if t_lower in ('проверка безопасности', 'проверка лизы', 'статус безопасности'):
        if get_admin_rank(cid, uid) < 1:
            return reply_no_rights(m)
        try:
            me = bot.get_chat_member(cid, BOT_ID)
            required = {
                'Удаление': getattr(me, 'can_delete_messages', False),
                'Блокировка': getattr(me, 'can_restrict_members', False),
                'Приглашения': getattr(me, 'can_invite_users', False),
                'Темы': getattr(me, 'can_manage_topics', False),
                'Назначение админов': getattr(me, 'can_promote_members', False),
                'Закрепление': getattr(me, 'can_pin_messages', False),
            }
            lines = [f"🔐 <b>ПРОВЕРКА БЕЗОПАСНОСТИ ЛИЗЫ</b>", f"Статус: <b>{html.escape(str(me.status))}</b>"]
            for label, value in required.items():
                lines.append(f"{'✅' if value else '⚠️'} {label}")
            if me.status not in ('administrator', 'creator'):
                lines.append('\n⚠️ Для модерации Лизе нужны права администратора.')
            return finish_command(m, 'security_check', bot.send_message(cid, '\n'.join(lines), parse_mode='HTML'), ttl=60)
        except Exception:
            return finish_command(m, 'security_check_err', bot.send_message(cid, '⚠️ Telegram не вернул текущие права Лизы.'), ttl=15)

    return None

def _telegram_admin_command(m, t_lower, t):
    cid, uid = m.chat.id, m.from_user.id
    if m.chat.type not in ('group','supergroup'):
        return None
    if not (t_lower.startswith(('тг админ', '+тг админ', '-тг админ', 'тг права', 'тг разрешения чата',
                                'проверить в чате', '+автозаявки', '-автозаявки'))):
        return None

    if t_lower in ('тг права', 'тг разрешения чата'):
        try:
            me = bot.get_chat_member(cid, BOT_ID)
            p = getattr(me, 'can_manage_chat', None)
            lines = [f"🤖 <b>Права Лизы</b>", f"Статус: <b>{html.escape(str(me.status))}</b>"]
            if me.status in ('administrator','creator'):
                for attr, label in (
                    ('can_delete_messages','Удаление сообщений'), ('can_restrict_members','Блокировка участников'),
                    ('can_pin_messages','Закрепление'), ('can_invite_users','Приглашение пользователей'),
                    ('can_manage_chat','Управление чатом'), ('can_promote_members','Назначение админов')):
                    val = getattr(me, attr, None)
                    if val is not None: lines.append(f"{'✅' if val else '❌'} {label}")
            else:
                lines.append('❌ Лиза не является администратором.')
            return finish_command(m,'tg_perms',bot.send_message(cid,'\n'.join(lines),parse_mode='HTML'),ttl=30)
        except Exception as e:
            logging.error(f'[TG PERMS] {e}', exc_info=True)
            return finish_command(m,'tg_perms_err',bot.send_message(cid,'⚠️ Не удалось получить права Лизы.'),ttl=15)

    if t_lower.startswith('проверить в чате'):
        parts=t.split(); target_uid,target_name,_=extract_target_and_args(m,parts)
        if not target_uid:
            return finish_command(m,'check_chat_err',bot.send_message(cid,'⚠️ Используй <code>Проверить в чате @user</code> или ответом на сообщение.',parse_mode='HTML'),ttl=10)
        try:
            member=bot.get_chat_member(cid,target_uid)
            status_map={'creator':'создатель','administrator':'администратор','member':'участник','restricted':'ограничен','left':'вышел','kicked':'заблокирован'}
            status=status_map.get(member.status,member.status)
            extra=[]
            if member.status=='administrator':
                for attr,label in (('can_delete_messages','удаление'),('can_restrict_members','ограничение'),('can_pin_messages','закрепление'),('can_invite_users','приглашения'),('can_promote_members','админы')):
                    if getattr(member,attr,None): extra.append(label)
            text=f"🔎 {get_user_mention(user_id=target_uid,first_name=target_name)}\nСтатус: <b>{status}</b>"
            if extra: text += '\nПрава: ' + ', '.join(extra)
            return finish_command(m,'check_chat',bot.send_message(cid,text,parse_mode='HTML'),ttl=30)
        except Exception:
            return finish_command(m,'check_chat_err',bot.send_message(cid,'⚠️ Пользователь не найден в этом чате.'),ttl=15)

    if t_lower.startswith(('+автозаявки','-автозаявки')):
        if not _cleanup_dk_allowed(cid,uid,'настройки'): return reply_no_rights(m)
        enabled=t_lower.startswith('+')
        set_chat_setting(cid,'auto_join_requests',enabled)
        return finish_command(m,'auto_requests',bot.send_message(cid, f"📥 Автозаявки {'включены' if enabled else 'выключены'}.",parse_mode='HTML'),ttl=15)

    if t_lower.startswith(('+тг админ','тг админ','-тг админ')):
        if not _cleanup_dk_allowed(cid,uid,'настройки'): return reply_no_rights(m)
        if not has_permission(cid,uid,'can_promote'): return reply_no_rights(m)
        parts=t.split(); target_uid,target_name,_=extract_target_and_args(m,parts)
        if not target_uid:
            return finish_command(m,'tg_admin_err',bot.send_message(cid,'⚠️ Укажи пользователя через @username, ID или reply.',parse_mode='HTML'),ttl=10)
        if target_uid == BOT_ID:
            return finish_command(m,'tg_admin_err',bot.send_message(cid,'⚠️ Права самой Лизы меняются только через Telegram.'),ttl=10)
        promote=not t_lower.startswith('-')
        try:
            if promote:
                bot.promote_chat_member(cid,target_uid,can_manage_chat=False,can_delete_messages=True,can_restrict_members=True,can_invite_users=True,can_pin_messages=True,can_manage_topics=True)
                msg=f"👮 {get_user_mention(user_id=target_uid,first_name=target_name)} назначен администратором Telegram."
            else:
                bot.promote_chat_member(cid,target_uid,can_manage_chat=False,can_delete_messages=False,can_restrict_members=False,can_invite_users=False,can_pin_messages=False,can_manage_topics=False)
                msg=f"🧹 Права администратора Telegram у {get_user_mention(user_id=target_uid,first_name=target_name)} сняты."
            return finish_command(m,'tg_admin',bot.send_message(cid,msg,parse_mode='HTML'),ttl=20)
        except Exception as e:
            logging.error(f'[TG ADMIN] {e}', exc_info=True)
            return finish_command(m,'tg_admin_err',bot.send_message(cid,'⚠️ Telegram не разрешил изменить права. Нужны соответствующие права Лизы.'),ttl=20)
    return None


def handle_chat_join_request(r):
    try:
        cid = r.chat.id
        if not get_v(cid,'auto_join_requests',False): return
        # Автопринятие только заявок, пришедших в чат, где настройка включена.
        bot.approve_chat_join_request(cid, r.from_user.id)
        logging.info(f'[JOIN REQUEST] approved user={r.from_user.id} chat={cid}')
    except Exception as e:
        logging.error(f'[JOIN REQUEST] {e}', exc_info=True)


# Регистрация основных обработчиков.
bot.register_message_handler(handle_system_messages, content_types=["new_chat_members", "left_chat_member"])
try:
    bot.register_chat_join_request_handler(handle_chat_join_request)
except AttributeError:
    logging.warning("[JOIN REQUEST] Handler registration unavailable")
bot.register_message_handler(on_photo, content_types=["photo"])
bot.register_message_handler(cmd_start, commands=["start"])
bot.register_message_handler(cmd_settings, commands=["settings"])
bot.register_message_handler(text_handler, content_types=["text"], func=lambda m: not _is_explicit_command(m))

def cb_handler(c):
    try:
        d=c.data or ""; cid=c.message.chat.id if c.message else c.from_user.id; uid=c.from_user.id
        if d=="noop": return bot.answer_callback_query(c.id)
        if d=="what_can_i_do":
            txt=("<b>ЛИЗА</b>\n\n🛡 Модерация и защита чата\n⚙️ Настройки, правила и приветствие\n📊 Статистика и активность\n👤 Профили участников\n💰 Базовая экономика")
            return bot.edit_message_text(txt,cid,c.message.message_id,parse_mode="HTML",reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад",callback_data="back_to_start")))
        if d=="back_to_start":
            kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Что умеет Лиза?",callback_data="what_can_i_do"))
            if get_admin_rank(cid,uid)>=5: kb.add(types.InlineKeyboardButton("⚙️ Настройки",callback_data="open_main_settings"))
            return bot.edit_message_text("👋 <b>Лиза готова работать.</b>\n\nДобавь её в группу и выдай права администратора.",cid,c.message.message_id,parse_mode="HTML",reply_markup=kb)
        if d=="liza_setup_help":
            return bot.edit_message_text("⚙️ <b>Быстрый старт</b>\n\n1. Добавь Лизу в группу.\n2. Выдай права администратора.\n3. Настрой правила, приветствие и модерацию.\n4. Проверь <code>Помощь</code>.",cid,c.message.message_id,parse_mode="HTML",reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("« Назад",callback_data="back_to_start")))
        if d=="open_main_settings":
            if get_admin_rank(cid,uid)<5: return bot.answer_callback_query(c.id,"⛔ Только создатель чата.",show_alert=True)
            return bot.edit_message_text("⚙️ <b>Настройки Лизы</b>\n\nОсновные параметры можно менять командами <code>Настройки чата</code> и <code>Права рангов</code>.",cid,c.message.message_id,parse_mode="HTML")
        if d.startswith("m:"):
            if get_admin_rank(cid,uid)<5: return bot.answer_callback_query(c.id,"⛔ Только создатель чата.",show_alert=True)
            key=d[2:]
            if key in ("toggle_intervene","toggle_sys","toggle_reactions","toggle_butt_in"):
                setting={"toggle_intervene":"intervene","toggle_sys":"del_sys","toggle_reactions":"random_reactions","toggle_butt_in":"butt_in"}[key]
                set_chat_setting(cid,setting,not bool(get_v(cid,setting,False)))
                bot.answer_callback_query(c.id,"✅ Сохранено")
                return bot.edit_message_reply_markup(cid,c.message.message_id,reply_markup=main_kb(cid,False))
            if key in ("freq","anger","butt_in_chance"):
                return bot.answer_callback_query(c.id,"Изменяй параметр командой в чате.",show_alert=True)
        return bot.answer_callback_query(c.id)
    except Exception as e:
        logging.error(f"[CALLBACK] {e}",exc_info=True)
        try: bot.answer_callback_query(c.id,"⚠️ Не удалось выполнить действие.",show_alert=True)
        except: pass

bot.register_callback_query_handler(cb_handler, func=lambda c: True)
