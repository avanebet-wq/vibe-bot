# -*- coding: utf-8 -*-
"""Модуль мгновенного распознавания речи и видеокружков через Groq Whisper."""
import html
import logging
import threading
import requests

from runtime import bot, WAKE_RE
from config import GROQ_KEYS, GROQ_KEY

LOG = logging.getLogger("transcriber")

_keys = [k.strip() for k in (GROQ_KEYS or GROQ_KEY.split(",")) if k.strip()]
_key_idx = 0
_key_lock = threading.Lock()


def _get_key():
    with _key_lock:
        return _keys[_key_idx % len(_keys)] if _keys else None


def _switch_key():
    global _key_idx
    with _key_lock:
        _key_idx += 1


def transcribe_audio_bytes(audio_bytes: bytes, filename: str, mime_type: str) -> str | None:
    """Отправляет аудиофайл в Groq Whisper API с ротацией ключей."""
    if not _keys:
        LOG.error("[transcriber] Нет доступных ключей GROQ_API_KEY")
        return None

    models = ("whisper-large-v3-turbo", "whisper-large-v3")

    for model in models:
        for _ in range(len(_keys)):
            key = _get_key()
            if not key:
                return None
            try:
                files = {
                    "file": (filename, audio_bytes, mime_type)
                }
                data = {
                    "model": model,
                    "response_format": "json",
                    "temperature": 0.0,
                }
                resp = requests.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files=files,
                    data=data,
                    timeout=(5, 25),
                )
                if resp.status_code == 200:
                    text = (resp.json().get("text") or "").strip()
                    return text
                elif resp.status_code in (401, 429, 503):
                    LOG.warning("[transcriber] Groq status %s, переключаю ключ", resp.status_code)
                    _switch_key()
                    continue
                else:
                    LOG.error("[transcriber] Ошибка Groq %s: %s", resp.status_code, resp.text[:250])
                    break
            except Exception as exc:
                LOG.error("[transcriber] Сетевой сбой Groq: %s", exc)
                _switch_key()
    return None


def handle_transcription(message):
    """Точка входа: запускает распознавание в отдельном потоке."""
    is_voice = message.content_type == "voice"
    is_video_note = message.content_type == "video_note"
    if not (is_voice or is_video_note):
        return False

    threading.Thread(
        target=_process_audio_async,
        args=(message, is_video_note),
        daemon=True,
        name="liza-voice-transcriber",
    ).start()
    return True


def _process_audio_async(message, is_video_note: bool):
    chat_id = message.chat.id
    try:
        bot.send_chat_action(chat_id, "typing")
        media_obj = message.video_note if is_video_note else message.voice
        if not media_obj:
            return

        # Защита от слишком больших файлов (до 20 МБ)
        if getattr(media_obj, "file_size", 0) > 20 * 1024 * 1024:
            bot.reply_to(message, "⚠️ Запись слишком длинная для расшифровки.")
            return

        # Скачиваем файл напрямую в RAM без сохранения на диск
        file_info = bot.get_file(media_obj.file_id)
        audio_bytes = bot.download_file(file_info.file_path)

        filename = "circle.mp4" if is_video_note else "voice.ogg"
        mime_type = "video/mp4" if is_video_note else "audio/ogg"

        recognized_text = transcribe_audio_bytes(audio_bytes, filename, mime_type)
        if not recognized_text or len(recognized_text.strip()) < 2:
            return

        # Telegram ограничивает сообщения до 4096 символов
        safe_text = recognized_text.strip()[:3800]
        escaped = html.escape(safe_text)

        header = "📹 <b>Расшифровка кружка:</b>\n" if is_video_note else "🗣 <b>Расшифровка:</b>\n"
        bot.reply_to(message, f"{header}<i>{escaped}</i>", parse_mode="HTML")

        # Если в голосовом обратились к Лизе или это личка — даем ей ответить
        is_group = message.chat.type in ("group", "supergroup")
        addressed = (not is_group) or bool(WAKE_RE.match(safe_text))

        if addressed:
            from handlers import text_handler
            # Подставляем распознанный текст и отправляем в диспетчер Лизы
            message.text = safe_text
            text_handler(message)

    except Exception as exc:
        LOG.exception("[transcriber] Ошибка при обработке аудио: %s", exc)
