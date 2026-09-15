# -*- coding: utf-8 -*-
"""Модуль мгновенного распознавания речи и видеокружков через Groq Whisper с умной коррекцией."""
import html
import logging
import threading
import re
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


def transcribe_audio_bytes(audio_bytes: bytes, filename: str, mime_type: str, force_lang: str = None) -> str | None:
    """Отправляет аудиофайл в Groq Whisper API (Уши)."""
    if not _keys:
        LOG.error("[transcriber] Нет доступных ключей GROQ_API_KEY")
        return None

    models = ("whisper-large-v3", "whisper-large-v3-turbo")

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
                    "prompt": "Это обычное голосовое сообщение в Telegram. Русская и украинская речь, суржик. Привет, як справи? Ага, хорошо, дякую. Давай."
                }
                if force_lang:
                    data["language"] = force_lang

                resp = requests.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key}"},
                    files=files,
                    data=data,
                    timeout=(5, 25),
                )
                if resp.status_code == 200:
                    return (resp.json().get("text") or "").strip()
                elif resp.status_code in (401, 429, 503):
                    _switch_key()
                    continue
                else:
                    break
            except Exception:
                _switch_key()
    return None


def clean_transcription_with_llm(raw_text: str) -> str:
    """Отправляет кривую расшифровку в текстовый ИИ для умной коррекции (Мозг)."""
    if not _keys or len(raw_text) < 5:
        return raw_text

    for _ in range(len(_keys)):
        key = _get_key()
        if not key:
            return raw_text
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama3-8b-8192",  # Супер-быстрая и умная модель для коррекции
                    "messages": [
                        {
                            "role": "system", 
                            "content": (
                                "Ты корректор. Твоя задача — исправить кривой текст после распознавания голоса нейросетью. "
                                "Язык говорящего: смесь русского, украинского и суржика. "
                                "Ориентируйся на логику. Убери галлюцинации (повторяющиеся слова, бессмысленные обрывки, случайные иностранные слова). "
                                "Исправь опечатки и расставь правильную пунктуацию. Сохрани оригинальный смысл и тон. "
                                "Отвечай ТОЛЬКО исправленным текстом, никаких вводных слов, пояснений и кавычек."
                            )
                        },
                        {"role": "user", "content": raw_text}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 1024
                },
                timeout=5
            )
            if resp.status_code == 200:
                cleaned = resp.json()["choices"][0]["message"]["content"].strip()
                return cleaned if cleaned else raw_text
            elif resp.status_code in (401, 429, 503):
                _switch_key()
                continue
            else:
                break
        except Exception:
            _switch_key()
    return raw_text


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

        if getattr(media_obj, "file_size", 0) > 20 * 1024 * 1024:
            bot.reply_to(message, "⚠️ Запись слишком длинная для расшифровки.")
            return

        file_info = bot.get_file(media_obj.file_id)
        audio_bytes = bot.download_file(file_info.file_path)

        filename = "circle.mp4" if is_video_note else "voice.ogg"
        mime_type = "video/mp4" if is_video_note else "audio/ogg"

        # ШАГ 1: Распознаем звук (Уши)
        recognized_text = transcribe_audio_bytes(audio_bytes, filename, mime_type)
        if not recognized_text or len(recognized_text.strip()) < 2:
            return

        # Проверка на полное отсутствие кириллицы (жесткая галлюцинация)
        has_cyrillic = bool(re.search(r'[а-яА-ЯёЁіІїЇєЄґҐ]', recognized_text))
        if not has_cyrillic:
            LOG.warning("[transcriber] Фолбек на укр язык.")
            recognized_text = transcribe_audio_bytes(audio_bytes, filename, mime_type, force_lang="uk")
            if not recognized_text or not bool(re.search(r'[а-яА-ЯёЁіІїЇєЄґҐ]', recognized_text)):
                return

        # ШАГ 2: Чистим и правим логику через Llama 3 (Мозг)
        cleaned_text = clean_transcription_with_llm(recognized_text)
        if cleaned_text:
            recognized_text = cleaned_text

        safe_text = recognized_text.strip()[:3800]
        escaped = html.escape(safe_text)

        header = "📹 <b>Расшифровка кружка:</b>\n" if is_video_note else "🗣 <b>Расшифровка:</b>\n"
        bot.reply_to(message, f"{header}<blockquote><code>{escaped}</code></blockquote>", parse_mode="HTML")

        is_group = message.chat.type in ("group", "supergroup")
        addressed = (not is_group) or bool(WAKE_RE.match(safe_text))

        if addressed:
            from handlers import text_handler
            message.text = safe_text
            text_handler(message)

    except Exception as exc:
        LOG.exception("[transcriber] Ошибка при обработке аудио: %s", exc)
