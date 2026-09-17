# -*- coding: utf-8 -*-
import html, time, threading
from database import conn, db_lock
from runtime import bot
from minigames import get_xp
from karma import get_karma

_PRESENCE_LOCK = threading.RLock()
_LAST_PRESENCE_WRITE = {}
_PRESENCE_INTERVAL = 60.0
_PRESENCE_CACHE_TTL = 86400.0
_LAST_PRESENCE_CLEAN = 0.0

RANKS = [
    (0, "🌱 Росток"), (100, "🍃 Листик"), (250, "🌿 Кустик"),
    (500, "🪴 Гровер"), (900, "🌳 Куст"), (1500, "💨 Дымарь"),
    (2400, "🔥 Боец Дыма"), (3600, "🌿 Знаток Шишек"),
    (5200, "👑 Ботаник"), (7500, "🔥 Шаман Дыма"), (11000, "👑 Легенда Шишек"),
]

def ensure_profile_schema():
    with db_lock:
        try:
            conn.execute('''CREATE TABLE IF NOT EXISTS chat_user_presence (
                chat_id TEXT NOT NULL, user_id TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                PRIMARY KEY(chat_id,user_id))''')
            conn.execute('''CREATE TABLE IF NOT EXISTS profile_xp (
                chat_id TEXT NOT NULL, user_id TEXT NOT NULL, xp INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(chat_id,user_id))''')
            conn.commit()
        except Exception:
            conn.rollback()
            raise

def touch_user(message):
    global _LAST_PRESENCE_CLEAN
    if not getattr(message, 'from_user', None):
        return
    now=time.time()
    key=(str(message.chat.id), str(message.from_user.id))
    if now - _LAST_PRESENCE_CLEAN > 3600:
        with _PRESENCE_LOCK:
            cutoff = now - _PRESENCE_CACHE_TTL
            for k, ts in list(_LAST_PRESENCE_WRITE.items()):
                if ts < cutoff:
                    _LAST_PRESENCE_WRITE.pop(k, None)
            _LAST_PRESENCE_CLEAN = now
    with _PRESENCE_LOCK:
        last=_LAST_PRESENCE_WRITE.get(key, 0.0)
        if last and now-last < _PRESENCE_INTERVAL:
            return
        _LAST_PRESENCE_WRITE[key]=now
    with db_lock:
        try:
            conn.execute(
                """INSERT INTO chat_user_presence(chat_id,user_id,first_seen,last_seen) VALUES(?,?,?,?)
                ON CONFLICT(chat_id,user_id) DO UPDATE SET last_seen=excluded.last_seen""",
                (key[0], key[1], now, now),
            )
            conn.commit()
        except Exception:
            with _PRESENCE_LOCK:
                _LAST_PRESENCE_WRITE.pop(key, None)
            conn.rollback()
            raise

def rank_for(xp):
    current=RANKS[0]
    for item in RANKS:
        if xp >= item[0]: current=item
        else: break
    idx=RANKS.index(current)
    if idx == len(RANKS)-1: return current, None
    return current, RANKS[idx+1][0]-xp

def fmt_duration(seconds):
    seconds=max(0,int(seconds)); d,r=divmod(seconds,86400); h,r=divmod(r,3600); m,_=divmod(r,60)
    parts=[]
    if d: parts.append(f'{d} д')
    if h: parts.append(f'{h} ч')
    if m: parts.append(f'{m} мин')
    return ' '.join(parts) if parts else 'меньше минуты'

def _count(chat_id,user_id,kind):
    with db_lock:
        try:
            return int(conn.execute('SELECT COUNT(*) FROM minigame_events WHERE chat_id=? AND user_id=? AND kind=?',(str(chat_id),str(user_id),kind)).fetchone()[0])
        except Exception:
            conn.rollback()
            raise

def _drink_stats(chat_id,user_id):
    with db_lock:
        try:
            row=conn.execute('SELECT COUNT(*), COALESCE(SUM(volume_liters),0) FROM drink_game_events WHERE chat_id=? AND user_id=?',(str(chat_id),str(user_id))).fetchone()
        except Exception:
            conn.rollback()
            raise
    return int(row[0]),float(row[1] or 0)

def cmd_profile(message):
    touch_user(message)
    u=message.from_user; cid=str(message.chat.id); uid=str(u.id)
    first=(u.first_name or '').strip(); last=(u.last_name or '').strip(); nick=' '.join(x for x in (first,last) if x) or '—'
    tag='@'+u.username if u.username else '—'
    xp=get_xp(cid,uid); rank,remaining=rank_for(xp)
    cups=_count(cid,uid,'coffee'); cigs=_count(cid,uid,'smoke'); drinks,liters=_drink_stats(cid,uid)
    with db_lock:
        try:
            row=conn.execute('SELECT first_seen FROM chat_user_presence WHERE chat_id=? AND user_id=?',(cid,uid)).fetchone()
        except Exception:
            conn.rollback()
            raise
    since=float(row[0]) if row else time.time()
    lines=[f'👤 <b>Профиль</b>', '', f'🆔 ID: <code>{html.escape(str(u.id))}</code>',f'👤 Ник: <b>{html.escape(nick)}</b>',f'🏷 Тег: <b>{html.escape(tag)}</b>',
           f'🏆 Ранг: <b>{html.escape(rank[1])}</b>',f'⭐ Опыт: <b>{xp} XP</b>']
    if remaining is None: lines.append('👑 Максимальный ранг достигнут')
    else: lines.append(f'📈 До следующего ранга: <b>{remaining} XP</b>')
    lines += ['', '🎮 <b>Статистика мини-игр</b>', f'🚬 Сиг скурено: <b>{cigs}</b>', f'☕ Чашек выпито: <b>{cups}</b>', f'🥤 Revo выпито: <b>{drinks}</b>', f'💧 Литров Revo: <b>{liters:.1f} л</b>', '', f'⏱ В чате: <b>{html.escape(fmt_duration(time.time()-since))}</b>', f'⚖️ Карма: <b>{get_karma(cid, uid):+d}</b>']
    bot.reply_to(message,'\n'.join(lines),parse_mode='HTML')

# updated 2026-09-18
