# -*- coding: utf-8 -*-
"""Модуль мгновенного распознавания речи и видеокружков через Groq Whisper Large v3."""
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

# Только полная модель — она заметно точнее на суржике/смешанной ру-укр речи, чем turbo.
# Никакого текстового корректора после неё: модель должна понимать речь сама, без "причёсывания".
MODEL = "whisper-large-v3"

# Пороги отсева галлюцинаций — те же эвристики, что использует референсный
# декодер OpenAI Whisper (no_speech_threshold / logprob_threshold / compression_ratio_threshold),
# применённые к сегментам, которые Groq отдаёт в verbose_json.
NO_SPEECH_THRESHOLD = 0.6
LOGPROB_THRESHOLD = -1.0
COMPRESSION_RATIO_THRESHOLD = 2.4

# Типовые "ютубовские" галлюцинации — Whisper обучался на субтитрах роликов с YouTube/TikTok
# и на тишине/шуме иногда достаёт эти фразы из training-данных вместо реальной речи.
_HALLUCINATION_PATTERNS = [
    r"подпис(ыва[ий]тесь|ывайся|ывайтесь)\s+на\s+(мо[йи]|наш)?\s*канал",
    r"ставь?те?\s+лайк",
    r"не\s+забудьте?\s+подписаться",
    r"спасибо\s+за\s+просмотр",
    r"увидимся\s+в\s+следующ",
    r"редактор\s+субтитров",
    r"субтитры\s+(делал|сделал|создал)",
    r"продолжение\s+следует",
    r"\[?музыка\]?",
    r"\(?аплодисменты\)?",
    r"спасибо\s+за\s+внимание",
]
_HALLUCINATION_RE = re.compile("|".join(_HALLUCINATION_PATTERNS), re.IGNORECASE)

# Подсказка модели: даём ей живые примеры суржика и смеси ру/укр лексики и имён собственных,
# чтобы она с первого прохода правильно ловила переключение между языками внутри одной фразы.
WHISPER_PROMPT = (
    "Это обычное голосовое сообщение в Telegram от русскоязычного и украиноязычного собеседника. "
    "Речь часто смешанная — суржик: русские и украинские слова в одной фразе. "
    "Примеры: Привет, як справи? Та ні, всьо норм, дякую. Слухай, я щас зайнятий, давай пізніше созвонимся. "
    "Ти шо, серйозно? Ну добре, домовились. Короче, я тобі зараз скину файл."
)


def _get_key():
    with _key_lock:
        return _keys[_key_idx % len(_keys)] if _keys else None


def _switch_key():
    global _key_idx
    with _key_lock:
        _key_idx += 1


def _collapse_repetitions(text: str) -> str:
    """Схлопывает зацикленные повторы слов/фраз — классический баг Whisper на шуме/тишине."""
    if not text:
        return text
    # одно и то же слово 3+ раза подряд -> оставляем одно
    text = re.sub(r"\b(\S+)(\s+\1\b){2,}", r"\1", text, flags=re.IGNORECASE)
    # одна и та же короткая фраза (1-5 слов) 3+ раза подряд -> оставляем одно вхождение
    text = re.sub(r"((?:\S+\s+){1,5}\S+)(\s+\1){2,}", r"\1", text, flags=re.IGNORECASE)
    return text


def _filter_segments(payload: dict) -> str:
    """Отбрасывает сегменты, которые сам Whisper считает тишиной/шумом/зацикливанием."""
    segments = payload.get("segments")
    if not segments:
        return (payload.get("text") or "").strip()

    kept = []
    for seg in segments:
        seg_text = (seg.get("text") or "").strip()
        if not seg_text:
            continue
        no_speech = seg.get("no_speech_prob", 0.0) or 0.0
        avg_logprob = seg.get("avg_logprob", 0.0) or 0.0
        compression = seg.get("compression_ratio", 0.0) or 0.0

        if no_speech > NO_SPEECH_THRESHOLD and avg_logprob < LOGPROB_THRESHOLD:
            continue  # почти наверняка тишина/шум, а не речь
        if compression > COMPRESSION_RATIO_THRESHOLD:
            continue  # зацикленный повтор — классическая галлюцинация
        if _HALLUCINATION_RE.search(seg_text):
            continue  # типовой артефакт обучения на YouTube/TikTok субтитрах

        kept.append(seg_text)

    joined = " ".join(kept).strip()
    return joined if joined else (payload.get("text") or "").strip()


def transcribe_audio_bytes(audio_bytes: bytes, filename: str, mime_type: str, force_lang: str = None) -> str | None:
    """Отправляет аудиофайл в Groq Whisper Large v3."""
    if not _keys:
        LOG.error("[transcriber] Нет доступных ключей GROQ_API_KEY")
        return None

    for _ in range(len(_keys)):
        key = _get_key()
        if not key:
            return None
        try:
            files = {
                "file": (filename, audio_bytes, mime_type)
            }
            data = {
                "model": MODEL,
                "response_format": "verbose_json",  # нужен для сегментов и метрик галлюцинаций
                "temperature": 0.0,
                "prompt": WHISPER_PROMPT,
            }
            if force_lang:
                data["language"] = force_lang

            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files=files,
                data=data,
                timeout=(5, 30),
            )
            if resp.status_code == 200:
                payload = resp.json()
                text = _filter_segments(payload)
                text = _collapse_repetitions(text)
                return text.strip()
            elif resp.status_code in (401, 429, 503):
                _switch_key()
                continue
            else:
                break
        except Exception:
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

        if getattr(media_obj, "file_size", 0) > 20 * 1024 * 1024:
            bot.reply_to(message, "⚠️ Запись слишком длинная для расшифровки.")
            return

        file_info = bot.get_file(media_obj.file_id)
        audio_bytes = bot.download_file(file_info.file_path)

        filename = "circle.mp4" if is_video_note else "voice.ogg"
        mime_type = "video/mp4" if is_video_note else "audio/ogg"

        # Распознаём звук — без второго прохода через текстовый корректор.
        recognized_text = transcribe_audio_bytes(audio_bytes, filename, mime_type)
        if not recognized_text or len(recognized_text.strip()) < 2:
            return

        # Проверка на полное отсутствие кириллицы (жесткая галлюцинация: модель услышала
        # не ру/укр речь и уехала в другой язык/алфавит) — форсируем язык и пробуем ещё раз.
        has_cyrillic = bool(re.search(r'[а-яА-ЯёЁіІїЇєЄґҐ]', recognized_text))
        if not has_cyrillic:
            LOG.warning("[transcriber] Фолбек на укр язык.")
            recognized_text = transcribe_audio_bytes(audio_bytes, filename, mime_type, force_lang="uk")
            if not recognized_text or not bool(re.search(r'[а-яА-ЯёЁіІїЇєЄґҐ]', recognized_text)):
                return

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

# updated 2026-09-18
