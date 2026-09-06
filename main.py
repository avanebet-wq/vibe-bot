# -*- coding: utf-8 -*-
"""VIBE Telegram bot entry point."""
import threading

from runtime import bot, logging, time

import core
import general
import ai
import ui
import triggers
import cleanup
import handlers

if __name__ == "__main__":
    threading.Thread(target=cleanup.cleanup_worker, daemon=True).start()

    try:
        bot.remove_webhook()
    except Exception as e:
        logging.error(f"[REMOVE WEBHOOK] {e}")

    logging.info("Бот запущен и готов к работе!")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"Сбой связи: {e}", exc_info=True)
            time.sleep(5)
