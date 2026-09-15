# -*- coding: utf-8 -*-
"""Надёжная загрузка TikTok-ссылок и отправка видео в Telegram."""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

from telebot.apihelper import ApiTelegramException

from runtime import bot

try:
    import yt_dlp
except Exception:  # pragma: no cover - dependency is installed in production
    yt_dlp = None

log = logging.getLogger("tiktok")

# Telegram Bot API currently accepts uploaded videos up to 50 MB.
# Keep a safety margin so a file is not rejected because of metadata/rounding.
MAX_UPLOAD_BYTES = 49 * 1024 * 1024
CACHE_TTL = 6 * 60 * 60

TIKTOK_RE = re.compile(
    r"https?://(?:www\.|m\.|vm\.|vt\.)?tiktok\.com/[^\s<>]+",
    re.IGNORECASE,
)

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="liza-tiktok")
_CACHE_LOCK = threading.RLock()
_URL_CACHE: dict[str, tuple[float, str]] = {}
_INFLIGHT: set[str] = set()


def extract_tiktok_url(text: str | None) -> str | None:
    """Return the first TikTok URL from a text message."""
    if not text:
        return None
    match = TIKTOK_RE.search(text)
    if not match:
        return None
    raw = match.group(0).rstrip(".,!?;:)]}>\"'")
    return normalize_tiktok_url(raw)


def normalize_tiktok_url(url: str) -> str:
    """Normalize only harmless URL noise; keep TikTok query parameters intact."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.netloc.lower()
    return urlunsplit((scheme, host, parts.path, parts.query, ""))


def _cached_file_id(url: str) -> str | None:
    now = time.time()
    with _CACHE_LOCK:
        item = _URL_CACHE.get(url)
        if not item:
            return None
        expires_at, file_id = item
        if expires_at <= now:
            _URL_CACHE.pop(url, None)
            return None
        return file_id


def _remember_file_id(url: str, file_id: str) -> None:
    with _CACHE_LOCK:
        _URL_CACHE[url] = (time.time() + CACHE_TTL, file_id)
        # Small bounded cleanup so a busy group cannot grow this forever.
        if len(_URL_CACHE) > 256:
            oldest = sorted(_URL_CACHE.items(), key=lambda item: item[1][0])[:64]
            for key, _ in oldest:
                _URL_CACHE.pop(key, None)


def _claim_url(url: str) -> bool:
    with _CACHE_LOCK:
        if url in _INFLIGHT:
            return False
        _INFLIGHT.add(url)
        return True


def _release_url(url: str) -> None:
    with _CACHE_LOCK:
        _INFLIGHT.discard(url)


def _format_candidates(info: dict) -> list[dict]:
    formats = []
    for fmt in info.get("formats") or []:
        if not fmt.get("url"):
            continue
        if fmt.get("vcodec") in (None, "none"):
            continue
        if fmt.get("acodec") in (None, "none"):
            continue
        ext = (fmt.get("ext") or "").lower()
        if ext != "mp4":
            continue
        formats.append(fmt)

    def size_key(fmt: dict) -> tuple[int, int, float]:
        size = fmt.get("filesize") or fmt.get("filesize_approx") or 10**18
        height = fmt.get("height") or 0
        tbr = fmt.get("tbr") or 0.0
        return (int(size), int(height), float(tbr))

    # Prefer the highest resolution that is known to fit. Among equally suitable
    # files, prefer higher resolution/bitrate. Never select an obviously oversized
    # known file merely because it has better quality.
    fitting = [f for f in formats if (f.get("filesize") or f.get("filesize_approx")) and (f.get("filesize") or f.get("filesize_approx")) <= MAX_UPLOAD_BYTES]
    if fitting:
        return sorted(
            fitting,
            key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
            reverse=True,
        )

    # If TikTok does not expose a size, use the smallest progressive MP4 first.
    # The final on-disk size check below remains authoritative.
    return sorted(formats, key=size_key)


def _download(url: str) -> tuple[str, dict]:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp не установлен")

    workdir = tempfile.mkdtemp(prefix="liza-tiktok-")
    output_template = os.path.join(workdir, "video.%(ext)s")

    common = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 2,
        "concurrent_fragment_downloads": 2,
        "outtmpl": output_template,
        "restrictfilenames": True,
        "overwrites": True,
        "nocheckcertificate": False,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    try:
        # First pass only inspects formats. This lets us deliberately choose a
        # progressive MP4 that Telegram can accept without an ffmpeg merge.
        with yt_dlp.YoutubeDL({**common, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)

        candidates = _format_candidates(info)
        if not candidates:
            raise RuntimeError("TikTok не отдал подходящий MP4 с видео и звуком")

        last_error = None
        for fmt in candidates[:6]:
            fmt_id = fmt.get("format_id")
            if not fmt_id:
                continue
            try:
                opts = {**common, "format": str(fmt_id)}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    downloaded = ydl.extract_info(url, download=True)
                    prepared = ydl.prepare_filename(downloaded)

                path = prepared
                if not os.path.isfile(path):
                    files = [os.path.join(workdir, name) for name in os.listdir(workdir)]
                    files = [p for p in files if os.path.isfile(p)]
                    if not files:
                        raise RuntimeError("Файл после загрузки не найден")
                    path = max(files, key=os.path.getsize)

                size = os.path.getsize(path)
                if size <= 0:
                    raise RuntimeError("TikTok вернул пустой файл")
                if size > MAX_UPLOAD_BYTES:
                    raise RuntimeError("Видео больше лимита Telegram")

                return path, downloaded
            except Exception as exc:
                last_error = exc
                # Remove the failed candidate before trying a lower-quality one.
                for name in os.listdir(workdir):
                    try:
                        os.remove(os.path.join(workdir, name))
                    except OSError:
                        pass

        raise RuntimeError(f"не удалось скачать подходящий MP4: {last_error}")
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def _safe_delete(chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def _process(message, url: str, status_message_id: int | None) -> None:
    chat_id = message.chat.id
    workdir = None
    try:
        cached = _cached_file_id(url)
        if cached:
            bot.send_video(
                chat_id,
                cached,
                reply_to_message_id=message.message_id,
                supports_streaming=True,
            )
            _safe_delete(chat_id, status_message_id)
            return

        path, info = _download(url)
        workdir = os.path.dirname(path)
        with open(path, "rb") as video:
            sent = bot.send_video(
                chat_id,
                video,
                reply_to_message_id=message.message_id,
                supports_streaming=True,
                timeout=180,
            )
        file_id = getattr(getattr(sent, "video", None), "file_id", None)
        if file_id:
            _remember_file_id(url, file_id)
        _safe_delete(chat_id, status_message_id)
    except ApiTelegramException as exc:
        log.warning("[tiktok] Telegram upload failed for %s: %s", url, exc)
        _safe_delete(chat_id, status_message_id)
        try:
            bot.send_message(chat_id, "⚠️ Не смогла отправить это видео в Telegram. Попробуй другую ссылку.", reply_to_message_id=message.message_id)
        except Exception:
            pass
    except Exception as exc:
        log.warning("[tiktok] processing failed for %s: %s", url, exc, exc_info=True)
        _safe_delete(chat_id, status_message_id)
        try:
            bot.send_message(chat_id, "⚠️ Не удалось получить видео по этой TikTok-ссылке.", reply_to_message_id=message.message_id)
        except Exception:
            pass
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
        _release_url(url)


def handle_message(message) -> bool:
    """Schedule TikTok processing. Returns True only when a TikTok URL was found."""
    if getattr(message.chat, "type", "") not in ("group", "supergroup"):
        return False

    url = extract_tiktok_url(getattr(message, "text", None))
    if not url:
        return False

    cached = _cached_file_id(url)
    if cached:
        try:
            bot.send_video(
                message.chat.id,
                cached,
                reply_to_message_id=message.message_id,
                supports_streaming=True,
            )
            return True
        except Exception:
            with _CACHE_LOCK:
                _URL_CACHE.pop(url, None)

    if not _claim_url(url):
        return True

    status_id = None
    try:
        status = bot.reply_to(message, "⏳ Забираю видео из TikTok…")
        status_id = getattr(status, "message_id", None)
    except Exception:
        pass

    _EXECUTOR.submit(_process, message, url, status_id)
    return True
