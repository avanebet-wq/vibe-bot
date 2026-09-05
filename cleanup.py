# -*- coding: utf-8 -*-
"""VIBE Bot — cleanup module."""
from runtime import *
from core import *

def cleanup_worker():
    while True:
        try:
            time.sleep(5)
            now = time.time()
            with state_lock:
                remaining = []
                for item in messages_to_delete:
                    if now >= item["time"]:
                        try: bot.delete_message(item["cid"], item["mid"])
                        except Exception as e:
                            if "message to delete not found" not in str(e): logging.error(f"[CLEANUP DEL] {e}")
                    else: remaining.append(item)
                messages_to_delete[:] = remaining
        except Exception as e: logging.error(f"[CLEANUP WORKER] {e}", exc_info=True)

def _parse_period_words(text):
    text = text.lower().strip()
    m = re.search(r'(\d+)\s*(минут[аы]?|мин|м|час(?:а|ов)?|ч|дн(?:я|ей)?|д|недел(?:я|и|ь)|нед|месяц(?:а|ев)?|мес)', text)
    if not m: return None
    n = int(m.group(1)); u = m.group(2)
    if u.startswith(('мес',)): return n * 30 * 86400
    if u.startswith(('нед',)): return n * 7 * 86400
    if u.startswith(('д',)): return n * 86400
    if u.startswith(('час','ч')): return n * 3600
    return n * 60

def _cleanup_messages(cid, start_mid, count):
    count = max(1, min(int(count), 100))
    deleted = 0
    # Telegram does not expose message history to bots. We therefore use the
    # message IDs observed by this bot and walk backwards from the anchor.
    for mid in range(max(1, start_mid - count), start_mid + 1):
        try:
            bot.delete_message(cid, mid)
            deleted += 1
        except Exception:
            pass
    return deleted

def _set_chat_rules(cid, text):
    data = db_get("chat_settings", {})
    chat = data.setdefault(str(cid), {})
    chat["rules"] = text.strip()
    db_set("chat_settings", data)

def _get_chat_setting(cid, key, default=""):
    return db_get("chat_settings", {}).get(str(cid), {}).get(key, default)

def _render_template(text, user):
    if not text: return ""
    name = html.escape((user.first_name or "участник") + ((" " + user.last_name) if user.last_name else ""))
    return text.replace("{имя}", name)

def _save_welcome(cid, text):
    data = db_get("chat_settings", {})
    data.setdefault(str(cid), {})["welcome"] = text.strip()
    db_set("chat_settings", data)

def _record_join(cid, user):
    if not user or user.is_bot: return
    data = db_get("chat_activity", {})
    chat = data.setdefault(str(cid), {})
    row = chat.setdefault(str(user.id), {})
    row.setdefault("first_seen", time.time())
    row["last_seen"] = time.time()
    row.setdefault("msgs", 0)
    row["name"] = user.first_name or ""
    row["uname"] = user.username or ""
    db_set("chat_activity", data)

def _cleanup_dk_allowed(cid, uid, code):
    return command_allowed_by_dk(cid, uid, code)

def _tracked_users(cid):
    return db_get("chat_activity", {}).get(str(cid), {})

def _is_protected_member(cid, uid):
    try:
        cm = bot.get_chat_member(cid, uid)
        return cm.status in ("creator", "administrator")
    except Exception:
        return False

def _kick_user(cid, uid, ban=False):
    if _is_protected_member(cid, uid):
        return False
    try:
        if ban:
            bot.ban_chat_member(cid, uid)
        else:
            bot.ban_chat_member(cid, uid)
            bot.unban_chat_member(cid, uid, only_if_banned=True)
        return True
    except Exception as e:
        logging.warning(f"[CLEAN KICK] {cid}/{uid}: {e}")
        return False

def _cleanup_candidates(cid, mode, value=None):
    now=time.time(); rows=[]
    for uid,row in _tracked_users(cid).items():
        try: uid=int(uid)
        except: continue
        if _is_protected_member(cid, uid): continue
        first=row.get("first_seen", now); last=row.get("last_seen", first); msgs=int(row.get("msgs",0) or 0)
        if mode=="inactive" and value and now-last >= value: rows.append((uid,row,now-last))
        elif mode=="active" and value and now-last <= value: rows.append((uid,row,now-last))
        elif mode=="new" and value and now-first <= value: rows.append((uid,row,now-first))
        elif mode=="silent" and value and now-first >= value and msgs==0: rows.append((uid,row,now-first))
        elif mode=="sms" and value and now-last <= value[1] and msgs < value[0]: rows.append((uid,row,msgs))
    return rows

def _format_cleanup_users(rows, limit=30):
    out=[]
    for uid,row,_ in rows[:limit]:
        name=html.escape(row.get("name") or row.get("uname") or str(uid))
        out.append(f"• <a href=\"tg://user?id={uid}\">{name}</a>")
    return "\n".join(out) if out else "— никого не найдено —"

def _handle_advanced_cleanup(m):
    t=(m.text or "").strip(); low=t.lower(); cid,uid=m.chat.id,m.from_user.id
    if not any(low.startswith(x) for x in ("кик неактив","кик актив","кик новичков","кик удалённых","кик удаленных","кто удалён","кто удален","кик собак","кик молчунов","кик по смс","кик по сообщениям","неактив","молчуны","по смс")):
        return False
    parts=low.split()
    if low.startswith("кто удал"):
        if not _cleanup_dk_allowed(cid,uid,"кик удалённых"): return reply_no_rights(m) or True
        found=[]
        for sid,row in _tracked_users(cid).items():
            try:
                member=bot.get_chat_member(cid,int(sid))
                if member.status=="kicked" and row.get("name"):
                    found.append((int(sid),row,0))
            except: pass
        return finish_command(m,"who_deleted",bot.send_message(cid,"👻 <b>VIBE — УДАЛЁННЫЕ АККАУНТЫ</b>\n\n"+_format_cleanup_users(found),parse_mode="HTML"),ttl=60) or True
    if low.startswith("неактив") or low.startswith("молчуны") or low.startswith("по смс"):
        mode="inactive" if low.startswith("неактив") else ("silent" if low.startswith("молчуны") else "sms")
        if not _cleanup_dk_allowed(cid,uid,"кик неактив" if mode=="inactive" else ("кик молчунов" if mode=="silent" else "кик по смс")): return reply_no_rights(m) or True
        rows=_cleanup_candidates(cid,mode,(7*86400 if mode=="inactive" else 30*86400 if mode=="silent" else (10,7*86400)))
        return finish_command(m,"clean_list",bot.send_message(cid,"🧹 <b>VIBE-СПИСОК</b>\n\n"+_format_cleanup_users(rows),parse_mode="HTML"),ttl=60) or True
    if low.startswith("кик "):
        if low.startswith("кик неактив"):
            code="кик неактив"; mode="inactive"
        elif low.startswith("кик актив"):
            code="кик актив"; mode="active"
        elif low.startswith("кик новичков"):
            code="кик новичков"; mode="new"
        elif low.startswith(("кик удалённых","кик удаленных","кик собак")):
            code="кик удалённых"; mode="deleted"
        elif low.startswith("кик молчунов"):
            code="кик молчунов"; mode="silent"
        elif low.startswith(("кик по смс","кик по сообщениям")):
            code="кик по смс"; mode="sms"
        else: return False
        if not _cleanup_dk_allowed(cid,uid,code): return reply_no_rights(m) or True
        if mode=="deleted":
            rows=[]
            for sid,row in _tracked_users(cid).items():
                try:
                    if bot.get_chat_member(cid,int(sid)).status=="kicked": rows.append((int(sid),row,0))
                except: pass
        else:
            nums=re.findall(r"\d+",low)
            n=int(nums[0]) if nums else None
            if mode in ("inactive","new","silent"):
                period=_parse_period_words(low) or (7*86400 if mode=="inactive" else 86400)
                if n and not _parse_period_words(low):
                    rows=_cleanup_candidates(cid,mode,None)[:n]
                else: rows=_cleanup_candidates(cid,mode,period)
            elif mode=="active":
                period=_parse_period_words(low) or 600
                rows=_cleanup_candidates(cid,mode,period)
            else:
                count=n or 1; period=_parse_period_words(low) or 7*86400
                rows=_cleanup_candidates(cid,mode,(count,period))
        if not rows:
            return finish_command(m,"clean_none",bot.send_message(cid,"🧹 <b>VIBE-ЧИСТКА</b>\nПодходящих участников не найдено."),ttl=15) or True
        # For numeric inactive mode, choose the least active first. For active/new, process all matching rows.
        if mode=="inactive": rows=sorted(rows,key=lambda x:x[2],reverse=True)
        if mode=="active": rows=sorted(rows,key=lambda x:x[2])
        limit=50 if mode in ("inactive","active","new","silent","sms") else len(rows)
        kicked=0
        for row in rows[:limit]:
            if _kick_user(cid,row[0]): kicked+=1
        return finish_command(m,"clean_kick",bot.send_message(cid,f"🧹 <b>VIBE-ЧИСТКА</b>\n👢 Исключено участников: <b>{kicked}</b>.\n📋 Найдено: <b>{len(rows)}</b>.",parse_mode="HTML"),ttl=20) or True
    return False
