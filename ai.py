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
from config import (
    GROQ_KEY,
    GROQ_AI_MODEL,
    HF_TOKEN,
    HF_BASE_URL,
    AI_MODEL,
    SYS_PROMPT_NORMAL,
    SYS_PROMPT_ANGRY,
)

_key_list = [k.strip() for k in GROQ_KEY.split(",") if k.strip()]
_key_idx = 0
_key_lock = threading.Lock()
_circuit_lock = threading.Lock()
_circuit_failures = 0
_circuit_until = 0.0
_CIRCUIT_THRESHOLD = 5
_CIRCUIT_COOLDOWN = 30.0
_HTTP_LOCAL = threading.local()


def _http_session():
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=2, pool_maxsize=2, max_retries=0)
        session.mount("https://", adapter)
        session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "Liza-Telegram-Bot/1.0",
        })
        _HTTP_LOCAL.session = session
    return session


try:
    from conversation_memory import conversation_memory as persistent_conversation_memory
except Exception:
    persistent_conversation_memory = None


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
    text = str(text or "").strip()
    text = text.replace("<think>", "").replace("</think>", "").replace("```", "")
    text = text.replace("(", "").replace(")", "")
    return text.strip()


def _message_content(data):
    """Safely extract visible assistant text from an OpenAI-compatible response."""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        content = "".join(parts)
    return clean_response(content)


def _post_huggingface(messages, max_tokens, enable_thinking=True):
    if not HF_TOKEN:
        return None, "HF_TOKEN is not configured"

    # Qwen3.8 can spend the whole output budget on hidden reasoning.
    # Give it enough room to think, while keeping the visible answer short.
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "max_tokens": max(512, min(int(max_tokens), 2048)),
        "temperature": 1.0 if enable_thinking else 0.7,
        "top_p": 0.95 if enable_thinking else 0.80,
        "presence_penalty": 0.0 if enable_thinking else 1.5,
        # Hugging Face Inference Providers accepts Qwen3.8 reasoning control
        # as a top-level OpenAI-compatible field. OVHcloud rejects nested
        # extra_body/chat_template_kwargs with HTTP 400.
        "reasoning_effort": "medium" if enable_thinking else "low",
    }
    try:
        resp = _http_session().post(
            f"{HF_BASE_URL.rstrip('/')}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            timeout=(5, 30 if not enable_thinking else 45),
        )
    except requests.RequestException as exc:
        return None, str(exc)

    if resp.status_code == 200:
        try:
            data = resp.json()
            content = _message_content(data)
        except Exception as exc:
            return None, f"invalid HF JSON: {exc}"
        if content:
            return content, None

        # Do not expose Qwen's private reasoning to Telegram.
        # Log a compact diagnostic so provider/schema problems are visible.
        choices = data.get("choices") or []
        msg = choices[0].get("message") if choices else {}
        if isinstance(msg, dict) and (msg.get("reasoning_content") or msg.get("reasoning")):
            return None, "HF returned reasoning without final answer"
        return None, "empty HF response"

    body = resp.text[:500]
    return None, f"HF HTTP {resp.status_code}: {body}"

def _post_groq(messages, max_tokens):
    if not _key_list:
        return None, "GROQ_API_KEY is not configured"
    for _ in range(min(2, len(_key_list))):
        key = _current_key()
        if not key:
            break
        payload = {
            "model": GROQ_AI_MODEL,
            "messages": messages,
            "max_completion_tokens": max(384, min(int(max_tokens), 768)),
            "temperature": 0.68,
            "reasoning_effort": "low",
            "include_reasoning": False,
        }
        try:
            resp = _http_session().post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
                timeout=(4, 12),
            )
        except requests.RequestException as exc:
            _switch_key()
            return None, str(exc)

        if resp.status_code == 200:
            try:
                content = _message_content(resp.json())
            except Exception as exc:
                return None, f"invalid Groq JSON: {exc}"
            if content:
                return content, None
            _switch_key()
            continue

        if resp.status_code in (401, 402, 429, 498):
            _switch_key()
            continue
        return None, f"Groq HTTP {resp.status_code}: {resp.text[:300]}"
    return None, "Groq returned no answer"


def _choose_reasoning_effort(user_text, group_context=None):
    """Choose Qwen thinking level from the request complexity."""
    text = str(user_text or "").strip()
    low = text.lower()
    complex_markers = (
        "почему", "объясни", "объяснить", "как сделать", "как настроить",
        "сравни", "сравнение", "план", "придумай", "проанализируй",
        "разбери", "посчитай", "рассчитай", "код", "python", "sql",
        "ошибка", "почему не", "помоги", "совет", "что лучше", "разница",
        "подробно", "докажи", "спланируй", "инструкция", "проблема",
    )
    if len(text) >= 500 or len(text.split()) >= 80:
        return "medium"
    if any(marker in low for marker in complex_markers):
        return "medium"
    if group_context and len(str(group_context)) >= 4500:
        return "medium"
    return "low"


def _reasoning_budget(effort, max_tokens):
    requested = max(1, int(max_tokens))
    if effort == "low":
        return max(512, min(requested, 768))
    return max(1024, min(requested, 2048))


def ask_liza(user_text, angry=False, max_tokens=200, chat_id=None, user_id=None, group_context=None, personality=None):
    if chat_id is not None and not allow(f"ai:{chat_id}:{user_id or 0}", 8, 20):
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            time.sleep(0.2)
            if allow(f"ai:{chat_id}:{user_id or 0}", 8, 20):
                break

    if not _circuit_allows():
        logging.warning("[ai] circuit breaker open")
        return None

    sys_prompt = SYS_PROMPT_ANGRY if angry else SYS_PROMPT_NORMAL
    extra = []
    try:
        extra.append(build_personality_prompt(personality or (get_chat_personality(chat_id) if chat_id is not None else None)))
    except Exception:
        pass
    if chat_id is not None:
        try:
            extra.append(build_mood_prompt(chat_id))
        except Exception:
            pass
    if chat_id is not None and user_id is not None:
        try:
            if get_setting(chat_id, "memory_enabled", True):
                facts = format_facts(chat_id, user_id)
                if facts:
                    extra.append(facts)
            social = social_summary(chat_id, user_id)
            if social:
                extra.append("Связи только по текущему разговору, используй осторожно: " + str(social)[:1200])
        except Exception:
            pass
        try:
            dc = _format_dialogue_context(chat_id, limit=10)
            if dc:
                dc_text = "\n".join(
                    f"{item.get('role', 'user')}: {item.get('content', '')}"
                    for item in dc if isinstance(item, dict)
                )
                extra.append("Структурированный диалог:\n" + dc_text[:6500])
        except Exception:
            logging.exception("[ai] failed to build structured dialogue context")
    if group_context:
        extra.append("Контекст последних сообщений группы:\n" + str(group_context)[:6500])
    if extra:
        sys_prompt += "\n\n" + "\n".join(extra)

    messages = [{"role": "system", "content": sys_prompt}]
    try:
        mem = persistent_conversation_memory
        for item in mem.get(chat_id)[-10:] if chat_id is not None else []:
            role = item.get("role") if isinstance(item, dict) else "user"
            content = item.get("content", "") if isinstance(item, dict) else str(item)
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": str(content)[:2600]})
    except Exception:
        pass

    messages.append({"role": "user", "content": str(user_text or "").strip()[:2200]})

    # Qwen is primary. Simple chat uses low reasoning for speed; complex
    # requests use medium reasoning. If a provider returns no visible answer,
    # retry once at the lighter level. Private reasoning is never sent to chat.
    effort = _choose_reasoning_effort(user_text, group_context)
    last_error = None
    attempts = (effort, "low") if effort == "medium" else ("low",)
    for attempt_effort in attempts:
        enable_thinking = attempt_effort in ("low", "medium")
        budget = _reasoning_budget(attempt_effort, max_tokens)
        content, error = _post_huggingface(
            messages,
            budget,
            enable_thinking=enable_thinking,
        )
        if content:
            _circuit_success()
            if chat_id is not None:
                try:
                    mem = persistent_conversation_memory
                    mem.add(chat_id, "user", user_text)
                    mem.add(chat_id, "assistant", content)
                except Exception:
                    pass
            return content
        last_error = error
        _circuit_failure()
        logging.warning("[ai] HF %s attempt failed: %s", attempt_effort, error)
        time.sleep(0.25)

    # Keep the old Groq path as a safety net if the old Railway secrets are still
    # configured. This prevents a temporary HF/provider outage from breaking AI.
    content, error = _post_groq(messages, max_tokens)
    if content:
        _circuit_success()
        if chat_id is not None:
            try:
                mem = persistent_conversation_memory
                mem.add(chat_id, "user", user_text)
                mem.add(chat_id, "assistant", content)
            except Exception:
                pass
        return content
    last_error = error or last_error

    logging.error("[ai] no answer after HF + fallback: %s", last_error)
    # Не показываем пользователю техническую ошибку/фолбэк-текст.
    # Очередь может повторить запрос, а обработчик сам даст нейтральный ответ.
    return None
