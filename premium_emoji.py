# -*- coding: utf-8 -*-
"""Управление premium (custom) emoji для кнопок автопубликаций.

Логика:
  - Администратор пересылает боту (в личку) сообщение с премиум-эмодзи
    или отправляет их напрямую в чат командой /addemoji.
  - Бот читает entities типа 'custom_emoji', получает preview через
    getCustomEmojiStickers и сохраняет в таблицу premium_emojis.
  - miniapp.py отдаёт список через /api/emojis.
  - При отправке сообщения с кнопками settings_core.py использует
    прямой HTTP-запрос к Bot API с полем entities в тексте кнопки,
    что позволяет отобразить premium emoji (недокументировано, но работает).
"""
import json
import logging
import time

import requests

from config import TOKEN
from database import conn, db_lock

log = logging.getLogger("premium_emoji")

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def save_emoji(emoji_id: str, name: str, preview_url: str | None, group_id: str = "") -> None:
    """Сохранить или обновить premium emoji в БД."""
    with db_lock:
        conn.execute(
            """INSERT INTO premium_emojis (emoji_id, group_id, name, preview_url, added_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(emoji_id, group_id) DO UPDATE SET
                   name=EXCLUDED.name,
                   preview_url=EXCLUDED.preview_url,
                   added_at=EXCLUDED.added_at""",
            (str(emoji_id), str(group_id), str(name or ""), preview_url, time.time()),
        )
        conn.commit()


def preview_proxy_url(emoji_id: str) -> str:
    """Стабильный URL прокси-превью для Mini App.

    Telegram file URLs содержат bot token и не являются хорошим источником для
    браузерного превью: они могут истекать, а сам token нельзя отдавать клиенту.
    Поэтому Mini App получает картинку через наш backend, по одному emoji_id.
    """
    from urllib.parse import quote
    return f"/api/stickerImage?emoji_id={quote(str(emoji_id), safe='')}"


def get_emojis(group_id: str = "", query: str = "") -> list[dict]:
    """Вернуть список premium emoji для группы (+ глобальные).

    Порядок: глобальные (group_id='') + эмодзи этой группы, без дублей.
    Для Mini App previewUrl всегда указывает на безопасный backend-прокси,
    а не раскрывает Telegram file URL с bot token.
    """
    with db_lock:
        rows = conn.execute(
            """SELECT emoji_id, name, preview_url FROM premium_emojis
               WHERE group_id IN ('', ?)
               ORDER BY group_id DESC, added_at DESC""",
            (str(group_id),),
        ).fetchall()

    seen: set[str] = set()
    result = []
    for emoji_id, name, preview_url in rows:
        if emoji_id in seen:
            continue
        seen.add(emoji_id)
        if query and query.lower() not in name.lower():
            continue
        result.append({"id": emoji_id, "name": name, "previewUrl": preview_proxy_url(emoji_id)})
    return result


def delete_emoji(emoji_id: str, group_id: str = "") -> None:
    """Удалить emoji из БД (только для указанной группы или глобально)."""
    with db_lock:
        conn.execute(
            "DELETE FROM premium_emojis WHERE emoji_id=? AND group_id=?",
            (str(emoji_id), str(group_id)),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

_API = f"https://api.telegram.org/bot{TOKEN}"
_FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
_SESSION = requests.Session()
_PREVIEW_CACHE: dict[str, tuple[float, str, bytes]] = {}
_PREVIEW_CACHE_TTL = 600


def _tg(method: str, **params) -> dict:
    """Минимальный raw-запрос к Bot API."""
    resp = _SESSION.post(f"{_API}/{method}", json=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_preview_bytes(emoji_id: str) -> tuple[str, bytes] | None:
    """Получить статичное превью custom emoji для backend-прокси.

    Возвращает (content_type, bytes). Результат кэшируется, чтобы открытие
    Mini App не создавало отдельный запрос к Telegram для каждого клика.
    """
    key = str(emoji_id)
    now = time.time()
    cached = _PREVIEW_CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1], cached[2]

    try:
        data = _tg("getCustomEmojiStickers", custom_emoji_ids=[key])
        stickers = data.get("result") or []
        if not stickers:
            return None
        sticker = stickers[0]
        thumb = sticker.get("thumbnail") or sticker.get("thumb")
        source = thumb or sticker
        file_id = source.get("file_id") if isinstance(source, dict) else None
        if not file_id:
            return None
        fp = _tg("getFile", file_id=file_id)
        file_path = (fp.get("result") or {}).get("file_path")
        if not file_path:
            return None
        resp = _SESSION.get(f"{_FILE_API}/{file_path}", timeout=15)
        resp.raise_for_status()
        content_type = (resp.headers.get("Content-Type") or "image/webp").split(";", 1)[0]
        payload = resp.content
        if not payload or len(payload) > 2 * 1024 * 1024:
            return None
        _PREVIEW_CACHE[key] = (now + _PREVIEW_CACHE_TTL, content_type, payload)
        return content_type, payload
    except Exception as exc:
        log.warning("[premium_emoji] fetch_preview_bytes(%s) error: %s", emoji_id, exc)
        return None


def fetch_preview_url(emoji_id: str) -> str | None:
    """Получить URL превью для custom_emoji_id через getCustomEmojiStickers."""
    try:
        data = _tg("getCustomEmojiStickers", custom_emoji_ids=[str(emoji_id)])
        stickers = data.get("result") or []
        if not stickers:
            return None
        sticker = stickers[0]
        thumb = sticker.get("thumbnail") or sticker.get("thumb")
        file_id = (thumb or {}).get("file_id") or sticker.get("file_id")
        if not file_id:
            return None
        fp = _tg("getFile", file_id=file_id)
        file_path = (fp.get("result") or {}).get("file_path")
        if not file_path:
            return None
        return f"{_FILE_API}/{file_path}"
    except Exception as exc:
        log.warning("[premium_emoji] fetch_preview_url(%s) error: %s", emoji_id, exc)
        return None


def extract_custom_emoji_ids(entities: list) -> list[str]:
    """Извлечь custom_emoji_id из списка entities (telebot или dict)."""
    ids = []
    for ent in (entities or []):
        # telebot entity object
        etype = getattr(ent, "type", None) or (ent.get("type") if isinstance(ent, dict) else None)
        if etype == "custom_emoji":
            eid = getattr(ent, "custom_emoji_id", None) or (
                ent.get("custom_emoji_id") if isinstance(ent, dict) else None
            )
            if eid:
                ids.append(str(eid))
    return ids


def process_message_for_emojis(message, group_id: str = "") -> list[str]:
    """Обработать входящее сообщение, сохранить найденные premium emoji.

    Возвращает список сохранённых emoji_id.
    """
    entities = getattr(message, "entities", None) or getattr(message, "caption_entities", None) or []
    emoji_ids = extract_custom_emoji_ids(entities)
    saved = []
    for eid in emoji_ids:
        preview = fetch_preview_url(eid)
        # Имя — порядковый номер или имя если задано
        existing = get_emojis(group_id=group_id)
        name = f"Эмодзи {len(existing) + 1}"
        save_emoji(eid, name, preview, group_id=group_id)
        saved.append(eid)
        log.info("[premium_emoji] saved emoji_id=%s group=%s preview=%s", eid, group_id, preview)
    return saved


# ---------------------------------------------------------------------------
# Raw send helpers для custom emoji в кнопках (Bot API 9.4+)
# ---------------------------------------------------------------------------

def _build_button_with_emoji(btn_text: str, emoji_id: str | None, **kwargs) -> dict:
    """Построить InlineKeyboardButton с официальным custom emoji icon.

    Начиная с Bot API 9.4 у InlineKeyboardButton есть поле
    ``icon_custom_emoji_id``. Оно как раз предназначено для показа
    premium-эмодзи непосредственно перед текстом кнопки.

    Для реальной публикации используется официальный ``icon_custom_emoji_id``.
    Для браузерного предпросмотра используется отдельный proxy endpoint
    ``/api/stickerImage`` — по смыслу такой же подход, как у GroupHelpBot.
    """
    btn = {"text": str(btn_text or "Кнопка")}
    if emoji_id:
        btn["icon_custom_emoji_id"] = str(emoji_id)
    btn.update(kwargs)
    return btn


def send_message_with_emoji_buttons(
    chat_id: int,
    text: str,
    reply_markup_rows: list[list[dict]],
    *,
    parse_mode: str = "HTML",
    message_thread_id: int | None = None,
) -> dict | None:
    """Отправить сообщение с кнопками, поддерживающими premium emoji.

    reply_markup_rows — list of rows, каждая row — list of button dicts:
        {text, emoji_id (optional), url/callback_data/...}

    Возвращает dict ответа Bot API или None при ошибке.
    """
    inline_keyboard = []
    for row in reply_markup_rows:
        row_buttons = []
        for b in row:
            emoji_id = b.get("custom_emoji_id") or b.get("emoji_id")
            btn_text = b.get("text", "")
            extra = {}
            if b.get("url"):
                extra["url"] = b["url"]
            elif b.get("callback_data"):
                extra["callback_data"] = b["callback_data"]
            row_buttons.append(_build_button_with_emoji(btn_text, emoji_id, **extra))
        inline_keyboard.append(row_buttons)

    params: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "reply_markup": {"inline_keyboard": inline_keyboard},
    }
    if message_thread_id:
        params["message_thread_id"] = message_thread_id

    try:
        resp = _SESSION.post(f"{_API}/sendMessage", json=params, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            log.error("[premium_emoji] sendMessage failed: %s", data)
        return data
    except Exception as exc:
        log.error("[premium_emoji] sendMessage exception: %s", exc)
        return None


def send_photo_with_emoji_buttons(
    chat_id: int,
    photo: str,
    caption: str,
    reply_markup_rows: list[list[dict]],
    *,
    parse_mode: str = "HTML",
    message_thread_id: int | None = None,
) -> dict | None:
    """Отправить фото с кнопками, поддерживающими premium emoji."""
    inline_keyboard = []
    for row in reply_markup_rows:
        row_buttons = []
        for b in row:
            emoji_id = b.get("custom_emoji_id") or b.get("emoji_id")
            btn_text = b.get("text", "")
            extra = {}
            if b.get("url"):
                extra["url"] = b["url"]
            elif b.get("callback_data"):
                extra["callback_data"] = b["callback_data"]
            row_buttons.append(_build_button_with_emoji(btn_text, emoji_id, **extra))
        inline_keyboard.append(row_buttons)

    params: dict = {
        "chat_id": chat_id,
        "photo": photo,
        "caption": caption,
        "parse_mode": parse_mode,
        "reply_markup": {"inline_keyboard": inline_keyboard},
    }
    if message_thread_id:
        params["message_thread_id"] = message_thread_id

    try:
        resp = _SESSION.post(f"{_API}/sendPhoto", json=params, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            log.error("[premium_emoji] sendPhoto failed: %s", data)
        return data
    except Exception as exc:
        log.error("[premium_emoji] sendPhoto exception: %s", exc)
        return None


def send_media_with_emoji_buttons(
    chat_id: int,
    media_type: str,
    file_id: str,
    caption: str,
    reply_markup_rows: list[list[dict]],
    *,
    parse_mode: str = "HTML",
    message_thread_id: int | None = None,
) -> dict | None:
    """Отправить медиа (video/animation/document/audio) с emoji-кнопками."""
    method_map = {
        "photo": "sendPhoto",
        "video": "sendVideo",
        "animation": "sendAnimation",
        "document": "sendDocument",
        "audio": "sendAudio",
    }
    method = method_map.get(media_type, "sendDocument")
    field = media_type if media_type in method_map else "document"

    inline_keyboard = []
    for row in reply_markup_rows:
        row_buttons = []
        for b in row:
            emoji_id = b.get("custom_emoji_id") or b.get("emoji_id")
            btn_text = b.get("text", "")
            extra = {}
            if b.get("url"):
                extra["url"] = b["url"]
            elif b.get("callback_data"):
                extra["callback_data"] = b["callback_data"]
            row_buttons.append(_build_button_with_emoji(btn_text, emoji_id, **extra))
        inline_keyboard.append(row_buttons)

    params: dict = {
        "chat_id": chat_id,
        field: file_id,
        "caption": caption,
        "parse_mode": parse_mode,
        "reply_markup": {"inline_keyboard": inline_keyboard},
    }
    if message_thread_id:
        params["message_thread_id"] = message_thread_id

    try:
        resp = _SESSION.post(f"{_API}/{method}", json=params, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            log.error("[premium_emoji] %s failed: %s", method, data)
        return data
    except Exception as exc:
        log.error("[premium_emoji] %s exception: %s", method, exc)
        return None


def has_emoji_buttons(rows: list[list[dict]]) -> bool:
    """Проверить, есть ли хотя бы одна кнопка с custom_emoji_id."""
    for row in (rows or []):
        for b in (row or []):
            if b.get("custom_emoji_id"):
                return True
    return False

# updated 2026-09-18
