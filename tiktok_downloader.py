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

import requests
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


CDN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

_YDL_COMMON = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    "socket_timeout": 20,
    "retries": 3,
    "http_headers": CDN_HEADERS,
}


def _extract_candidates(url: str) -> list[dict]:
    """Ask TikTok for format info exactly once and return usable MP4 candidates.

    The previous implementation called yt-dlp's extract_info twice (once to
    list formats, once again to actually download), which meant TikTok's
    slow anti-scraping page was scraped twice per request. Format entries
    already carry a direct CDN url, so a single extraction is enough for
    both URL-passthrough sending and the requests-based fallback download.
    """
    if yt_dlp is None:
        raise RuntimeError("yt-dlp не установлен")

    with yt_dlp.YoutubeDL(_YDL_COMMON) as ydl:
        info = ydl.extract_info(url, download=False)

    candidates = _format_candidates(info)
    if not candidates:
        raise RuntimeError("TikTok не отдал подходящий MP4 с видео и звуком")
    return candidates[:6]


def _download_direct(fmt: dict, workdir: str) -> str:
    """Stream a format's direct CDN url to disk via requests (no yt-dlp)."""
    direct_url = fmt.get("url")
    if not direct_url:
        raise RuntimeError("У формата нет прямой ссылки")

    path = os.path.join(workdir, "video.mp4")
    headers = {**CDN_HEADERS, **(fmt.get("http_headers") or {})}

    with requests.get(direct_url, headers=headers, stream=True, timeout=20) as resp:
        resp.raise_for_status()
        declared = resp.headers.get("Content-Length")
        if declared and int(declared) > MAX_UPLOAD_BYTES:
            raise RuntimeError("Видео больше лимита Telegram")

        written = 0
        with open(path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise RuntimeError("Видео больше лимита Telegram")
                fh.write(chunk)

    if written <= 0:
        raise RuntimeError("TikTok вернул пустой файл")
    return path


def _safe_delete(chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def _remember_from_sent(url: str, sent) -> None:
    file_id = getattr(getattr(sent, "video", None), "file_id", None)
    if file_id:
        _remember_file_id(url, file_id)


def _try_url_passthrough(message, url: str, fmt: dict):
    """Hand the direct CDN url straight to Telegram so it fetches the file
    itself. This is the fast path (near-instant when it works) and avoids
    our server downloading + re-uploading the video at all."""
    direct_url = fmt.get("url")
    if not direct_url:
        return None
    return bot.send_video(
        message.chat.id,
        direct_url,
        reply_to_message_id=message.message_id,
        supports_streaming=True,
        timeout=30,
    )


def _try_direct_download(message, url: str, fmt: dict, workdir: str):
    """Fallback: stream the CDN url to disk ourselves, then upload bytes."""
    path = _download_direct(fmt, workdir)
    with open(path, "rb") as video:
        return bot.send_video(
            message.chat.id,
            video,
            reply_to_message_id=message.message_id,
            supports_streaming=True,
            timeout=180,
        )


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

        candidates = _extract_candidates(url)
        workdir = tempfile.mkdtemp(prefix="liza-tiktok-")

        last_error = None
        for fmt in candidates:
            # Fast path: let Telegram fetch the CDN url directly.
            try:
                sent = _try_url_passthrough(message, url, fmt)
                if sent is not None:
                    _remember_from_sent(url, sent)
                    _safe_delete(chat_id, status_message_id)
                    return
            except Exception as exc:
                last_error = exc
                log.info("[tiktok] URL passthrough failed for %s: %s", url, exc)

            # Fallback: download the same format ourselves, then upload bytes.
            try:
                sent = _try_direct_download(message, url, fmt, workdir)
                _remember_from_sent(url, sent)
                _safe_delete(chat_id, status_message_id)
                return
            except Exception as exc:
                last_error = exc
                log.info("[tiktok] direct download failed for %s: %s", url, exc)
                for name in os.listdir(workdir):
                    try:
                        os.remove(os.path.join(workdir, name))
                    except OSError:
                        pass

        raise RuntimeError(f"не удалось отправить подходящий MP4: {last_error}")
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
