# -*- coding: utf-8 -*-
"""Обработка ссылок TikTok: сверхбыстрая загрузка видео и слайдшоу через API."""
import re
import logging
import requests
import threading
from telebot.types import InputMediaPhoto
from runtime import bot

LOG = logging.getLogger("tiktok")

# Регулярка для отлова любых ссылок тиктока (мобильные, десктопные, шорт-линки)
TIKTOK_RE = re.compile(r"https?://(?:www\.|vt\.|vm\.|m\.)?tiktok\.com/(?:@[\w.-]+/video/\d+|[\w.-]+)")

def handle_message(message):
    text = message.text or ""
    match = TIKTOK_RE.search(text)
    if not match:
        return False
    
    url = match.group(0)
    # Запускаем в отдельном потоке, чтобы не тормозить обработчик сообщений Лизы
    threading.Thread(target=_process_tiktok, args=(message.chat.id, message.message_id, url), daemon=True).start()
    return True

def _process_tiktok(chat_id, message_id, url):
    try:
        bot.send_chat_action(chat_id, "upload_video")
        
        # Используем TikWM API: он быстрый и отдаёт прямые ссылки без водяных знаков
        resp = requests.post("https://www.tikwm.com/api/", data={"url": url, "hd": 1}, timeout=10)
        if resp.status_code != 200:
            bot.send_message(chat_id, "⚠️ Сервис TikTok сейчас недоступен. Попробуй позже.", reply_to_message_id=message_id)
            return
            
        data = resp.json()
        if data.get("code") != 0:
            bot.send_message(chat_id, "⚠️ Не удалось скачать. Возможно, видео удалено или приватное.", reply_to_message_id=message_id)
            return

        content = data.get("data", {})
        title = content.get("title", "")[:1000]

        # 1. Если это слайдшоу (картинки)
        if "images" in content and content["images"]:
            images = content["images"]
            media_group = []
            
            # Telegram принимает максимум 10 медиа в группе
            for i, img_url in enumerate(images[:10]):
                caption = title if i == 0 else ""
                media_group.append(InputMediaPhoto(media=img_url, caption=caption))
            
            bot.send_media_group(chat_id, media_group, reply_to_message_id=message_id)
            
            # Прикрепляем оригинальную музыку отдельным файлом
            audio_url = content.get("music")
            if audio_url:
                bot.send_audio(chat_id, audio_url, reply_to_message_id=message_id)
                
        # 2. Если это обычное видео
        else:
            # Пытаемся взять HD, если нет - обычное качество без вотермарки
            video_url = content.get("hdplay") or content.get("play")
            if video_url:
                # Передаём прямую ссылку. Telegram скачает её сам, не нагружая Railway
                bot.send_video(chat_id, video_url, caption=title, reply_to_message_id=message_id)
            else:
                bot.send_message(chat_id, "⚠️ Не нашла видео по ссылке.", reply_to_message_id=message_id)

    except requests.RequestException as e:
        LOG.error("Network error fetching TikTok: %s", e)
        bot.send_message(chat_id, "⚠️ Произошла ошибка сети при скачивании.", reply_to_message_id=message_id)
    except Exception as e:
        LOG.error("Unexpected error in TikTok downloader: %s", e)
        bot.send_message(chat_id, "⚠️ Произошла ошибка при обработке ссылки.", reply_to_message_id=message_id)

# updated 2026-09-18
