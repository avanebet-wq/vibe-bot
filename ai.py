import re, requests, logging
from config import OPENROUTER_KEY, AI_MODEL

def clean_ai_response(content):
    if not content: return "С радостью помогу, но спроси по другому..."
    content = re.sub(r"(?si)^.*?thinking process.*?(?:output|option \d+:|final response:|answer:|draft generation:?)\s*", "", content)
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    clean_str = content.strip()
    clean_str = re.sub(r"(?i)^option \d+:\s*", "", clean_str)

    if re.fullmatch(r"[\d\.\-\sE]+", clean_str) and len(clean_str) > 3:
        return "С радостью помогу, но спроси по другому..."
    if re.search(r"(i cannot|i can't|as an ai|sorry|error|я искусственный интеллект)", clean_str, re.IGNORECASE):
        return "С радостью помогу, но спроси по другому..."

    clean_str = re.sub(r"^(Лиза|Lisa|Ліза):\s*", "", clean_str, flags=re.IGNORECASE)
    clean_str = re.sub(r"[()]+", "", clean_str)
    res = clean_str.strip()
    if not res: return "С радостью помогу..."
    if len(res) >= 2 and res[-1].isalnum(): res += "."
    return res

def call_ai(messages, max_tokens=300, temp=0.5):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
    try:
        payload = {"model": AI_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temp}
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        data = r.json()
        if "choices" in data and data["choices"]:
            return clean_ai_response(data["choices"][0].get("message", {}).get("content", ""))
    except Exception as e:
        logging.error(f"[AI Error]: {e}")
    return "Сервис временно занят."
