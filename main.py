# -*- coding: utf-8 -*-
"""Лиза — точка входа."""
import time
import logging

from runtime import bot

import handlers  # регистрирует обработчики

if __name__ == "__main__":
    try:
        bot.remove_webhook()
    except Exception as e:
        logging.error(f"[REMOVE WEBHOOK] {e}")

    logging.info("Лиза запущена и готова к работе!")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.error(f"Сбой связи: {e}", exc_info=True)
            time.sleep(5)
