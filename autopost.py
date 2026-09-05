# -*- coding: utf-8 -*-
"""VIBE Bot — autopost module."""
from runtime import *
from ui import *

def send_specific_post(chat_id, post):
    try:
        mk = build_post_user_kb(post)
        txt = post.get("text", "")
        if post.get("photo"): msg = bot.send_photo(chat_id, post["photo"], caption=txt, reply_markup=mk, parse_mode='HTML')
        else: msg = bot.send_message(chat_id, txt, reply_markup=mk, parse_mode='HTML')
        old_msg_id = None
        with state_lock:
            if post.get("auto_delete_prev") and post.get("last_msg_id"): old_msg_id = post["last_msg_id"]
            post["last_msg_id"] = msg.message_id
        if old_msg_id:
            try: bot.delete_message(chat_id, old_msg_id)
            except Exception as e:
                if "message to delete not found" not in str(e): logging.error(f"[AUTOPOST DEL PREV] {e}")
        if post.get("pin_after_send"):
            prev_pinned = post.get("last_pinned_msg_id")
            try: bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
            except Exception as e:
                logging.error(f"[AUTOPOST PIN] {e}")
            else:
                with state_lock: post["last_pinned_msg_id"] = msg.message_id
                if prev_pinned and prev_pinned != msg.message_id:
                    try: bot.unpin_chat_message(chat_id, prev_pinned)
                    except Exception as e:
                        if "message to unpin not found" not in str(e): logging.error(f"[AUTOPOST UNPIN PREV] {e}")
    except Exception as e: logging.error(f"[AUTOPOST SEND] {e}", exc_info=True)

def autopost_worker():
    while True:
        try:
            time.sleep(15)
            now_ts = datetime.now(KYIV_TZ).timestamp()
            td_str, curr_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d"), datetime.now(KYIV_TZ).strftime("%H:%M")
            to_send = []
            with state_lock:
                data = db_get("autopost", {"posts": []})
                updated = False
                for p in data.get("posts", []):
                    if not p.get("enabled") or (p.get("start_date") and td_str < p["start_date"]): continue
                    dt = p.get("daily_time")
                    if dt:
                        if curr_str >= dt and p.get("last_sent_date") != td_str:
                            p["last_post"], p["last_sent_date"], updated = now_ts, td_str, True
                            to_send.append((p.get("chat_id"), p))
                    elif p.get("interval", 0) > 0 and now_ts - p.get("last_post", 0) >= p["interval"]:
                        p["last_post"], updated = now_ts, True
                        to_send.append((p.get("chat_id"), p))
                if updated: db_set("autopost", data)
            for chat_id, p in to_send: send_specific_post(chat_id, p)
            if to_send:
                with state_lock:
                    fresh = db_get("autopost", {"posts": []})
                    by_id = {item["id"]: item for item in fresh.get("posts", [])}
                    for _, sent_post in to_send:
                        target = by_id.get(sent_post["id"])
                        if target:
                            target["last_msg_id"] = sent_post.get("last_msg_id")
                            target["last_post"] = sent_post.get("last_post")
                            if "last_sent_date" in sent_post: target["last_sent_date"] = sent_post["last_sent_date"]
                            if "last_pinned_msg_id" in sent_post: target["last_pinned_msg_id"] = sent_post["last_pinned_msg_id"]
                    db_set("autopost", fresh)
        except Exception as e: logging.error(f"[WORKER] {e}", exc_info=True)
