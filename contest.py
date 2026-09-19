# -*- coding: utf-8 -*-
"""Конкурс на приглашение участников: живой лидерборд в одном сообщении.

Идея: Лиза слушает служебные апдейты Telegram о добавлении/выходе участников
(new_chat_members / left_chat_member). Эти апдейты бот получает всегда, даже
если в чате включено скрытие/удаление системных уведомлений о входе-выходе —
настройка удаления лишь прячет сообщение от людей уже после того, как Лиза
успела его обработать (см. settings_core.enforce_system_message_deletion).
Поэтому именно эти апдейты и есть тот самый "лог чата", на который Лиза
ориентируется, а не текст, который видят люди.

Правила подсчёта:
  * Кто добавил пользователя (message.from_user) получает +1 балл в чате,
    где это произошло.
  * Если добавленный — бот, счёт не идёт.
  * Если добавленный уже когда-либо был замечен Лизой (chat_user_presence)
    раньше, чем за CONTEST_STALE_SECONDS до текущего момента — то есть явно
    не новый для чата человек — балл не засчитывается, приглашённый
    исключается из чата, а в чат уходит сообщение с объяснением.
    Telegram Bot API не отдаёт боту дату регистрации аккаунта, поэтому
    "был в сети давно" здесь означает "этого пользователя Лиза уже видела
    в этом чате задолго до конкурса", а не буквальный возраст аккаунта.
  * Если приглашённый участник позже сам вышел (left_chat_member) —
    приглашавшему засчитывается -1 балл, в чат уходит напоминание, что
    участники должны оставаться в группе, а не выходить.
  * Лидерборд живёт в одном сообщении, которое Лиза редактирует на месте
    при каждом изменении счёта — новое сообщение не создаётся.
"""
from __future__ import annotations

import html
import logging
import time

from telebot.apihelper import ApiTelegramException

from runtime import bot
from database import db_update_json_scoped, db_get_scoped, conn, db_lock
from utils import get_mention, is_chat_admin

log = logging.getLogger(__name__)

# "Был в сети давно" = Лиза уже видела этого человека в чате настолько
# заранее, что для конкурса он не в счёт. Порог настраивается на будущее,
# сейчас — неделя.
CONTEST_STALE_SECONDS = 7 * 24 * 3600

_NAMESPACE = "contest"


def _empty_state():
    return {
        "active": False,
        "started_at": None,
        "started_by": None,
        "chat_title": None,
        "board_message_id": None,
        "scores": {},       # inviter_id(str) -> int
        "names": {},        # inviter_id(str) -> display name
        "invited_by": {},   # invited_user_id(str) -> inviter_id(str) (только успешные, приносящие балл)
        "log": [],          # последние события для отладки/истории (ограничено)
    }


def _get_state(chat_id):
    return db_get_scoped(_NAMESPACE, chat_id, _empty_state())


def _update_state(chat_id, mutator):
    return db_update_json_scoped(_NAMESPACE, chat_id, mutator, _empty_state())


def _display_name(user):
    if user is None:
        return "Пользователь"
    return getattr(user, "first_name", None) or getattr(user, "username", None) or str(getattr(user, "id", "?"))


def _first_seen_before(chat_id, user_id, cutoff_ts):
    """Была ли у Лизы отметка о presence этого пользователя раньше cutoff_ts —
    в этом же чате. Если данных нет, считаем пользователя новым."""
    try:
        with db_lock:
            row = conn.execute(
                "SELECT first_seen FROM chat_user_presence WHERE chat_id=? AND user_id=?",
                (str(chat_id), str(user_id)),
            ).fetchone()
    except Exception:
        log.exception("[contest] presence lookup failed")
        return False
    if not row or row[0] is None:
        return False
    return float(row[0]) < cutoff_ts


def _board_text(state, chat_title=None):
    scores = state.get("scores", {}) or {}
    names = state.get("names", {}) or {}
    title = chat_title or state.get("chat_title") or "чат"
    lines = [f"🏆 <b>Конкурс на приглашения — {html.escape(str(title))}</b>", ""]
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], names.get(kv[0], "")))
    ranked = [(uid, pts) for uid, pts in ranked if pts > 0]
    if not ranked:
        lines.append("Пока никто не пригласил ни одного засчитанного участника.")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, pts) in enumerate(ranked[:50]):
            prefix = medals[i] if i < 3 else f"{i + 1}."
            mention = get_mention(uid, names.get(uid, uid))
            word = _points_word(pts)
            lines.append(f"{prefix} {mention} — <b>{pts}</b> {word}")
    lines.append("")
    lines.append("Приглашай новых людей в чат — каждый засчитанный участник даёт +1 балл.")
    lines.append("Если приглашённый уже был в чате раньше или выйдет из чата — балл сгорает.")
    return "\n".join(lines)


def _points_word(n):
    n_abs = abs(int(n))
    if n_abs % 10 == 1 and n_abs % 100 != 11:
        return "балл"
    if 2 <= n_abs % 10 <= 4 and not 12 <= n_abs % 100 <= 14:
        return "балла"
    return "баллов"


def _render_board(chat_id, state, chat_title=None):
    """Редактирует существующее сообщение лидерборда либо создаёт новое,
    если его ещё нет. Никогда не шлёт новое сообщение поверх старого."""
    text = _board_text(state, chat_title=chat_title)
    mid = state.get("board_message_id")
    if mid:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=mid, parse_mode="HTML")
            return mid
        except ApiTelegramException as e:
            desc = str(e)
            if "message is not modified" in desc:
                return mid
            log.warning(f"[contest] edit board failed, re-sending: {e}")
        except Exception:
            log.exception("[contest] edit board failed, re-sending")
    try:
        sent = bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception:
        log.exception("[contest] failed to send board message")
        return mid

    def mutate(st):
        st["board_message_id"] = sent.message_id
        return st

    _update_state(chat_id, mutate)
    return sent.message_id


# --------------------------------------------------------------------- команды


def cmd_start_contest(message):
    """"старт конкурс" — включает конкурс и публикует лидерборд."""
    cid = message.chat.id
    if getattr(message.chat, "type", "") not in ("group", "supergroup"):
        bot.reply_to(message, "🏆 Конкурс можно запустить только в групповом чате.")
        return
    uid = message.from_user.id if message.from_user else None
    if uid is not None and not is_chat_admin(cid, uid):
        bot.reply_to(message, "⛔ Запустить конкурс может только администратор чата.")
        return

    chat_title = getattr(message.chat, "title", None) or str(cid)

    already_active = _get_state(cid).get("active")

    def mutate(st):
        st["active"] = True
        st["started_at"] = time.time()
        st["started_by"] = str(uid) if uid is not None else None
        st["chat_title"] = chat_title
        st.setdefault("scores", {})
        st.setdefault("names", {})
        st.setdefault("invited_by", {})
        st.setdefault("log", [])
        return st

    state = _update_state(cid, mutate)
    _render_board(cid, state, chat_title=chat_title)

    if already_active:
        bot.reply_to(message, "🏆 Конкурс уже был запущен — таблица лидеров выше, продолжаем считать.")
    else:
        bot.reply_to(
            message,
            "🏆 Конкурс запущен! Приглашайте новых участников в чат — таблица лидеров будет обновляться сама.",
        )


def cmd_stop_contest(message):
    """"стоп конкурс" — останавливает подсчёт (лидерборд остаётся видимым)."""
    cid = message.chat.id
    uid = message.from_user.id if message.from_user else None
    if uid is not None and not is_chat_admin(cid, uid):
        bot.reply_to(message, "⛔ Остановить конкурс может только администратор чата.")
        return

    state = _get_state(cid)
    if not state.get("active"):
        bot.reply_to(message, "🏆 Конкурс сейчас не запущен.")
        return

    def mutate(st):
        st["active"] = False
        return st

    _update_state(cid, mutate)
    bot.reply_to(message, "🏁 Конкурс остановлен. Итоговая таблица лидеров — выше.")


# --------------------------------------------------------------------- события


def handle_new_members_for_contest(message):
    """Вызывать из обработчика new_chat_members (см. settings_core.handle_new_members)."""
    cid = message.chat.id
    state = _get_state(cid)
    if not state.get("active"):
        return

    inviter = getattr(message, "from_user", None)
    if inviter is None:
        return

    from runtime import BOT_ID

    now = time.time()
    cutoff = now - CONTEST_STALE_SECONDS
    chat_title = getattr(message.chat, "title", None) or state.get("chat_title")

    for user in getattr(message, "new_chat_members", []) or []:
        if user.id == BOT_ID:
            continue
        if inviter.id == user.id:
            # Самостоятельный вход по ссылке — Лиза не знает наверняка, кто
            # пригласил, поэтому баллы не начисляем никому.
            continue

        stale = _first_seen_before(cid, user.id, cutoff)
        if stale:
            _reject_stale_member(message, inviter, user)
            continue

        inviter_id = str(inviter.id)
        invited_id = str(user.id)

        def mutate(st, inviter_id=inviter_id, invited_id=invited_id, inviter=inviter):
            st.setdefault("scores", {})
            st.setdefault("names", {})
            st.setdefault("invited_by", {})
            st["scores"][inviter_id] = int(st["scores"].get(inviter_id, 0)) + 1
            st["names"][inviter_id] = _display_name(inviter)
            st["invited_by"][invited_id] = inviter_id
            log_list = st.setdefault("log", [])
            log_list.append({"type": "join", "inviter": inviter_id, "invited": invited_id, "ts": time.time()})
            st["log"] = log_list[-200:]
            return st

        new_state = _update_state(cid, mutate)
        _render_board(cid, new_state, chat_title=chat_title)


def _reject_stale_member(message, inviter, user):
    cid = message.chat.id
    mention_invited = get_mention(user.id, _display_name(user))
    mention_inviter = get_mention(inviter.id, _display_name(inviter))
    try:
        bot.send_message(
            cid,
            f"⚠️ {mention_inviter}, балл за {mention_invited} не засчитывается — этот пользователь "
            f"уже был в чате раньше. Такие пользователи не считаются, поэтому я его исключаю.",
            parse_mode="HTML",
        )
    except Exception:
        log.exception("[contest] failed to announce stale rejection")
    try:
        bot.ban_chat_member(cid, user.id)
        bot.unban_chat_member(cid, user.id, only_if_banned=True)
    except Exception:
        log.exception("[contest] failed to remove stale member %s from %s", user.id, cid)


def handle_left_member_for_contest(message):
    """Вызывать из обработчика left_chat_member."""
    cid = message.chat.id
    state = _get_state(cid)
    if not state.get("active"):
        return

    left_user = getattr(message, "left_chat_member", None)
    if left_user is None:
        return

    invited_id = str(left_user.id)
    inviter_id = (state.get("invited_by") or {}).get(invited_id)
    if not inviter_id:
        # Этого выхода Лиза не сможет связать с конкурсом — либо человек не
        # был засчитан ранее, либо это выход самого пригласившего.
        return

    chat_title = getattr(message.chat, "title", None) or state.get("chat_title")

    def mutate(st, inviter_id=inviter_id, invited_id=invited_id):
        st.setdefault("scores", {})
        st["scores"][inviter_id] = int(st["scores"].get(inviter_id, 0)) - 1
        (st.get("invited_by") or {}).pop(invited_id, None)
        log_list = st.setdefault("log", [])
        log_list.append({"type": "leave", "inviter": inviter_id, "invited": invited_id, "ts": time.time()})
        st["log"] = log_list[-200:]
        return st

    new_state = _update_state(cid, mutate)
    inviter_name = (new_state.get("names") or {}).get(inviter_id, inviter_id)
    mention_inviter = get_mention(inviter_id, inviter_name)
    mention_left = get_mention(left_user.id, _display_name(left_user))
    try:
        bot.send_message(
            cid,
            f"➖ {mention_left} вышел(-ла) из чата, поэтому у {mention_inviter} снят 1 балл. "
            f"Приглашённые участники не должны выходить из группы — они должны оставаться в ней.",
            parse_mode="HTML",
        )
    except Exception:
        log.exception("[contest] failed to announce leave penalty")
    _render_board(cid, new_state, chat_title=chat_title)

# updated 2026-09-19
