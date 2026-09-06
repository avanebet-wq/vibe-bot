# -*- coding: utf-8 -*-
import logging
import requests

from config import OPENROUTER_KEY, AI_MODEL, SYS_PROMPT_NORMAL, SYS_PROMPT_ANGRY

_key_list = [k.strip() for k in OPENROUTER_KEY.split(",") if k.strip()]
_key_idx = 0


def _current_key():
    if not _key_list:
        return None
    return _key_list[_key_idx % len(_key_list)]


def _switch_key():
    global _key_idx
    _key_idx += 1


def clean_response(text):
    text = (text or "").strip()
    # уберём случайные технические маркеры, если модель их добавит
    for bad in ("<think>", "</think>", "```"):
        text = text.replace(bad, "")
    return text.strip()


def ask_liza(user_text, angry=False, max_tokens=200):
    key = _current_key()
    if not key:
        return "🔌 AI сейчас недоступен — не настроен ключ OPENROUTER_KEY."

    sys_prompt = SYS_PROMPT_ANGRY if angry else SYS_PROMPT_NORMAL
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_text[:2000]},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    for _ in range(max(1, len(_key_list))):
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload, headers=headers, timeout=25,
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return clean_response(content)
            elif resp.status_code in (401, 402, 429):
                _switch_key()
                headers["Authorization"] = f"Bearer {_current_key()}"
                continue
            else:
                logging.error(f"[ai] status={resp.status_code} body={resp.text[:200]}")
                return "🙃 Что-то я зависла, спроси ещё раз чуть позже."
        except Exception as e:
            logging.error(f"[ai] {e}")
            return "🙃 Не получилось ответить, попробуй ещё раз."

    return "🔌 AI сейчас недоступен, все ключи исчерпаны."
