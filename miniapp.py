# -*- coding: utf-8 -*-
"""HTTP server for Лиза's Telegram Mini App button constructor.

The bot itself remains on pyTelegramBotAPI. This module uses only the
Python standard library and the already-created runtime.bot instance.
"""
import hashlib
from security import allow
from reliability import health
import hmac
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlparse

from config import TOKEN
from runtime import bot
import settings_store as store
from admin_api import dashboard

log = logging.getLogger("miniapp")

BASE_DIR = Path(__file__).resolve().parent
INDEX_PATH = BASE_DIR / "miniapp_index.html"
MAX_INIT_DATA_AGE = 3600
MAX_BODY = 256 * 1024
ALLOWED_TYPES = {
    "url", "popup", "alert", "share", "copy", "rules",
    "user_command", "delete_message",
}


def validate_init_data(init_data: str) -> dict:
    """Validate Telegram WebApp initData and return its parsed fields."""
    if not init_data:
        raise ValueError("missing Telegram initData")

    pairs = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise ValueError("missing hash")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except (TypeError, ValueError):
        raise ValueError("invalid auth_date")

    if auth_date <= 0 or abs(time.time() - auth_date) > MAX_INIT_DATA_AGE:
        raise ValueError("initData expired")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(pairs.items())
    )
    secret_key = hmac.new(
        b"WebAppData", TOKEN.encode("utf-8"), hashlib.sha256
    ).digest()
    calculated = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise ValueError("invalid initData hash")

    user_raw = pairs.get("user")
    if not user_raw:
        raise ValueError("Telegram user is missing")
    try:
        pairs["user"] = json.loads(user_raw)
    except json.JSONDecodeError:
        raise ValueError("invalid Telegram user data")

    return pairs


def _auth_user(handler):
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        raise PermissionError("missing Telegram authorization")
    return validate_init_data(auth[4:])["user"]


def _require_admin(user_id: int, chat_id: int):
    try:
        member = bot.get_chat_member(chat_id, user_id)
    except Exception as exc:
        raise PermissionError("cannot verify chat administrator") from exc
    if member.status not in ("administrator", "creator"):
        raise PermissionError("user is not an administrator of this chat")


def _normalize_url(value: str) -> str:
    value = value.strip()
    if value.startswith(("http://", "https://", "tg://")):
        return value
    if value.startswith("t.me/"):
        return "https://" + value
    if value.startswith("@"):
        return "https://t.me/" + value[1:]
    return value


def _to_public_rows(rows):
    """Convert the bot's stored button format to Mini App format."""
    result = []
    for row in rows or []:
        out_row = []
        for b in row or []:
            if not isinstance(b, dict):
                continue
            if b.get("url") is not None:
                typ, value = "url", b.get("url", "")
            elif b.get("popup") is not None:
                typ, value = "popup", b.get("popup", "")
            elif b.get("alert") is not None:
                typ, value = "alert", b.get("alert", "")
            elif b.get("share") is not None:
                typ, value = "share", b.get("share", "")
            elif b.get("copy") is not None:
                typ, value = "copy", b.get("copy", "")
            elif b.get("rules") is not None:
                typ, value = "rules", b.get("rules") if isinstance(b.get("rules"), str) else ""
            elif b.get("user_command") is not None:
                typ, value = "user_command", b.get("user_command", "")
            elif b.get("delete_message"):
                typ, value = "delete_message", ""
            else:
                continue

            out_row.append({
                "type": typ,
                "name": str(b.get("text", ""))[:30],
                "value": str(value or ""),
                "style": b.get("style", "transparent"),
                "custom_emoji_id": b.get("custom_emoji_id"),
                "custom_emoji_preview": b.get("custom_emoji_preview"),
            })
        result.append(out_row)
    return result


def _from_public_rows(rows):
    if not isinstance(rows, list):
        raise ValueError("rows must be an array")
    if len(rows) > 15:
        raise ValueError("maximum 15 rows")

    result = []
    for row in rows:
        if not isinstance(row, list):
            raise ValueError("each row must be an array")
        if len(row) > 4:
            raise ValueError("maximum 4 buttons per row")

        out_row = []
        for b in row:
            if not isinstance(b, dict):
                raise ValueError("invalid button")
            typ = str(b.get("type", "")).strip()
            name = str(b.get("name", "")).strip()[:30]
            value = str(b.get("value", "")).strip()
            style = str(b.get("style", "transparent")).strip()

            if typ not in ALLOWED_TYPES:
                raise ValueError("unsupported button type")
            if not name:
                raise ValueError("button name is required")
            if typ != "delete_message" and not value:
                raise ValueError("button value is required")

            item = {
                "text": name,
                "style": style if style in {"transparent", "blue", "green", "red"} else "transparent",
            }

            if typ == "url":
                item["url"] = _normalize_url(value)
            elif typ == "popup":
                item["popup"] = value
            elif typ == "alert":
                item["alert"] = value
            elif typ == "share":
                item["share"] = value
            elif typ == "copy":
                item["copy"] = value
            elif typ == "rules":
                item["rules"] = value
            elif typ == "user_command":
                item["user_command"] = value
            elif typ == "delete_message":
                item["delete_message"] = True

            # Telegram inline buttons cannot render a custom emoji entity in
            # their text, but keep the metadata so the constructor does not
            # lose it on a later edit.
            emoji_id = b.get("custom_emoji_id")
            if emoji_id:
                item["custom_emoji_id"] = str(emoji_id)[:128]
            if b.get("custom_emoji_preview"):
                item["custom_emoji_preview"] = str(b["custom_emoji_preview"])[:2048]

            out_row.append(item)
        result.append(out_row)

    return result


def _post_from_request(user_id, chat_id, post_id):
    _require_admin(user_id, chat_id)
    post = store.get_post(chat_id, str(post_id))
    if not post:
        raise LookupError("publication not found")
    return post


class MiniAppHandler(BaseHTTPRequestHandler):
    server_version = "LizaMiniApp/1.0"

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _error(self, status, message):
        self._json(status, {"ok": False, "error": message})

    def _parse_ids(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        try:
            chat_id = int((qs.get("chat_id") or [""])[0])
        except ValueError:
            raise ValueError("invalid chat_id")
        post_id = str((qs.get("post_id") or [""])[0]).strip()
        if not chat_id or not post_id:
            raise ValueError("chat_id and post_id are required")
        return chat_id, post_id

    def do_GET(self):
        try:
            if not allow("miniapp:" + self.client_address[0], limit=60, window=60):
                return self._error(429, "rate limit")
            parsed = urlparse(self.path)

            if parsed.path == "/":
                return self._send(200, b"Liza is alive!", "text/plain; charset=utf-8")

            if parsed.path in ("/app", "/app/"):
                if not INDEX_PATH.exists():
                    return self._error(500, "Mini App file is missing")
                data = INDEX_PATH.read_bytes()
                return self._send(200, data, "text/html; charset=utf-8")

            if parsed.path == "/health":
                return self._json(200, {"ok": True, "service": "liza-miniapp", "health": health()})

            if parsed.path == "/api/dashboard":
                user = _auth_user(self)
                qs = parse_qs(parsed.query)
                try: chat_id = int((qs.get("chat_id") or [""])[0])
                except ValueError: raise ValueError("invalid chat_id")
                _require_admin(user["id"], chat_id)
                return self._json(200, dashboard(chat_id))

            if parsed.path == "/api/context":
                user = _auth_user(self)
                chat_id, post_id = self._parse_ids()
                post = _post_from_request(user["id"], chat_id, post_id)
                return self._json(200, {"rows": _to_public_rows(post.get("buttons"))})

            if parsed.path == "/api/emojis":
                # The ready-made UI supports a premium-emoji library. The
                # existing bot does not yet maintain one, so return an empty
                # authenticated library instead of pretending arbitrary
                # Telegram emoji IDs are valid.
                _auth_user(self)
                return self._json(200, [])

            return self._error(404, "not found")

        except PermissionError as exc:
            return self._error(401, str(exc))
        except LookupError as exc:
            return self._error(404, str(exc))
        except ValueError as exc:
            return self._error(400, str(exc))
        except Exception:
            log.exception("Mini App GET failed")
            return self._error(500, "internal server error")

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain; charset=utf-8")

    def do_POST(self):
        try:
            if not allow("miniapp:" + self.client_address[0], limit=30, window=60):
                return self._error(429, "rate limit")
            parsed = urlparse(self.path)
            if parsed.path != "/api/save-buttons":
                return self._error(404, "not found")

            user = _auth_user(self)
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                return self._error(400, "invalid request body")

            raw = self.rfile.read(length)
            body = json.loads(raw.decode("utf-8"))

            chat_id = int(body.get("chat_id"))
            post_id = str(body.get("post_id", "")).strip()
            if not chat_id or not post_id:
                return self._error(400, "chat_id and post_id are required")

            _post_from_request(user["id"], chat_id, post_id)
            buttons = _from_public_rows(body.get("rows", []))
            updated = store.update_post(chat_id, post_id, buttons=buttons)
            if updated is None:
                return self._error(404, "publication not found")

            return self._json(200, {"ok": True})

        except PermissionError as exc:
            return self._error(401, str(exc))
        except LookupError as exc:
            return self._error(404, str(exc))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._error(400, str(exc))
        except Exception:
            log.exception("Mini App POST failed")
            return self._error(500, "internal server error")

    def log_message(self, fmt, *args):
        return


def start_server():
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), MiniAppHandler)
    log.info("Mini App server started on 0.0.0.0:%s", port)
    server.serve_forever()


def start_miniapp_server():
    thread = threading.Thread(target=start_server, daemon=True, name="liza-miniapp")
    thread.start()
    return thread
