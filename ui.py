# -*- coding: utf-8 -*-
"""Минимальный интерфейс управления Лизой."""
from runtime import *
from core import *
from general import *

def format_seconds_human(secs):
    if not secs or secs <= 0: return "выкл"
    h, rem = divmod(int(secs), 3600); m_, s_ = divmod(rem, 60)
    if h: return f"{h}ч" + (f" {m_}м" if m_ else "")
    if m_: return f"{m_}м"
    return f"{s_}с"

def main_kb(cid, is_pv=False):
    intervene=get_v(cid,"intervene",True); del_sys=get_v(cid,"del_sys",False); reactions=get_v(cid,"random_reactions",True); butt=get_v(cid,"butt_in",False)
    kb=types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"💬 Отвечать в чате: {'✅' if intervene else '❌'}",callback_data="m:toggle_intervene"))
    kb.add(types.InlineKeyboardButton(f"🔥 Случайные реакции: {'✅' if reactions else '❌'}",callback_data="m:toggle_reactions"))
    kb.add(types.InlineKeyboardButton(f"🗑 Чистить системные сообщения: {'✅' if del_sys else '❌'}",callback_data="m:toggle_sys"))
    kb.add(types.InlineKeyboardButton(f"🗣 Вмешательство в диалог: {'✅' if butt else '❌'}",callback_data="m:toggle_butt_in"))
    return kb
