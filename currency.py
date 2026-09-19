# -*- coding: utf-8 -*-
"""Внутриигровая валюта «Лизки».

Отдельно от опыта (XP): опыт даёт только ранг профиля, Лизки — деньги
фермы (сбор урожая + прокачка территории/грядок).
"""
import html
import logging

from database import conn, db_lock
from runtime import bot

LOG = logging.getLogger("currency")

CURRENCY_NAME = "Луны"
CURRENCY_ICON = "🌙"


def ensure_schema():
    with db_lock:
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS user_balance (
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    balance BIGINT NOT NULL DEFAULT 0,
                    PRIMARY KEY(chat_id, user_id)
                )"""
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def get_balance(chat_id, user_id):
    with db_lock:
        try:
            row = conn.execute(
                "SELECT balance FROM user_balance WHERE chat_id=? AND user_id=?",
                (str(chat_id), str(user_id)),
            ).fetchone()
        except Exception:
            conn.rollback()
            raise
    return int(row[0]) if row else 0


def add_balance(chat_id, user_id, amount):
    """Начисляет (amount>0) или списывает (amount<0) Лизки. Возвращает итоговый баланс."""
    amount = int(amount)
    with db_lock:
        try:
            if amount:
                conn.execute(
                    "INSERT INTO user_balance(chat_id,user_id,balance) VALUES(?,?,?) "
                    "ON CONFLICT(chat_id,user_id) DO UPDATE SET balance=user_balance.balance+excluded.balance",
                    (str(chat_id), str(user_id), amount),
                )
            row = conn.execute(
                "SELECT balance FROM user_balance WHERE chat_id=? AND user_id=?",
                (str(chat_id), str(user_id)),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return int(row[0]) if row else 0


def spend_balance(chat_id, user_id, amount):
    """Списывает Лизки, если хватает средств. True при успехе, иначе баланс не трогается."""
    amount = int(amount)
    if amount <= 0:
        return True
    with db_lock:
        try:
            row = conn.execute(
                "SELECT balance FROM user_balance WHERE chat_id=? AND user_id=?",
                (str(chat_id), str(user_id)),
            ).fetchone()
            current = int(row[0]) if row else 0
            if current < amount:
                conn.rollback()
                return False
            conn.execute(
                "UPDATE user_balance SET balance = balance - ? WHERE chat_id=? AND user_id=?",
                (amount, str(chat_id), str(user_id)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return True


def fmt(amount):
    return f"{CURRENCY_ICON} {int(amount)} {CURRENCY_NAME}"


def cmd_balance(message, args=""):
    chat_id = message.chat.id
    uid = message.from_user.id
    try:
        bal = get_balance(chat_id, uid)
    except Exception:
        LOG.exception("cmd_balance failed")
        bot.reply_to(message, "⚠️ Не удалось получить баланс, попробуй ещё раз.")
        return
    bot.reply_to(message, f"{CURRENCY_ICON} Лун: <b>{bal}</b>", parse_mode="HTML")

# created 2026-09-20
