# -*- coding: utf-8 -*-
"""Легковесный HTTP-сервер для Mini App настройки кнопок Лизы."""
import http.server
import json
import logging
import urllib.parse
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from runtime import BOT_USERNAME
from reliability import stopped
import settings_store as store
import premium_emoji as pe

log = logging.getLogger("miniapp")

PORT = 8080


class MiniAppHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        log.debug("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), format % args)

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Telegram-Init-Data")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status, html_content):
        body = html_content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Telegram-Init-Data")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/emojis":
            qs = parse_qs(parsed.query)
            query = (qs.get("q") or [""])[0]
            group_id = (qs.get("group_id") or [""])[0]
            emojis_data = pe.get_emojis(group_id=group_id, query=query)
            return self._json(200, emojis_data)

        if path == "/api/emojis/rename":
            # GET /api/emojis/rename?emoji_id=...&name=...&group_id=...
            qs = parse_qs(parsed.query)
            emoji_id = (qs.get("emoji_id") or [""])[0]
            name = (qs.get("name") or [""])[0]
            group_id = (qs.get("group_id") or [""])[0]
            if emoji_id and name:
                existing = pe.get_emojis(group_id=group_id)
                preview = next((e["previewUrl"] for e in existing if e["id"] == emoji_id), None)
                pe.save_emoji(emoji_id, name, preview, group_id=group_id)
                return self._json(200, {"ok": True})
            return self._json(400, {"error": "emoji_id and name required"})

        if path == "/api/emojis/delete":
            qs = parse_qs(parsed.query)
            emoji_id = (qs.get("emoji_id") or [""])[0]
            group_id = (qs.get("group_id") or [""])[0]
            if emoji_id:
                pe.delete_emoji(emoji_id, group_id=group_id)
                return self._json(200, {"ok": True})
            return self._json(400, {"error": "emoji_id required"})

        # Новый API конструктора: настройки строго привязаны к chat_id + post_id.
        if path == "/api/context":
            qs = parse_qs(parsed.query)
            gid = (qs.get("chat_id") or [""])[0]
            pid = (qs.get("post_id") or [""])[0]
            if not gid or not pid:
                return self._json(400, {"error": "chat_id и post_id обязательны"})
            try:
                gid_i = int(gid)
            except (TypeError, ValueError):
                return self._json(400, {"error": "Некорректный chat_id"})
            post = store.get_post(gid_i, pid)
            if post is None:
                return self._json(404, {"error": "Публикация не найдена"})
            return self._json(200, {"rows": post.get("buttons") or []})

        if path.startswith("/api/buttons/"):
            parts = path.split("/")
            if len(parts) >= 4:
                identifier = parts[3]
                gid, pid = self._parse_identifier(identifier)
                if gid and pid:
                    post = store.get_post(gid, pid)
                    buttons = post.get("buttons", []) if post else []
                    return self._json(200, {"buttons": buttons})
            return self._json(400, {"error": "Некорректные параметры"})

        self._html(200, _get_miniapp_html())

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Новый API сохранения конструктора.
        if path == "/api/save-buttons":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                gid = int(data.get("chat_id"))
                pid = str(data.get("post_id"))
                rows = data.get("rows")
                if not isinstance(rows, list):
                    raise ValueError("Поле rows должно быть массивом")
                post = store.get_post(gid, pid)
                if post is None:
                    return self._json(404, {"error": "Публикация не найдена"})
                # Нормализуем данные Mini App в формат, который использует settings_core.
                normalized = []
                for row in rows:
                    if not isinstance(row, list):
                        raise ValueError("Каждая строка кнопок должна быть массивом")
                    out_row = []
                    for btn in row:
                        if not isinstance(btn, dict):
                            raise ValueError("Некорректная кнопка")
                        name = str(btn.get("name") or "").strip()
                        if not name:
                            raise ValueError("Укажите название каждой кнопки")
                        typ = str(btn.get("type") or "url")
                        value = str(btn.get("value") or "")
                        out = {"text": name}
                        if typ == "url":
                            value = value.strip()
                            parsed_url = urlparse(value)
                            if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                                raise ValueError(f"Некорректная ссылка: «{value}»")
                            out["url"] = value
                        elif typ in ("popup", "alert", "share", "copy", "rules", "user_command"):
                            out[typ] = value
                        elif typ == "delete_message":
                            out["delete_message"] = True
                        else:
                            raise ValueError(f"Неизвестный тип кнопки: {typ}")
                        if btn.get("custom_emoji_id"):
                            out["custom_emoji_id"] = btn["custom_emoji_id"]
                        out_row.append(out)
                    normalized.append(out_row)
                store.update_post(gid, pid, buttons=normalized)
                return self._json(200, {"ok": True})
            except (TypeError, ValueError, json.JSONDecodeError) as e:
                return self._json(400, {"error": str(e) or "Некорректные данные"})
            except Exception as e:
                log.exception("[MiniApp] Ошибка сохранения кнопок")
                return self._json(500, {"error": "Внутренняя ошибка сервера"})

        if path.startswith("/api/buttons/"):
            parts = path.split("/")
            if len(parts) >= 4:
                identifier = parts[3]
                gid, pid = self._parse_identifier(identifier)
                if gid and pid:
                    content_length = int(self.headers.get('Content-Length', 0))
                    body = self.rfile.read(content_length)
                    try:
                        data = json.loads(body.decode('utf-8'))
                        rows = data.get("buttons", [])
                        store.update_post(gid, pid, buttons=rows)
                        return self._json(200, {"status": "ok"})
                    except Exception as e:
                        log.exception("[MiniApp] Ошибка сохранения legacy API")
                        return self._json(400, {"error": str(e)})
            return self._json(400, {"error": "Некорректные параметры"})

        self._json(404, {"error": "Not found"})

    def _parse_identifier(self, identifier):
        identifier = identifier.split("_e")[0]
        if identifier.startswith("c") and "_p" in identifier:
            try:
                parts = identifier.split("_p")
                gid = int(parts[0][1:])
                pid = parts[1]
                return gid, pid
            except Exception:
                pass
        return None, None


class _BoundedHTTPServer(http.server.HTTPServer):
    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)


def run_miniapp_server():
    server_address = ("0.0.0.0", PORT)
    try:
        httpd = _BoundedHTTPServer(server_address, MiniAppHandler)
        log.info("[MiniApp] Сервер запущен на порту %s", PORT)
        while not stopped():
            httpd.handle_request()
        httpd.server_close()
    except Exception as e:
        log.error("[MiniApp] Ошибка запуска сервера: %s", e)


def start_miniapp_server():
    threading.Thread(target=run_miniapp_server, daemon=True, name="liza-miniapp").start()


def _get_miniapp_html():
    """Отдать актуальный русскоязычный интерфейс Mini App из miniapp_index.html."""
    html_path = Path(__file__).with_name("miniapp_index.html")
    try:
        return html_path.read_text(encoding="utf-8")
    except Exception:
        log.exception("[MiniApp] Не удалось загрузить miniapp_index.html")
        return "<!doctype html><html lang='ru'><body>Ошибка загрузки конструктора.</body></html>"

