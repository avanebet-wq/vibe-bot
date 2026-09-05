# -*- coding: utf-8 -*-
"""VIBE Bot — triggers module."""
from runtime import *
from core import *

def get_chat_triggers(cid):
    with state_lock: return dict(db_get("chat_triggers",{}).get(str(cid),{}))

def save_chat_triggers(cid,data):
    with state_lock:
        x=db_get("chat_triggers",{}); x[str(cid)]=data; db_set("chat_triggers",x)

def parse_duration_text(text,default_seconds=0):
    m=re.search(r"(\d+)\s*(сек|с|мин|м|час|ч|дн|день|дня|дней|нед|неделя|недель|мес|месяц|год)",text.lower())
    if not m:return default_seconds
    n=int(m.group(1));u=m.group(2)
    return n*({"сек":1,"с":1,"мин":60,"м":60,"час":3600,"ч":3600,"дн":86400,"день":86400,"дня":86400,"дней":86400,"нед":604800,"неделя":604800,"недель":604800,"мес":2592000,"месяц":2592000,"год":31536000}[u])

def _trigger_allowed(cid,uid): return get_admin_rank(cid,uid)>=get_v(cid,"trigger_dk",TRIGGER_DK_DEFAULT)

def _trigger_action(line):
    if "/" in line: command,reason=line.split("/",1);reason=reason.strip()
    else: command,reason=line,""
    p=command.strip().split();return {"command":p[0].lower(),"args":p[1:],"reason":reason} if p else None

def run_triggers(m,event):
    tr=get_chat_triggers(m.chat.id).get(event.lower())
    if not tr:return
    for a in tr.get("actions",[])[:3]:
        try:
            cmd=a.get("command");r=a.get("reason") or f"Автотриггер VIBE: {event}";args=a.get("args",[])
            if cmd in ("варн","пред","предупреждение"): issue_warn(m.chat.id,getattr(m.chat,"title",""),m.from_user.id,m.from_user.first_name,BOT_ID,"VIBE-автомод",r,None)
            elif cmd in ("мут","mute"):
                sec=max(60,parse_duration_text(" ".join(args),get_v(m.chat.id,"mute_period",604800)));bot.restrict_chat_member(m.chat.id,m.from_user.id,until_date=int(time.time())+sec,permissions=ChatPermissions(can_send_messages=False));bot.send_message(m.chat.id,f"🔇 <b>VIBE-АВТОМОД</b>\n{get_user_mention(m.from_user)} получил мут.\n📝 {html.escape(r)}",parse_mode="HTML")
            elif cmd in ("бан","чс","ban"):
                sec=parse_duration_text(" ".join(args),0);bot.ban_chat_member(m.chat.id,m.from_user.id,until_date=int(time.time())+sec if sec else 0);bot.send_message(m.chat.id,f"🚫 <b>VIBE-АВТОБАН</b>\n{get_user_mention(m.from_user)} заблокирован.\n📝 {html.escape(r)}",parse_mode="HTML")
            elif cmd in ("кик","kick"):
                bot.ban_chat_member(m.chat.id,m.from_user.id);bot.unban_chat_member(m.chat.id,m.from_user.id,only_if_banned=True);bot.send_message(m.chat.id,f"👢 <b>VIBE-АВТОКИК</b>\n{get_user_mention(m.from_user)} исключён.\n📝 {html.escape(r)}",parse_mode="HTML")
        except Exception as e: logging.error(f"[TRIGGER ACTION] {e}",exc_info=True)

def handle_trigger_command(m):
    t=(m.text or "").strip();low=t.lower();cid,uid=m.chat.id,m.from_user.id
    if low in ("-маты","+маты","-ссылки","+ссылки","-стикеры","+стикеры","-капс","+капс") or low.startswith(("-стикеры ","-капс ")):
        if not _trigger_allowed(cid,uid): return bot.reply_to(m,"⛔ Недостаточно ранга для настройки автомодерации.")
        if low in ("-маты", "+маты"):
            set_chat_setting(cid,"auto_mats",low=="-маты"); return bot.reply_to(m,"🛡 Фильтр матов: <b>включён</b>." if low=="-маты" else "🛡 Фильтр матов: <b>выключен</b>.",parse_mode="HTML")
        if low in ("-ссылки", "+ссылки"):
            set_chat_setting(cid,"auto_links",low=="-ссылки"); return bot.reply_to(m,"🔗 Запрет ссылок: <b>включён</b>." if low=="-ссылки" else "🔗 Запрет ссылок: <b>выключен</b>.",parse_mode="HTML")
        if low in ("-стикеры", "+стикеры") or low.startswith("-стикеры "):
            val=1 if low=="-стикеры" else (int(low.split()[1]) if low.split()[1].isdigit() else 1); set_chat_setting(cid,"sticker_limit",val if low.startswith("-стикеры") else 0); return bot.reply_to(m,f"🎟 Лимит стикеров подряд: <b>{val if low.startswith('-стикеры') else 'снят'}</b>.",parse_mode="HTML")
        if low=="-капс" or low.startswith("-капс "):
            q=low.split(); pct=int(q[1]) if len(q)>1 and q[1].isdigit() else 80; ln=int(q[2]) if len(q)>2 and q[2].isdigit() else 5; set_chat_setting(cid,"caps_percent",max(1,min(100,pct))); set_chat_setting(cid,"caps_min_length",max(1,ln)); return bot.reply_to(m,f"🔠 Антикапс: <b>{pct}%</b> при длине от <b>{ln}</b> символов.",parse_mode="HTML")
        if low=="+капс": set_chat_setting(cid,"caps_percent",101); return bot.reply_to(m,"🔠 Антикапс выключен.")
    if low in ("триггер помощь", "триггеры помощь", "триги помощь"):
        return bot.reply_to(m,"⚙️ <b>ЛИЗА — ТРИГГЕРЫ</b>\n\n"
            "<code>+Триггер (событие) (приоритет)</code>\n"
            "<code>Мут 10 минут / Причина</code>\n\n"
            "События: маты, ссылки, стикеры, капс, варнлимит, дуэль, кубы, русская рулетка.\n"
            "До 3 автоматических действий на событие.\n\n"
            "<code>Триггеры</code> — список\n<code>Триггер маты</code> — детали\n"
            "<code>-Триггер маты</code> — удалить.",parse_mode="HTML")
    if low in ("триггеры","триги"):
        d=get_chat_triggers(cid);return bot.reply_to(m,"🧩 <b>VIBE-ТРИГГЕРЫ</b>\n"+("Пока ничего не настроено." if not d else "\n".join(f"• <b>{html.escape(v['event'])}</b> — {len(v.get('actions',[]))} действий" for v in d.values())),parse_mode="HTML")
    if low.startswith("триггер "):
        ev=low[8:].strip();tr=get_chat_triggers(cid).get(ev)
        if not tr:return bot.reply_to(m,"🔎 Такой триггер не найден.")
        return bot.reply_to(m,f"🧩 <b>{html.escape(tr['event'])}</b>\n🎚 Приоритет: {tr.get('priority',0)}\n"+"\n".join(f"• <code>{html.escape(a['command']+' '+' '.join(a.get('args',[])))}</code> — {html.escape(a.get('reason',''))}" for a in tr.get('actions',[])),parse_mode="HTML")
    if low.startswith("-триггер "):
        if not _trigger_allowed(cid,uid):return bot.reply_to(m,"⛔ Недостаточно ранга для управления триггерами.")
        ev=low[9:].strip();d=get_chat_triggers(cid);d.pop(ev,None);save_chat_triggers(cid,d);return bot.reply_to(m,f"🗑 Триггер <b>{html.escape(ev)}</b> удалён.",parse_mode="HTML")
    if low.startswith("+триггер "):
        if not _trigger_allowed(cid,uid):return bot.reply_to(m,"⛔ Недостаточно ранга для управления триггерами.")
        lines=t.splitlines();h=lines[0].split();priority=int(h[-1]) if len(h)>2 and h[-1].isdigit() else 0;ev=" ".join(h[1:-1] if priority else h[1:]).lower()
        if ev not in TRIGGER_EVENTS:return bot.reply_to(m,"⚠️ Неизвестное событие. Используй: маты, ссылки, стикеры, капс, варнлимит, дуэль, кубы, русская рулетка.")
        acts=[x for x in (_trigger_action(x) for x in lines[1:4]) if x]
        if not acts:return bot.reply_to(m,"⚠️ Укажи автоматическое действие на следующей строке.")
        d=get_chat_triggers(cid);d[ev]={"event":TRIGGER_EVENTS[ev],"priority":priority,"actions":acts,"updated":time.time()};save_chat_triggers(cid,d);return bot.reply_to(m,f"✅ <b>VIBE-ТРИГГЕР</b> установлен\n⚡ Событие: {TRIGGER_EVENTS[ev]}\n🎚 Приоритет: {priority}\n🧩 Действий: {len(acts)}",parse_mode="HTML")
    if low.startswith("дк унб"):
        if get_admin_rank(cid,uid)<5:return bot.reply_to(m,"⛔ Только 5 ранг может менять доступ к триггерам.")
        rank=int(low.split()[-1]) if low.split()[-1].isdigit() else 4;set_chat_setting(cid,"trigger_dk",max(0,min(5,rank)));return bot.reply_to(m,f"🔐 Минимальный ранг для триггеров: <b>{rank}</b>",parse_mode="HTML")
    return None
