from personality import build_personality_prompt
from dialogue_context import format_for_ai as _format_dialogue_context
from mood_state import build_prompt as build_mood_prompt
from user_memory import format_facts
from social_context import summary as social_summary
from chat_personality import get as get_chat_personality
from security import allow
from utils import get_setting
import logging, threading, requests, time
from requests.adapters import HTTPAdapter
from config import GROQ_KEY, AI_MODEL, SYS_PROMPT_NORMAL, SYS_PROMPT_ANGRY

_key_list=[k.strip() for k in GROQ_KEY.split(",") if k.strip()]
_key_idx=0
_key_lock=threading.Lock()
_circuit_lock=threading.Lock()
_circuit_failures=0
_circuit_until=0.0
_CIRCUIT_THRESHOLD=5
_CIRCUIT_COOLDOWN=30.0
_HTTP_LOCAL=threading.local()

def _http_session():
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=0)
        session.mount("https://", adapter)
        session.headers.update({"Content-Type": "application/json", "User-Agent": "Liza-Telegram-Bot/1.0"})
        _HTTP_LOCAL.session = session
    return session
try:
    from conversation_memory import conversation_memory as persistent_conversation_memory
except Exception: persistent_conversation_memory=None

def _current_key():
    with _key_lock:
        return _key_list[_key_idx % len(_key_list)] if _key_list else None

def _switch_key():
    global _key_idx
    with _key_lock:
        _key_idx += 1
def _circuit_allows():
    with _circuit_lock:
        return time.monotonic() >= _circuit_until

def _circuit_success():
    global _circuit_failures, _circuit_until
    with _circuit_lock:
        _circuit_failures = 0
        _circuit_until = 0.0

def _circuit_failure():
    global _circuit_failures, _circuit_until
    with _circuit_lock:
        _circuit_failures += 1
        if _circuit_failures >= _CIRCUIT_THRESHOLD:
            _circuit_until = time.monotonic() + _CIRCUIT_COOLDOWN

def clean_response(text):
    text=str(text or "").strip().replace("<think>","").replace("</think>","").replace("```","")
    text=text.replace("(","").replace(")","")
    return text.strip()

def ask_liza(user_text,angry=False,max_tokens=200,chat_id=None,user_id=None,group_context=None,personality=None):
    # Do not show rate-limit or transport fallbacks to the user. The AI layer
    # keeps retrying until it gets an actual answer (or an available key).
    if chat_id is not None and not allow(f"ai:{chat_id}:{user_id or 0}",8,20):
        # Never tie up a worker for the whole local rate-limit window. One
        # bounded wait is enough; the queue itself is the main backpressure.
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            time.sleep(0.2)
            if allow(f"ai:{chat_id}:{user_id or 0}",8,20):
                break
    if not _key_list:
        logging.error("[ai] GROQ_API_KEY is not configured")
        return "⚠️ Не удалось получить ответ от ИИ. Попробуй ещё раз через несколько секунд."
    if not _circuit_allows():
        logging.warning("[ai] circuit breaker open")
        return None
    sys_prompt=SYS_PROMPT_ANGRY if angry else SYS_PROMPT_NORMAL; extra=[]
    try: extra.append(build_personality_prompt(personality or (get_chat_personality(chat_id) if chat_id is not None else None)))
    except Exception: pass
    if chat_id is not None:
        try: extra.append(build_mood_prompt(chat_id))
        except Exception: pass
    if chat_id is not None and user_id is not None:
        try:
            if get_setting(chat_id, "memory_enabled", True):
                facts=format_facts(chat_id,user_id)
                if facts: extra.append(facts)
            social=social_summary(chat_id,user_id)
            if social: extra.append("Связи только по текущему разговору, используй осторожно: "+str(social)[:1200])
        except Exception: pass
        try:
            dc=_format_dialogue_context(chat_id,limit=10)
            if dc:
                dc_text = "\n".join(
                    f"{item.get('role','user')}: {item.get('content','')}"
                    for item in dc if isinstance(item, dict)
                )
                extra.append("Структурированный диалог:\n"+dc_text[:6500])
        except Exception:
            logging.exception("[ai] failed to build structured dialogue context")
    if group_context: extra.append("Контекст последних сообщений группы:\n"+str(group_context)[:6500])
    if extra: sys_prompt += "\n\n"+"\n".join(extra)
    messages=[{"role":"system","content":sys_prompt}]
    try:
        mem=persistent_conversation_memory
        for item in mem.get(chat_id)[-10:] if chat_id is not None else []:
            role=item.get("role") if isinstance(item,dict) else "user"; content=item.get("content","") if isinstance(item,dict) else str(item)
            if role in ("user","assistant") and content: messages.append({"role":role,"content":str(content)[:2600]})
    except Exception: pass
    user_content=str(user_text or "").strip()[:2200]
    messages.append({"role":"user","content":user_content})

    # GPT-OSS spends completion tokens on reasoning. Starting at high effort
    # with a small max_completion_tokens budget can exhaust the budget before
    # any visible answer is produced, which looks like an "empty response".
    # Chat replies prioritize latency: low effort first, then one medium retry.
    reasoning_modes=("low","medium")
    total_attempts=2
    last_error=None
    for attempt in range(total_attempts):
        key=_current_key()
        if not key:
            break
        mode=reasoning_modes[min(attempt, len(reasoning_modes)-1)]
        payload={
            "model":AI_MODEL,
            "messages":messages,
            "max_completion_tokens":max(384,min(int(max_tokens),768)),
            "temperature":0.68,
            "reasoning_effort":mode,
            "include_reasoning":False
        }
        try:
            resp=_http_session().post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers={"Authorization":f"Bearer {key}"},
                timeout=(4,12)
            )
            if resp.status_code==200:
                data=resp.json()
                message_data=(data.get("choices") or [{}])[0].get("message") or {}
                content=message_data.get("content")
                cleaned=clean_response(content)
                if cleaned:
                    _circuit_success()
                    if chat_id is not None:
                        try:
                            mem=persistent_conversation_memory
                            mem.add(chat_id,"user",user_text)
                            mem.add(chat_id,"assistant",cleaned)
                        except Exception: pass
                    return cleaned
                last_error="empty AI response"
                _circuit_failure()
                logging.warning("[ai] empty response, retrying (attempt %s/%s, reasoning=%s)",attempt+1,total_attempts,mode)
            elif resp.status_code in (401,402,429,498):
                last_error=f"HTTP {resp.status_code}"
                _circuit_failure()
                _switch_key()
                retry_after = resp.headers.get("retry-after") if resp.status_code == 429 else None
                try:
                    delay = min(2.0, max(0.1, float(retry_after))) if retry_after is not None else 0.25
                except (TypeError, ValueError):
                    delay = 0.25
                time.sleep(delay)
            else:
                last_error=f"HTTP {resp.status_code}"
                _circuit_failure()
                logging.error("[ai] status=%s body=%s",resp.status_code,resp.text[:300])
                time.sleep(0.35)
        except requests.RequestException as exc:
            last_error=str(exc)
            _circuit_failure()
            logging.error("[ai] request error: %s",exc)
            if len(_key_list)>1: _switch_key()
            time.sleep(0.25)
        except Exception as exc:
            last_error=str(exc)
            _circuit_failure()
            logging.exception("[ai] unexpected error: %s",exc)
            time.sleep(0.25)

    # Do not issue a third full request here: after two bounded attempts, a
    # third call only makes the bot feel stuck and can occupy the AI worker.
    logging.error("[ai] no answer after all retries: %s",last_error)
    return "⚠️ Не удалось получить ответ от ИИ. Попробуй ещё раз через несколько секунд."

