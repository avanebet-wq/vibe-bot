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
from settings_core import _is_valid_button_url

log = logging.getLogger("miniapp")

PORT = 8080

# Типы кнопок, которые понимает редактор Mini App (см. BUTTON_TYPES в miniapp_index.html).
# Порядок важен: он определяет приоритет при распознавании типа хранимой кнопки.
_UI_BUTTON_TYPES = ("url", "popup", "alert", "share", "copy", "rules", "user_command", "delete_message")
_UI_STYLES = ("transparent", "blue", "green", "red")


def _stored_button_to_ui(btn):
    """Преобразовать кнопку из формата хранения (settings_core/build_markup_from_buttons:
    {"text":.., "url"/"popup"/"alert"/"share"/"copy"/"rules"/"user_command":.., "delete_message": True})
    в формат, который ждёт редактор Mini App: {type, name, value}.

    Без этого преобразования /api/context отдавал сырые данные хранения, редактор
    получал btn.type/btn.name/btn.value == undefined и падал при рендере (пустой
    экран "добавить кнопку" при повторном открытии уже настроенных кнопок).
    """
    typ = "url"
    value = ""
    for candidate in _UI_BUTTON_TYPES:
        if candidate == "delete_message":
            if btn.get("delete_message"):
                typ = candidate
                value = ""
                break
            continue
        raw = btn.get(candidate)
        if raw is not None and raw is not False:
            typ = candidate
            # Легаси-формат текстовой команды мог хранить rules/True как булево —
            # тогда просто нет доп. значения, но тип определён верно.
            value = raw if isinstance(raw, str) else ""
            break
    style = btn.get("style")
    if style not in _UI_STYLES:
        style = "transparent"
    return {
        "type": typ,
        "name": btn.get("text") or "",
        "value": value,
        "style": style,
    }


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

        if path == "/api/emoji-packs":
            # Lightweight tab metadata only; emoji bodies are loaded per selected pack.
            qs = parse_qs(parsed.query)
            chat_id = (qs.get("chat_id") or [""])[0] or None
            return self._json(200, pe.get_pack_list(chat_id=chat_id))

        if path.startswith("/api/emoji-packs/") and path.endswith("/emojis"):
            try:
                pack_id = int(path.split("/")[3])
            except (TypeError, ValueError, IndexError):
                return self._json(400, {"error": "Некорректный pack_id"})
            return self._json(200, pe.get_pack_emojis(pack_id))

        if path == "/api/emojis":
            # Search always spans the entire global library. With no query this
            # remains a compatibility flat endpoint for older Mini App clients.
            qs = parse_qs(parsed.query)
            query = (qs.get("q") or [""])[0]
            return self._json(200, pe.get_emojis(query=query))

        if path == "/api/stickerImage":
            # Аналог подхода GroupHelpBot: браузер получает картинку через
            # backend-прокси, а не напрямую с Telegram file API. Это важно,
            # потому что прямой URL содержит bot token и может быть
            # недоступен/нестабилен из Mini App.
            qs = parse_qs(parsed.query)
            emoji_id = (qs.get("emoji_id") or [""])[0]
            if not emoji_id:
                return self._json(400, {"error": "emoji_id required"})
            result = pe.fetch_preview_bytes(emoji_id)
            if not result:
                return self._json(404, {"error": "Preview not found"})
            content_type, payload = result
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=600")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/api/emojis/rename":
            # Legacy compatibility endpoint. The new picker does not rename emojis.
            qs = parse_qs(parsed.query)
            emoji_id = (qs.get("emoji_id") or [""])[0]
            name = (qs.get("name") or [""])[0]
            if emoji_id and name:
                with pe.db_lock:
                    pe.conn.execute("UPDATE premium_emojis SET name=? WHERE emoji_id=?", (name, emoji_id))
                    pe.conn.commit()
                return self._json(200, {"ok": True})
            return self._json(400, {"error": "emoji_id and name required"})

        if path == "/api/emojis/delete":
            qs = parse_qs(parsed.query)
            emoji_id = (qs.get("emoji_id") or [""])[0]
            if emoji_id:
                pe.delete_emoji(emoji_id)
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

            # Старые сохранённые кнопки могли содержать только custom_emoji_id.
            # Для них тоже строим стабильный URL нашего preview-прокси.
            rows = []
            for row in (post.get("buttons") or []):
                out_row = []
                for btn in (row or []):
                    if not isinstance(btn, dict):
                        continue
                    # Приводим формат хранения к формату редактора (type/name/value),
                    # иначе повторное открытие конструктора получает "пустые" кнопки.
                    out = _stored_button_to_ui(btn)
                    emoji_id = btn.get("custom_emoji_id")
                    if emoji_id:
                        out["custom_emoji_id"] = str(emoji_id)
                        # Всегда используем локальный прокси-превью. Старые
                        # Telegram file URLs могли протухнуть или содержать
                        # bot token и поэтому не отображались в Mini App.
                        out["custom_emoji_preview"] = pe.preview_proxy_url(str(emoji_id))
                    out_row.append(out)
                rows.append(out_row)
            return self._json(200, {"rows": rows})

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
                        style = str(btn.get("style") or "").strip()
                        if style in ("transparent", "blue", "green", "red"):
                            out["style"] = style
                        if typ == "url":
                            value = value.strip()
                            if not _is_valid_button_url(value):
                                raise ValueError(
                                    f"Некорректная ссылка: «{value}». "
                                    "Нужен настоящий домен, например https://example.com"
                                )
                            out["url"] = value
                        elif typ in ("popup", "alert", "share", "copy", "rules", "user_command"):
                            out[typ] = value
                        elif typ == "delete_message":
                            out["delete_message"] = True
                        else:
                            raise ValueError(f"Неизвестный тип кнопки: {typ}")
                        if btn.get("custom_emoji_id"):
                            emoji_id = str(btn["custom_emoji_id"])
                            out["custom_emoji_id"] = emoji_id
                            # Храним preview вместе с конфигурацией публикации.
                            # Это позволяет Mini App показывать тот же emoji после
                            # повторного открытия, не полагаясь только на текущий UI.
                            out["custom_emoji_preview"] = pe.preview_proxy_url(emoji_id)
                        out_row.append(out)
                    normalized.append(out_row)
                store.update_post(gid, pid, buttons=normalized)
                return self._json(200, {"ok": True})
            except (TypeError, ValueError, json.JSONDecodeError) as e:
                return self._json(400, {"error": str(e) or "Некорректные данные"})
            except Exception as e:
                log.exception("[MiniApp] Ошибка сохранения кнопок")
                return self._json(500, {"error": "Внутренняя ошибка сервера"})

        if path == "/api/group-pack/add":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                chat_id = str(data.get("chat_id") or "").strip()
                link = str(data.get("link") or "").strip()
                added_by = str(data.get("user_id") or "").strip() or None
                if not chat_id or not link:
                    return self._json(400, {"error": "chat_id и link обязательны"})
                set_name = pe.extract_set_name_from_link(link)
                if not set_name:
                    return self._json(400, {"error": "Не удалось определить пак. Вставьте ссылку вида t.me/addstickers/ИМЯ"})
                result = pe.add_group_pack(chat_id, set_name, added_by=added_by)
                if not result.get("ok"):
                    return self._json(400, {"error": result.get("error", "Не удалось добавить пак")})
                return self._json(200, {"ok": True, "already": result.get("already", False), "packId": result.get("pack_id")})
            except Exception:
                log.exception("[MiniApp] Ошибка добавления группового пака")
                return self._json(500, {"error": "Внутренняя ошибка"})

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


# updated 2026-09-18
