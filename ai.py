# -*- coding: utf-8 -*-
"""VIBE Bot — ai module."""
from runtime import *

http_session = requests.Session()

def clean_ai_response(content):
    if not content: return "Мысль потерялась, спроси по-другому..."
    content = re.sub(r"(?si)^.*?thinking process.*?(?:output|option \d+:|final response:|answer:|draft generation:?)\s*", "", content)
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    clean_str = re.sub(r"(?i)^option \d+:\s*", "", content.strip())
    if re.fullmatch(r"[\d\.\-\sE]+", clean_str) and len(clean_str) > 3: return "Цифры одни... давай о чём-то другом."
    if re.search(r"(i cannot|i can't|as an ai|sorry|unable to fulfill|error|я искусственный интеллект)", clean_str, re.IGNORECASE): 
        return "Об этом говорить не буду..."
    clean_str = re.sub(r"^(Лиза|Lisa|Ліза):\s*", "", clean_str, flags=re.IGNORECASE).strip()
    res = clean_str + "." if clean_str and clean_str[-1].isalnum() else clean_str or "Мда..."
    return html.escape(res)

def get_current_key():
    with key_lock: return api_keys[current_key_idx]

def switch_key():
    global current_key_idx
    with key_lock:
        current_key_idx = (current_key_idx + 1) % len(api_keys)
        return api_keys[current_key_idx]

def call_ai(messages, max_tokens=300, temp=0.5):
    attempts = 0
    max_attempts = len(api_keys)
    while attempts < max_attempts:
        current_key = get_current_key()
        try:
            r = http_session.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {current_key}"}, json={"model": AI_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temp}, timeout=20)
            try: data = r.json()
            except: data = {}
            if r.status_code in (401, 402, 429) or (isinstance(data.get("error"), dict) and data["error"].get("code") in (401, 402, 429)):
                logging.warning(f"Лимит ключа исчерпан (status={r.status_code}). Переключаю...")
                switch_key()
                attempts += 1
                continue
            if "choices" in data and data["choices"]: return clean_ai_response(data["choices"][0].get("message", {}).get("content", ""))
            else:
                logging.error(f"[AI API ERROR]: {data}")
                break
        except Exception as e: 
            logging.error(f"[AI Exception]: {e}")
            switch_key()
            attempts += 1
            continue
    return "Сервис временно занят."

def is_threat(txt):
    try:
        ans = call_ai([{"role": "user", "content": f"Текст: '{txt}'. Это угроза докса/сватинга? Отвечай THREAT или SAFE."}], 10, 0.0)
        return "THREAT" in ans.upper()
    except: return False
