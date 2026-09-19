# -*- coding: utf-8 -*-
"""Сохранение и отправка форматированного текста Telegram «1 в 1».

Вместо HTML/Markdown храним исходный текст и список entities ровно в том
виде, в каком их прислал Telegram: жирный, курсив, спойлер, цитаты, ссылки,
premium (custom) emoji, date_time и любые будущие типы. При отправке
передаём их обратно как есть, поэтому ничего не теряется и не нужно
экранировать спецсимволы.

Premium emoji в сообщениях бота работают с Bot API 9.4, если у владельца бота
есть Telegram Premium. Отправка возможна в личные чаты, группы и супергруппы,
в каналы нельзя.

Отправка идёт прямым HTTP-запросом: pyTelegramBotAPI 4.14 при сериализации
entities выбрасывает поля, которых не знает (например unix_time у date_time).
"""
import json
import logging
import time

import requests

from config import TOKEN

log = logging.getLogger("rich_text")

_API = f"https://api.telegram.org/bot{TOKEN}"
_SESSION = requests.Session()
_TIMEOUT = 15

# тип медиа в настройках публикации -> метод Bot API (имя поля совпадает с типом)
MEDIA_METHODS = {
    "photo": "sendPhoto",
    "video": "sendVideo",
    "animation": "sendAnimation",
    "document": "sendDocument",
    "audio": "sendAudio",
    "voice": "sendVoice",
}


class RichSendError(Exception):
    """Telegram отклонил запрос (или сеть недоступна)."""

    def __init__(self, description, error_code=None, retry_after=None):
        super().__init__(description)
        self.description = description or ""
        self.error_code = error_code
        self.retry_after = retry_after


def _scrub(value):
    """Токен бота не должен попадать в логи (он есть в URL запроса)."""
    text = str(value)
    return text.replace(TOKEN, "***") if TOKEN else text


def _utf16_len(text):
    # offset/length у entities Telegram считает в UTF-16 code units
    return len(text.encode("utf-16-le")) // 2


def _entity_to_dict(entity):
    if isinstance(entity, dict):
        return dict(entity)
    to_dict = getattr(entity, "to_dict", None)
    if callable(to_dict):
        try:
            return dict(to_dict())
        except Exception:
            pass
    out = {"type": getattr(entity, "type", None),
           "offset": getattr(entity, "offset", None),
           "length": getattr(entity, "length", None)}
    for field in ("url", "language", "custom_emoji_id"):
        value = getattr(entity, field, None)
        if value:
            out[field] = value
    user = getattr(entity, "user", None)
    if user is not None and getattr(user, "id", None):
        out["user"] = {"id": user.id, "is_bot": bool(getattr(user, "is_bot", False)),
                       "first_name": getattr(user, "first_name", "") or ""}
    return out


def _clean_item(item):
    """dict entity -> JSON-совместимый dict без пустых значений (или None, если не выйдет)."""
    out = {}
    for key, value in item.items():
        if value is None:
            continue
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        out[key] = value
    try:
        json.dumps(out)
    except (TypeError, ValueError):
        return None
    return out


def _raw_message_dict(message):
    raw = getattr(message, "json", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


def extract_entities(message, *, caption=False):
    """Список entities сообщения (или подписи) ровно как прислал Telegram.

    Берём сырой JSON сообщения, поэтому не теряются поля, о которых библиотека
    не знает. Entities с некорректным диапазоном отбрасываются, чтобы Telegram
    не отклонил отправку целиком. Возвращает [] если форматирования нет.
    """
    key = "caption_entities" if caption else "entities"
    text = (message.caption if caption else message.text) or ""
    raw = _raw_message_dict(message)
    items = raw.get(key) if raw is not None else None
    if items is None:
        items = [_entity_to_dict(e) for e in (getattr(message, key, None) or [])]

    limit = _utf16_len(text)
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item = _clean_item(item)
        if item is None:
            continue
        offset, length = item.get("offset"), item.get("length")
        if not item.get("type") or not isinstance(offset, int) or not isinstance(length, int):
            continue
        if offset < 0 or length <= 0 or offset + length > limit:
            continue
        result.append(item)
    return result


def markup_to_dict(markup):
    """InlineKeyboardMarkup (telebot) -> dict для Bot API. Пустые ряды убираем."""
    if markup is None:
        return None
    data = markup.to_dict() if hasattr(markup, "to_dict") else markup
    rows = [row for row in (data.get("inline_keyboard") or []) if row]
    return {"inline_keyboard": rows} if rows else None


def _call(method, params):
    for attempt in (1, 2):
        try:
            resp = _SESSION.post(f"{_API}/{method}", json=params, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RichSendError(f"network error: {_scrub(exc)}") from None
        try:
            data = resp.json()
        except ValueError:
            raise RichSendError(f"bad response (HTTP {resp.status_code})", resp.status_code) from None
        if data.get("ok"):
            return data["result"]
        retry_after = (data.get("parameters") or {}).get("retry_after")
        if data.get("error_code") == 429 and retry_after is not None and retry_after <= 5 and attempt == 1:
            time.sleep(retry_after + 0.2)
            continue
        raise RichSendError(data.get("description") or "", data.get("error_code"), retry_after)


def send_text(chat_id, text, entities=None, reply_markup=None, thread_id=None):
    """sendMessage с готовыми entities (без parse_mode). Возвращает result."""
    params = {"chat_id": chat_id, "text": text}
    if entities:
        params["entities"] = entities
    if reply_markup:
        params["reply_markup"] = reply_markup
    if thread_id:
        params["message_thread_id"] = thread_id
    return _call("sendMessage", params)


def send_media(chat_id, media_type, file_id, caption=None, caption_entities=None,
               reply_markup=None, thread_id=None):
    """sendPhoto/sendVideo/... с подписью и caption_entities. Возвращает result."""
    method = MEDIA_METHODS[media_type]
    params = {"chat_id": chat_id, media_type: file_id}
    if caption:
        params["caption"] = caption
        if caption_entities:
            params["caption_entities"] = caption_entities
    if reply_markup:
        params["reply_markup"] = reply_markup
    if thread_id:
        params["message_thread_id"] = thread_id
    return _call(method, params)


def is_format_error(exc):
    """Ошибка именно из-за форматирования (entities), а не из-за чата/прав."""
    desc = (getattr(exc, "description", "") or "").lower()
    return getattr(exc, "error_code", None) == 400 and any(
        marker in desc for marker in ("entit", "parse", "emoji"))
