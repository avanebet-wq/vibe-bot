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

  Кеш превью: байты картинки сохраняются в таблицу emoji_preview_cache
  и никогда не устаревают — Telegram выдаёт один и тот же стикер навсегда
  для данного emoji_id, поэтому TTL не нужен.

  Групповые паки: пользователи могут добавлять паки по имени сета, паки
  хранятся в group_emoji_packs и видны только в своей группе.
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

def _normalize_emoji(value: str | None) -> str:
    """Normalize the ordinary emoji used for search without changing display data."""
    return (str(value or "")).replace("\ufe0f", "").strip()


def _pack_row_to_dict(row) -> dict:
    pack_id, set_name, created_at, preview_emoji_id = row
    return {
        "id": int(pack_id),
        "setName": str(set_name or ""),
        "createdAt": float(created_at or 0),
        "previewEmojiId": str(preview_emoji_id) if preview_emoji_id else None,
    }


def save_pack(set_name: str, *, preview_emoji_id: str | None = None) -> int:
    """Get/create a global pack, preserving its original creation order."""
    set_name = str(set_name or "").strip()
    if not set_name:
        raise ValueError("set_name is required")
    now = time.time()
    with db_lock:
        row = conn.execute(
            "SELECT pack_id FROM premium_emoji_packs WHERE set_name=?",
            (set_name,),
        ).fetchone()
        if row:
            pack_id = int(row[0])
            if preview_emoji_id:
                conn.execute(
                    "UPDATE premium_emoji_packs SET preview_emoji_id=COALESCE(preview_emoji_id, ?) WHERE pack_id=?",
                    (str(preview_emoji_id), pack_id),
                )
            conn.commit()
            return pack_id
        cur = conn.execute(
            "INSERT INTO premium_emoji_packs(set_name,created_at,preview_emoji_id) VALUES(?,?,?) RETURNING pack_id",
            (set_name, now, str(preview_emoji_id) if preview_emoji_id else None),
        )
        pack_id = int(cur.fetchone()[0])
        conn.commit()
        return pack_id


def save_emoji(
    emoji_id: str,
    name: str,
    preview_url: str | None,
    *,
    pack_id: int,
    emoji: str = "",
    added_at: float | None = None,
) -> None:
    """Save/update a premium emoji in its global pack."""
    ts = float(added_at or time.time())
    with db_lock:
        conn.execute(
            """INSERT INTO premium_emojis (emoji_id, pack_id, emoji, name, preview_url, added_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(emoji_id) DO UPDATE SET
                   pack_id=EXCLUDED.pack_id,
                   emoji=EXCLUDED.emoji,
                   name=EXCLUDED.name,
                   preview_url=EXCLUDED.preview_url""",
            (str(emoji_id), int(pack_id), str(emoji or ""), str(name or ""), preview_url, ts),
        )
        conn.execute(
            "UPDATE premium_emoji_packs SET preview_emoji_id=COALESCE(preview_emoji_id, ?) WHERE pack_id=?",
            (str(emoji_id), int(pack_id)),
        )
        conn.commit()


def preview_proxy_url(emoji_id: str) -> str:
    """Stable backend URL for Mini App custom-emoji previews."""
    from urllib.parse import quote
    return f"/api/stickerImage?emoji_id={quote(str(emoji_id), safe='')}"


def get_packs() -> list[dict]:
    """Return all global packs in creation order (oldest first)."""
    with db_lock:
        packs = conn.execute(
            """SELECT pack_id, set_name, created_at, preview_emoji_id
               FROM premium_emoji_packs
               ORDER BY created_at ASC, pack_id ASC"""
        ).fetchall()
        result = []
        for row in packs:
            pack = _pack_row_to_dict(row)
            emojis = conn.execute(
                """SELECT emoji_id, emoji, name, preview_url, added_at
                   FROM premium_emojis
                   WHERE pack_id=?
                   ORDER BY added_at ASC, emoji_id ASC""",
                (pack["id"],),
            ).fetchall()
            pack["emojis"] = [
                {
                    "id": str(eid),
                    "emoji": str(em or ""),
                    "name": str(name or ""),
                    "previewUrl": preview_proxy_url(str(eid)),
                    "addedAt": float(added_at or 0),
                }
                for eid, em, name, preview_url, added_at in emojis
            ]
            if not pack["previewEmojiId"] and pack["emojis"]:
                pack["previewEmojiId"] = pack["emojis"][0]["id"]
            pack["previewUrl"] = (
                preview_proxy_url(pack["previewEmojiId"])
                if pack["previewEmojiId"] else None
            )
            result.append(pack)
        return result


def get_pack_list(chat_id: str | None = None) -> list[dict]:
    """Return lightweight pack metadata for the Mini App tab strip.

    If chat_id is given, also appends group-local packs for that chat
    that are not already in the global library (marked with isLocal=True).
    """
    with db_lock:
        rows = conn.execute(
            """SELECT p.pack_id, p.set_name, p.created_at, p.preview_emoji_id,
                      COUNT(e.emoji_id)
               FROM premium_emoji_packs p
               LEFT JOIN premium_emojis e ON e.pack_id=p.pack_id
               GROUP BY p.pack_id, p.set_name, p.created_at, p.preview_emoji_id
               ORDER BY p.created_at ASC, p.pack_id ASC"""
        ).fetchall()
    result = []
    global_set_names = set()
    for pack_id, set_name, created_at, preview_emoji_id, emoji_count in rows:
        global_set_names.add(set_name)
        result.append({
            "id": int(pack_id),
            "setName": str(set_name or ""),
            "createdAt": float(created_at or 0),
            "previewEmojiId": str(preview_emoji_id) if preview_emoji_id else None,
            "previewUrl": preview_proxy_url(str(preview_emoji_id)) if preview_emoji_id else None,
            "emojiCount": int(emoji_count or 0),
            "isLocal": False,
        })

    # Append group-local packs
    if chat_id:
        with db_lock:
            local_rows = conn.execute(
                """SELECT g.set_name, g.pack_id, g.added_at
                   FROM group_emoji_packs g
                   WHERE g.chat_id=?
                   ORDER BY g.added_at ASC""",
                (str(chat_id),),
            ).fetchall()
        for set_name, pack_id, added_at in local_rows:
            if set_name in global_set_names:
                continue  # already in global list
            # The pack may or may not exist in premium_emoji_packs yet
            preview_emoji_id = None
            emoji_count = 0
            if pack_id:
                with db_lock:
                    pr = conn.execute(
                        "SELECT preview_emoji_id FROM premium_emoji_packs WHERE pack_id=?",
                        (pack_id,),
                    ).fetchone()
                    if pr:
                        preview_emoji_id = pr[0]
                    emoji_count = conn.execute(
                        "SELECT COUNT(*) FROM premium_emojis WHERE pack_id=?",
                        (pack_id,),
                    ).fetchone()[0]
            result.append({
                "id": pack_id or -1,
                "setName": str(set_name or ""),
                "createdAt": float(added_at or 0),
                "previewEmojiId": str(preview_emoji_id) if preview_emoji_id else None,
                "previewUrl": preview_proxy_url(str(preview_emoji_id)) if preview_emoji_id else None,
                "emojiCount": int(emoji_count or 0),
                "isLocal": True,
            })
    return result


def get_emojis(group_id: str = "", query: str = "", chat_id: str | None = None) -> list[dict]:
    """Compatibility flat view; library itself is now global and pack-based.

    When query is supplied it matches the ordinary emoji associated with the
    custom emoji. With an empty query, all emoji are returned in pack order.
    If chat_id is given, group-local packs are included in results.
    """
    del group_id  # packs are deliberately global
    q = _normalize_emoji(query)
    with db_lock:
        sql = """SELECT e.emoji_id, e.emoji, e.name, e.preview_url, e.pack_id, p.created_at, e.added_at
                 FROM premium_emojis e
                 JOIN premium_emoji_packs p ON p.pack_id=e.pack_id"""
        params = []
        if q:
            sql += " WHERE REPLACE(e.emoji, ?, '') = ?"
            params.extend(["\ufe0f", q])
        sql += " ORDER BY p.created_at ASC, p.pack_id ASC, e.added_at ASC, e.emoji_id ASC"
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        {
            "id": str(eid),
            "name": str(name or ""),
            "emoji": str(em or ""),
            "packId": int(pack_id),
            "previewUrl": preview_proxy_url(str(eid)),
        }
        for eid, em, name, preview_url, pack_id, _created, _added in rows
    ]


def get_pack_emojis(pack_id: int) -> list[dict]:
    """Return one pack's emoji in insertion order."""
    with db_lock:
        rows = conn.execute(
            """SELECT emoji_id, emoji, name, added_at
               FROM premium_emojis WHERE pack_id=?
               ORDER BY added_at ASC, emoji_id ASC""",
            (int(pack_id),),
        ).fetchall()
    return [
        {
            "id": str(eid),
            "emoji": str(em or ""),
            "name": str(name or ""),
            "previewUrl": preview_proxy_url(str(eid)),
            "addedAt": float(added_at or 0),
        }
        for eid, em, name, added_at in rows
    ]


def search_emojis(query: str = "") -> list[dict]:
    """Search globally by the ordinary emoji represented by a custom emoji."""
    return get_emojis(query=query)


def delete_emoji(emoji_id: str, group_id: str = "") -> None:
    """Delete one emoji from the global library; keep empty packs for ordering."""
    del group_id
    with db_lock:
        conn.execute("DELETE FROM premium_emojis WHERE emoji_id=?", (str(emoji_id),))
        conn.commit()


# ---------------------------------------------------------------------------
# Group-local pack management
# ---------------------------------------------------------------------------

def add_group_pack(chat_id: str, set_name: str, added_by: str | None = None) -> dict:
    """Register a pack as visible in a specific group.

    Returns {"ok": True, "already": bool, "pack_id": int|None}.
    The pack's emojis are fetched from Telegram and stored if not yet in DB.
    """
    set_name = str(set_name or "").strip()
    chat_id = str(chat_id)
    now = time.time()

    # Check if already registered globally or locally
    with db_lock:
        global_row = conn.execute(
            "SELECT pack_id FROM premium_emoji_packs WHERE set_name=?", (set_name,)
        ).fetchone()
        local_row = conn.execute(
            "SELECT id FROM group_emoji_packs WHERE chat_id=? AND set_name=?",
            (chat_id, set_name),
        ).fetchone()

    if local_row and global_row:
        return {"ok": True, "already": True, "pack_id": int(global_row[0])}

    # Fetch the sticker set from Telegram to get emoji_ids
    try:
        data = _tg("getStickerSet", name=set_name)
    except Exception as exc:
        log.warning("[premium_emoji] getStickerSet(%s) failed: %s", set_name, exc)
        return {"ok": False, "error": "Пак не найден в Telegram"}

    stickers = (data.get("result") or {}).get("stickers") or []
    # Filter custom emoji only
    custom_stickers = [s for s in stickers if s.get("custom_emoji_id")]
    if not custom_stickers:
        return {"ok": False, "error": "В этом паке нет премиум-эмодзи"}

    # Save to global packs/emojis so they can be used by the group
    preview_emoji_id = None
    pack_id = None
    if custom_stickers:
        first_eid = str(custom_stickers[0]["custom_emoji_id"])
        pack_id = save_pack(set_name, preview_emoji_id=first_eid)
        preview_emoji_id = first_eid
        for s in custom_stickers:
            eid = str(s.get("custom_emoji_id") or "")
            if not eid:
                continue
            ordinary_emoji = str(s.get("emoji") or "")
            save_emoji(
                eid,
                ordinary_emoji or f"Эмодзи {eid}",
                preview_proxy_url(eid),
                pack_id=pack_id,
                emoji=ordinary_emoji,
            )
            # Pre-warm the persistent cache for each emoji
            _cache_preview_async(eid)

    # Register in group_emoji_packs
    with db_lock:
        conn.execute(
            """INSERT INTO group_emoji_packs(chat_id, set_name, pack_id, added_by, added_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(chat_id, set_name) DO UPDATE SET pack_id=EXCLUDED.pack_id""",
            (chat_id, set_name, pack_id, str(added_by or ""), now),
        )
        conn.commit()

    return {"ok": True, "already": bool(local_row), "pack_id": pack_id}


def _cache_preview_async(emoji_id: str) -> None:
    """Fire-and-forget: fetch and cache preview bytes for emoji_id if not cached."""
    import threading
    def _do():
        try:
            fetch_preview_bytes(emoji_id)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def extract_set_name_from_link(link: str) -> str | None:
    """Extract sticker set name from t.me/addstickers/<name> or addstickers/<name>."""
    import re
    link = (link or "").strip()
    m = re.search(r"(?:t\.me/addstickers?/|addstickers?/)([A-Za-z0-9_]+)", link, re.I)
    if m:
        return m.group(1)
    # Maybe user just typed the set name directly
    if re.fullmatch(r"[A-Za-z0-9_]{3,64}", link):
        return link
    return None


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

_API = f"https://api.telegram.org/bot{TOKEN}"
_FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
_SESSION = requests.Session()


def _tg(method: str, **params) -> dict:
    """Minimal raw request to Bot API."""
    resp = _SESSION.post(f"{_API}/{method}", json=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_custom_emoji_stickers(emoji_ids: list[str]) -> list[dict]:
    """Fetch custom emoji metadata in Bot API batches of at most 200."""
    ids = [str(x) for x in emoji_ids if x]
    result: list[dict] = []
    for offset in range(0, len(ids), 200):
        chunk = ids[offset:offset + 200]
        try:
            data = _tg("getCustomEmojiStickers", custom_emoji_ids=chunk)
            result.extend(data.get("result") or [])
        except Exception as exc:
            log.warning("[premium_emoji] getCustomEmojiStickers batch failed: %s", exc)
    return result


def fetch_preview_bytes(emoji_id: str) -> tuple[str, bytes] | None:
    """Get a static thumbnail for the backend preview proxy.

    Order of lookup:
    1. Persistent DB cache (emoji_preview_cache) — never re-fetches once stored.
    2. Telegram file API — fetches and then stores permanently in DB.
    """
    key = str(emoji_id)

    # 1. Check persistent DB cache first
    with db_lock:
        cached = conn.execute(
            "SELECT content_type, payload FROM emoji_preview_cache WHERE emoji_id=?",
            (key,),
        ).fetchone()
    if cached:
        return cached[0], bytes(cached[1])

    # 2. Fetch from Telegram
    try:
        stickers = fetch_custom_emoji_stickers([key])
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

        # 3. Store permanently in DB cache
        with db_lock:
            conn.execute(
                """INSERT INTO emoji_preview_cache(emoji_id, content_type, payload, cached_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(emoji_id) DO NOTHING""",
                (key, content_type, payload, time.time()),
            )
            conn.commit()

        return content_type, payload
    except Exception as exc:
        log.warning("[premium_emoji] fetch_preview_bytes(%s) error: %s", emoji_id, exc)
        return None


def fetch_preview_url(emoji_id: str) -> str | None:
    """Get Telegram's current file URL for compatibility; never exposed to Mini App."""
    try:
        stickers = fetch_custom_emoji_stickers([str(emoji_id)])
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
    """Extract custom_emoji_id from TeleBot/dict entities, preserving order."""
    ids = []
    for ent in (entities or []):
        etype = getattr(ent, "type", None) or (ent.get("type") if isinstance(ent, dict) else None)
        if etype == "custom_emoji":
            eid = getattr(ent, "custom_emoji_id", None) or (
                ent.get("custom_emoji_id") if isinstance(ent, dict) else None
            )
            if eid:
                ids.append(str(eid))
    return list(dict.fromkeys(ids))


def process_message_for_emojis(message, group_id: str = "") -> list[str]:
    """Add all custom emoji from a message to their global Telegram sticker-set packs."""
    del group_id  # library is intentionally global
    entities = getattr(message, "entities", None) or getattr(message, "caption_entities", None) or []
    emoji_ids = extract_custom_emoji_ids(entities)
    if not emoji_ids:
        return []

    stickers = fetch_custom_emoji_stickers(emoji_ids)
    by_id = {str(s.get("custom_emoji_id")): s for s in stickers if s.get("custom_emoji_id")}
    saved: list[str] = []
    pack_cache: dict[str, int] = {}
    for eid in emoji_ids:
        sticker = by_id.get(eid)
        if not sticker:
            continue
        set_name = str(sticker.get("set_name") or "").strip()
        if not set_name:
            set_name = f"__standalone_{eid}"
        pack_id = pack_cache.get(set_name)
        if pack_id is None:
            pack_id = save_pack(set_name, preview_emoji_id=eid)
            pack_cache[set_name] = pack_id

        ordinary_emoji = str(sticker.get("emoji") or "")
        save_emoji(
            eid,
            ordinary_emoji or f"Эмодзи {eid}",
            preview_proxy_url(eid),
            pack_id=pack_id,
            emoji=ordinary_emoji,
        )
        # Pre-warm persistent cache for this emoji
        _cache_preview_async(eid)
        saved.append(eid)
        log.info(
            "[premium_emoji] saved emoji_id=%s pack=%s set=%s emoji=%s",
            eid, pack_id, set_name, ordinary_emoji,
        )
    return saved


# ---------------------------------------------------------------------------
# Raw send helpers для custom emoji в кнопках (Bot API 9.4+)
# ---------------------------------------------------------------------------

def _build_button_with_emoji(btn_text: str, emoji_id: str | None, **kwargs) -> dict:
    """Построить InlineKeyboardButton с официальным custom emoji icon."""
    btn = {"text": str(btn_text or "Кнопка")}
    if emoji_id:
        btn["icon_custom_emoji_id"] = str(emoji_id)
    btn.update(kwargs)
    return btn


def _scrub(value) -> str:
    """Токен бота есть в URL запроса — не даём ему попасть в логи."""
    text = str(value)
    return text.replace(TOKEN, "***") if TOKEN else text


def _apply_text_format(params: dict, text_key: str, entities, parse_mode: str) -> None:
    """entities is None -> старое поведение (parse_mode), иначе текст 1 в 1 по entities."""
    if entities is None:
        params["parse_mode"] = parse_mode
    elif entities:
        params[text_key] = entities


def send_message_with_emoji_buttons(
    chat_id: int,
    text: str,
    reply_markup_rows: list[list[dict]],
    *,
    parse_mode: str = "HTML",
    message_thread_id: int | None = None,
    entities: list | None = None,
) -> dict | None:
    """Отправить сообщение с кнопками, поддерживающими premium emoji.

    entities=None — текст размечается через parse_mode (как раньше);
    entities=[...] — текст уходит без parse_mode с готовыми entities (1 в 1).
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
        "reply_markup": {"inline_keyboard": inline_keyboard},
    }
    _apply_text_format(params, "entities", entities, parse_mode)
    if message_thread_id:
        params["message_thread_id"] = message_thread_id

    try:
        resp = _SESSION.post(f"{_API}/sendMessage", json=params, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            log.error("[premium_emoji] sendMessage failed: %s", data)
        return data
    except Exception as exc:
        log.error("[premium_emoji] sendMessage exception: %s", _scrub(exc))
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
        log.error("[premium_emoji] sendPhoto exception: %s", _scrub(exc))
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
    entities: list | None = None,
) -> dict | None:
    """Отправить медиа (video/animation/document/audio) с emoji-кнопками.

    entities — caption_entities подписи (см. send_message_with_emoji_buttons).
    """
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
        "reply_markup": {"inline_keyboard": inline_keyboard},
    }
    if caption:
        params["caption"] = caption
        _apply_text_format(params, "caption_entities", entities, parse_mode)
    if message_thread_id:
        params["message_thread_id"] = message_thread_id

    try:
        resp = _SESSION.post(f"{_API}/{method}", json=params, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            log.error("[premium_emoji] %s failed: %s", method, data)
        return data
    except Exception as exc:
        log.error("[premium_emoji] %s exception: %s", method, _scrub(exc))
        return None


def has_emoji_buttons(rows: list[list[dict]]) -> bool:
    """Проверить, есть ли хотя бы одна кнопка с custom_emoji_id."""
    for row in (rows or []):
        for b in (row or []):
            if b.get("custom_emoji_id"):
                return True
    return False

# updated 2026-09-18
