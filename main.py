# -*- coding: utf-8 -*-
"""VIBE Telegram bot entry point."""
from runtime import bot, logging, time

import core
import general
import ai
import games
import ui
import autopost
import triggers
import cleanup
import handlers

if __name__ == "__main__":
    logging.info("Бот запущен и готов к работе!")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"Сбой связи: {e}", exc_info=True)
            time.sleep(5)
