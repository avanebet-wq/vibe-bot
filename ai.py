from personality import build_personality_prompt
from dialogue_context import format_for_ai as _format_dialogue_context
from mood_state import build_prompt as build_mood_prompt
from user_memory import format_facts
from social_context import summary as social_summary
from chat_personality import get as get_chat_personality
from security import allow
from utils import get_setting
import logging, threading, requests, time
from concurrent.futures import ThreadPoolExecutor
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
# Personality/mood/facts/social-graph/dialogue/history lookups below are
# independent of each other and several hit the database. Running them
# concurrently turns N sequential round trips into roughly one.
_CTX_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="liza-ctx")


def _safe_ctx(job):
    try:
        return job()
    except Exception:
        logging.exception("[ai] context job failed")
        return None


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


def _post_huggingface(messages, max_tokens, reasoning_effort="low"):
    if not HF_TOKEN:
        return None, "HF_TOKEN is not configured"

    # NOTE: this used to be driven by a boolean `enable_thinking` that was
    # `attempt_effort in ("low", "medium")` at the call site -- which is
    # *always true*, since attempt_effort is always one of those two values.
    # In practice every request was silently sent as "medium" reasoning with
    # a 45s timeout, no matter what _choose_reasoning_effort() decided. Simple
    # messages never actually got the fast "low" path. Fixed by passing the
    # real effort level straight through instead of a boolean.
    is_low = reasoning_effort == "low"

    # Qwen3.8 can spend the whole output budget on hidden reasoning.
    # Give it enough room to think, while keeping the visible answer short.
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "max_tokens": max(512, min(int(max_tokens), 2048)),
        "temperature": 0.7 if is_low else 1.0,
        "top_p": 0.80 if is_low else 0.95,
        "presence_penalty": 1.5 if is_low else 0.0,
        # Hugging Face Inference Providers accepts Qwen3.8 reasoning control
        # as a top-level OpenAI-compatible field. OVHcloud rejects nested
        # extra_body/chat_template_kwargs with HTTP 400.
        "reasoning_effort": reasoning_effort,
    }
    try:
        resp = _http_session().post(
            f"{HF_BASE_URL.rstrip('/')}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            timeout=(5, 18 if is_low else 28),
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
    """Choose Qwen thinking level from the request complexity.

    Kept deliberately narrow: this used to include very common words like
    "помоги", "совет", "что лучше", "проблема" -- which fire on a huge share
    of ordinary casual messages and pushed them onto the slower "medium"
    path for no real benefit. Only genuinely complex asks (explanations,
    code, calculations, explicit "in detail") should pay for extra thinking.
    """
    text = str(user_text or "").strip()
    low = text.lower()
    complex_markers = (
        "почему", "объясни", "объяснить", "как сделать", "как настроить",
        "сравни", "сравнение", "придумай", "проанализируй",
        "разбери", "посчитай", "рассчитай", "код", "python", "sql",
        "ошибка", "почему не", "разница",
        "подробно", "докажи", "спланируй", "инструкция",
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


def ask_liza(user_text, angry=False, max_tokens=200, chat_id=None, user_id=None, group_context=None, personality=None, user_context=None, is_group=False, user_name=None):
    if not _circuit_allows():
        logging.warning("[ai] circuit breaker open")
        return None

    sys_prompt = SYS_PROMPT_ANGRY if angry else SYS_PROMPT_NORMAL

    def _personality_job():
        return build_personality_prompt(personality or (get_chat_personality(chat_id) if chat_id is not None else None))

    def _mood_job():
        return build_mood_prompt(chat_id) if chat_id is not None else None

    def _facts_job():
        if chat_id is None or user_id is None:
            return None
        if not get_setting(chat_id, "memory_enabled", True):
            return None
        return format_facts(chat_id, user_id) or None

    def _social_job():
        if chat_id is None or user_id is None:
            return None
        social = social_summary(chat_id, user_id)
        if not social:
            return None
        return "Связи только по текущему разговору, используй осторожно: " + str(social)[:1200]

    def _dialogue_job():
        if chat_id is None or user_id is None:
            return None
        dc = _format_dialogue_context(chat_id, limit=8)
        if not dc:
            return None
        # Each item's content is already "Имя: текст" (see dialogue_context.record),
        # so the artificial "user:" role wrapper only added noise and hid the
        # actual speaker behind a generic label.
        dc_text = "\n".join(
            str(item.get("content", "")) for item in dc if isinstance(item, dict) and item.get("content")
        )
        if not dc_text:
            return None
        return (
            "Структурированный диалог (каждая строка размечена именем говорящего, "
            "не путай реплики разных людей):\n" + dc_text[:4000]
        )

    def _history_job():
        # In group chats this table stores messages from several different
        # people (see group_context.record_group_message), so replaying it
        # as raw alternating user/assistant turns loses who-said-what and is
        # exactly what made Liza mix up different users' lines. Group chats
        # get their attribution from group_context/_dialogue_job instead,
        # which keep an explicit "Имя: текст" per line. Plain 1-on-1 chats
        # have only one human speaker, so turn-based history stays safe there.
        if chat_id is None or is_group:
            return []
        try:
            return persistent_conversation_memory.get(chat_id)[-6:]
        except Exception:
            return []

    # These five lookups are independent and several hit the database
    # (facts, social graph, dialogue context, conversation history). Firing
    # them concurrently instead of one after another turns several
    # sequential DB round trips into roughly the time of the slowest one.
    jobs = {
        "personality": _personality_job,
        "mood": _mood_job,
        "facts": _facts_job,
        "social": _social_job,
        "dialogue": _dialogue_job,
        "history": _history_job,
    }
    futures = {name: _CTX_EXECUTOR.submit(_safe_ctx, job) for name, job in jobs.items()}
    results = {name: f.result() for name, f in futures.items()}

    extra = [results[name] for name in ("personality", "mood", "facts", "social", "dialogue") if results.get(name)]

    if is_group:
        extra.append(
            "Это групповой чат: одновременно пишут разные люди. Ниже реплики размечены "
            "именем говорящего — ориентируйся по нему и не приписывай сказанное одним "
            "человеком другому."
        )

    if group_context:
        # group_context is the raw [{"role", "content"}, ...] rows from the shared
        # chat-history table: "user" rows already carry "Имя: текст" (see
        # group_context.record_group_message), "assistant" rows are Liza's own
        # past replies. Render them as a clean labeled transcript instead of a
        # raw Python repr, which is both unreadable and harder to attribute.
        lines = []
        for item in group_context:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            lines.append(f"Лиза: {content}" if item.get("role") == "assistant" else content)
        if lines:
            extra.append(
                "Контекст последних сообщений группы (каждая строка от своего человека):\n"
                + "\n".join(lines)[:3000]
            )

    if user_context or user_name:
        parts = []
        if user_name:
            parts.append(f"Сейчас тебе пишет: {user_name}.")
        if user_context:
            parts.append(str(user_context)[:2500])
        extra.append(
            "ПЕРСОНАЛЬНЫЙ КОНТЕКСТ СОБЕСЕДНИКА:\n" + "\n".join(parts) +
            "\nОбращайся к текущему человеку как к отдельному собеседнику. Подстраивай длину, сленг, эмодзи, пунктуацию и степень неформальности под его манеру, но не копируй его фразы дословно."
        )
    if extra:
        sys_prompt += "\n\n" + "\n".join(extra)

    messages = [{"role": "system", "content": sys_prompt}]
    for item in (results.get("history") or []):
        role = item.get("role") if isinstance(item, dict) else "user"
        content = item.get("content", "") if isinstance(item, dict) else str(item)
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)[:1800]})

    messages.append({"role": "user", "content": str(user_text or "").strip()[:2200]})

    def _save_turn(reply_text):
        if chat_id is None:
            return
        try:
            mem = persistent_conversation_memory
            # In groups the incoming message was already saved with its
            # speaker's name by group_context.record_group_message before
            # ask_liza ran. Saving the bare user_text here too would add an
            # unlabeled duplicate of the same message into the shared table,
            # which is exactly the kind of unattributed entry that made Liza
            # mix up who said what. DMs have only one human speaker, so it's
            # safe (and needed) to save the turn there.
            if not is_group:
                mem.add(chat_id, "user", user_text)
            mem.add(chat_id, "assistant", reply_text)
        except Exception:
            pass

    # Qwen is primary. Simple chat uses low reasoning for speed; complex
    # requests use medium reasoning. If a provider returns no visible answer,
    # retry once at the lighter level. Private reasoning is never sent to chat.
    effort = _choose_reasoning_effort(user_text, group_context)
    last_error = None
    attempts = (effort, "low") if effort == "medium" else ("low",)
    for attempt_effort in attempts:
        budget = _reasoning_budget(attempt_effort, max_tokens)
        content, error = _post_huggingface(
            messages,
            budget,
            reasoning_effort=attempt_effort,
        )
        if content:
            _circuit_success()
            _save_turn(content)
            return content
        last_error = error
        _circuit_failure()
        logging.warning("[ai] HF %s attempt failed: %s", attempt_effort, error)

    # Keep the old Groq path as a safety net if the old Railway secrets are still
    # configured. This prevents a temporary HF/provider outage from breaking AI.
    content, error = _post_groq(messages, max_tokens)
    if content:
        _circuit_success()
        _save_turn(content)
        return content
    last_error = error or last_error

    logging.error("[ai] no answer after HF + fallback: %s", last_error)
    # Не показываем пользователю техническую ошибку/фолбэк-текст.
    # Очередь может повторить запрос, а обработчик сам даст нейтральный ответ.
    return None
