# -*- coding: utf-8 -*-
"""Лиза — production entry point."""
import time, logging, os
from runtime import bot
import handlers, settings
from moderation import start_moderation_scheduler
from reliability import safe_loop, mark_ok, mark_error, stop, stopped
from db_schema import ensure_schema
from lifecycle import install
from miniapp import start_miniapp_server
from goals import due, complete
from minigames import ensure_schema as ensure_minigame_schema

def _goal_tick():
    # Goals are intentionally passive unless a future notification channel is configured.
    return None

if __name__ == "__main__":
    ensure_schema(); ensure_minigame_schema(); start_miniapp_server(); settings.start_scheduler(); start_moderation_scheduler(); install(bot)
    try: bot.remove_webhook()
    except Exception: logging.exception("[REMOVE WEBHOOK]")
    logging.info("Лиза запущена и готова к работе!")
    while not stopped():
        try:
            bot.infinity_polling(timeout=20,long_polling_timeout=10,skip_pending=True)
            mark_ok()
        except KeyboardInterrupt: break
        except Exception as e:
            mark_error(e); logging.error("Сбой связи: %s",e,exc_info=True); time.sleep(5)
    stop()
