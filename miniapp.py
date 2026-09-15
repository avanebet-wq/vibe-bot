# -*- coding: utf-8 -*-
"""Легковесный HTTP-сервер для Mini App настройки кнопок Лизы."""
import http.server
import json
import logging
import urllib.parse
import threading
from urllib.parse import parse_qs, urlparse

from runtime import BOT_USERNAME
from reliability import stopped
import settings_store as store

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
            # Самые популярные и ходовые иконки для кнопок
            emojis_data = [
                {"id": "fire", "symbol": "🔥", "name": "Огонь"},
                {"id": "lightning", "symbol": "⚡️", "name": "Молния"},
                {"id": "star", "symbol": "⭐", "name": "Звезда"},
                {"id": "rocket", "symbol": "🚀", "name": "Ракета"},
                {"id": "gem", "symbol": "💎", "name": "Кристалл"},
                {"id": "heart", "symbol": "❤️‍🔥", "name": "Сердце"},
                {"id": "money", "symbol": "💸", "name": "Деньги"},
                {"id": "pin", "symbol": "📌", "name": "Пин"},
                {"id": "check", "symbol": "✅", "name": "Галочка"},
                {"id": "bell", "symbol": "🔔", "name": "Колокольчик"},
                {"id": "chat", "symbol": "💬", "name": "Чат"},
                {"id": "link", "symbol": "🔗", "name": "Ссылка"},
                {"id": "gift", "symbol": "🎁", "name": "Подарок"},
                {"id": "trophy", "symbol": "🏆", "name": "Кубок"},
                {"id": "target", "symbol": "🎯", "name": "Цель"},
                {"id": "bulb", "symbol": "💡", "name": "Идея"},
                {"id": "note", "symbol": "📝", "name": "Заметка"},
                {"id": "question", "symbol": "⁉️", "name": "Вопрос"}
            ]
            return self._json(200, emojis_data)

        if path.startswith("/api/buttons/"):
            parts = path.split("/")
            if len(parts) >= 4:
                identifier = parts[3]
                gid, pid = self._parse_identifier(identifier)
                if gid and pid:
                    post = store.get_post(gid, pid)
                    buttons = post.get("buttons", []) if post else []
                    return self._json(200, {"buttons": buttons})
            return self._json(400, {"error": "Invalid parameters"})

        self._html(200, _get_miniapp_html())

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

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
                        return self._json(400, {"error": str(e)})
            return self._json(400, {"error": "Invalid parameters"})

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
    return """<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Конструктор кнопок - Лиза</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * { box-sizing: border-box; }
        body { background: #0f0f0f; color: #fff; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 16px; overflow-x: hidden; }
        h2 { font-size: 18px; margin-bottom: 12px; }
        .row-box { background: #1a1a1a; border: 1px solid #333; border-radius: 12px; padding: 12px; margin-bottom: 12px; width: 100%; overflow: hidden; }
        .btn-item { background: #262626; border: 1px solid #444; border-radius: 8px; padding: 10px; margin-bottom: 8px; width: 100%; }
        .btn-content { display: flex; gap: 8px; align-items: flex-start; width: 100%; }
        .input-group { flex: 1; min-width: 0; }
        input[type="text"] { background: #121212; border: 1px solid #333; color: #fff; border-radius: 8px; padding: 8px; width: 100%; max-width: 100%; font-size: 14px; display: block; }
        button { cursor: pointer; border: none; border-radius: 8px; padding: 10px 16px; font-weight: 600; font-size: 14px; }
        .btn-primary { background: #3b82f6; color: #fff; width: 100%; margin-top: 10px; }
        .btn-success { background: #10b981; color: #fff; width: 100%; margin-top: 20px; padding: 14px; font-size: 16px; }
        .btn-danger { background: #ef4444; color: #fff; padding: 6px 10px; font-size: 12px; white-space: nowrap; }
        .emoji-scroll { display: flex; gap: 6px; overflow-x: auto; white-space: nowrap; padding-bottom: 6px; width: 100%; -webkit-overflow-scrolling: touch; }
        .emoji-scroll::-webkit-scrollbar { height: 4px; }
        .emoji-scroll::-webkit-scrollbar-thumb { background: #444; border-radius: 2px; }
        .emoji-btn { background: #2a2a2a; border: none; border-radius: 6px; min-width: 36px; height: 36px; font-size: 16px; display: flex; align-items: center; justify-content: center; cursor: pointer; flex-shrink: 0; }
        .emoji-btn:hover { background: #3b82f6; }
    </style>
</head>
<body>
    <h2>Конструктор кнопок</h2>
    <div id="editor-container"></div>
    <button class="btn-primary" onclick="addRow()">+ Додати рядок</button>
    <button class="btn-success" onclick="saveButtons()">Зберегти</button>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        let rows = [];
        let emojisList = [];

        let startAppParam = "";
        const segs = window.location.pathname.split("/");
        startAppParam = segs[segs.length - 1];
        if (!startAppParam || startAppParam.startsWith("api")) {
            startAppParam = tg.initDataUnsafe?.start_param || "";
        }

        fetch('/api/emojis')
            .then(res => res.json())
            .then(data => { emojisList = data; loadButtons(); });

        function loadButtons() {
            if (!startAppParam) return;
            fetch('/api/buttons/' + startAppParam)
                .then(res => res.json())
                .then(data => {
                    rows = data.buttons || [];
                    render();
                });
        }

        function render() {
            const container = document.getElementById('editor-container');
            container.innerHTML = '';
            
            rows.forEach((row, rIdx) => {
                const rowDiv = document.createElement('div');
                rowDiv.className = 'row-box';
                rowDiv.innerHTML = `<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; font-size:12px; color:#aaa;"><span>Рядок ${rIdx + 1}</span><button class="btn-danger" onclick="deleteRow(${rIdx})">Видалити рядок</button></div>`;
                
                row.forEach((btn, bIdx) => {
                    const btnDiv = document.createElement('div');
                    btnDiv.className = 'btn-item';
                    btnDiv.innerHTML = `
                        <div class="btn-content">
                            <div class="input-group">
                                <div style="margin-bottom:6px; font-size:12px; color:#888;">Виберіть емодзи:</div>
                                <div class="emoji-scroll" id="emojis-${rIdx}-${bIdx}"></div>
                                <input type="text" placeholder="Назва кнопки" value="${escapeHtml(btn.text || '')}" id="text-${rIdx}-${bIdx}" style="margin-top:6px;">
                                <input type="text" placeholder="Посилання (https://...)" value="${escapeHtml(btn.url || btn.popup || '')}" id="url-${rIdx}-${bIdx}" style="margin-top:6px;">
                            </div>
                            <button class="btn-danger" onclick="deleteBtn(${rIdx}, ${bIdx})" style="margin-top:22px;">✕</button>
                        </div>
                    `;
                    rowDiv.appendChild(btnDiv);

                    const scrollContainer = btnDiv.querySelector(`#emojis-${rIdx}-${bIdx}`);
                    emojisList.forEach(em => {
                        const eb = document.createElement('button');
                        eb.className = 'emoji-btn';
                        eb.innerText = em.symbol;
                        eb.onclick = () => {
                            const input = document.getElementById(`text-${rIdx}-${bIdx}`);
                            let cleanText = input.value.replace(/^(\p{Emoji}|\u200d)+/gu, "").trim();
                            input.value = em.symbol + " " + cleanText;
                        };
                        scrollContainer.appendChild(eb);
                    });
                });

                const addBtn = document.createElement('button');
                addBtn.className = 'btn-primary';
                addBtn.style = "background: #262626; font-size:12px; padding:8px; margin-top:4px;";
                addBtn.innerText = "+ Додати кнопку в рядок";
                addBtn.onclick = () => { row.push({text: "Кнопка", url: "https://t.me"}); render(); };
                rowDiv.appendChild(addBtn);

                container.appendChild(rowDiv);
            });
        }

        function addRow() {
            rows.push([{text: "Кнопка", url: "https://t.me"}]);
            render();
        }

        function deleteRow(rIdx) {
            rows.splice(rIdx, 1);
            render();
        }

        function deleteBtn(rIdx, bIdx) {
            rows[rIdx].splice(bIdx, 1);
            if (rows[rIdx].length === 0) rows.splice(rIdx, 1);
            render();
        }

        function saveButtons() {
            rows.forEach((row, rIdx) => {
                row.forEach((btn, bIdx) => {
                    const txt = document.getElementById(`text-${rIdx}-${bIdx}`).value;
                    const val = document.getElementById(`url-${rIdx}-${bIdx}`).value;
                    btn.text = txt;
                    if (val.startsWith("http")) {
                        btn.url = val;
                        delete btn.popup;
                    } else {
                        btn.popup = val;
                        delete btn.url;
                    }
                });
            });

            fetch('/api/buttons/' + startAppParam, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ buttons: rows })
            }).then(res => res.json()).then(data => {
                if (data.status === 'ok') {
                    tg.close();
                } else {
                    alert('Помилка збереження');
                }
            });
        }

        function escapeHtml(text) {
            return text.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        }
    </script>
</body>
</html>"""
