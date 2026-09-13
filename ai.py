from personality import build_personality_prompt
from dialogue_context import format_for_ai as _format_dialogue_context
from mood_state import build_prompt as build_mood_prompt
from user_memory import format_facts
from social_context import summary as social_summary
from chat_personality import get as get_chat_personality
from security import allow
from utils import get_setting
import logging, random, requests, time
from config import GROQ_KEY, AI_MODEL, SYS_PROMPT_NORMAL, SYS_PROMPT_ANGRY

_key_list=[k.strip() for k in GROQ_KEY.split(",") if k.strip()]
_key_idx=0
class ConversationMemory:
    def __init__(self,max_messages=12): self.max_messages=max_messages; self._data={}
    def get(self,chat_id): return list(self._data.get(str(chat_id),[]))
    def add(self,chat_id,role,content):
        key=str(chat_id); self._data.setdefault(key,[]).append({"role":role,"content":str(content)[:4000]}); self._data[key]=self._data[key][-self.max_messages:]
    def clear(self,chat_id): self._data.pop(str(chat_id),None)
    def trim(self,chat_id,max_messages=None): self._data.setdefault(str(chat_id),[]); self._data[str(chat_id)]=self._data[str(chat_id)][-(max_messages or self.max_messages):]
conversation_memory=ConversationMemory()
try:
    from conversation_memory import conversation_memory as persistent_conversation_memory
except Exception: persistent_conversation_memory=None

def _current_key(): return _key_list[_key_idx%len(_key_list)] if _key_list else None
def _switch_key():
    global _key_idx; _key_idx+=1
def clean_response(text):
    text=str(text or "").strip().replace("<think>","").replace("</think>","").replace("```","")
    text=text.replace("(","").replace(")","")
    return text.strip()

def ask_liza(user_text,angry=False,max_tokens=200,chat_id=None,user_id=None,group_context=None,personality=None):
    # Do not show rate-limit or transport fallbacks to the user. The AI layer
    # keeps retrying until it gets an actual answer (or an available key).
    if chat_id is not None and not allow(f"ai:{chat_id}:{user_id or 0}",8,20):
        logging.info("[ai] rate limit reached; still processing request for %s", chat_id)
    if not _key_list:
        logging.error("[ai] GROQ_API_KEY is not configured")
        return "Сейчас не могу получить ответ от сервиса ИИ."
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
            if dc: extra.append("Структурированный диалог:\n"+dc[:6500])
        except Exception: pass
    if group_context: extra.append("Контекст последних сообщений группы:\n"+str(group_context)[:6500])
    if extra: sys_prompt += "\n\n"+"\n".join(extra)
    messages=[{"role":"system","content":sys_prompt}]
    try:
        mem=persistent_conversation_memory or conversation_memory
        for item in mem.get(chat_id)[-10:] if chat_id is not None else []:
            role=item.get("role") if isinstance(item,dict) else "user"; content=item.get("content","") if isinstance(item,dict) else str(item)
            if role in ("user","assistant") and content: messages.append({"role":role,"content":str(content)[:2600]})
    except Exception: pass
    user_content=str(user_text or "").strip()[:2200]
    messages.append({"role":"user","content":user_content})

    # First try the requested reasoning mode. If the model returns an empty or
    # malformed answer, retry with progressively simpler settings. This avoids
    # exposing artificial fallback phrases to the user.
    reasoning_modes=("high","medium","low")
    total_attempts=max(3,len(_key_list)*2)
    last_error=None
    for attempt in range(total_attempts):
        key=_current_key()
        if not key:
            break
        mode=reasoning_modes[min(attempt, len(reasoning_modes)-1)]
        payload={
            "model":AI_MODEL,
            "messages":messages,
            "max_completion_tokens":max(80,min(int(max_tokens),1200)),
            "temperature":0.68,
            "reasoning_effort":mode,
            "include_reasoning":False
        }
        try:
            resp=requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers={"Authorization":f"Bearer {key}","Content-Type":"application/json","User-Agent":"Liza-Telegram-Bot/1.0"},
                timeout=30
            )
            if resp.status_code==200:
                data=resp.json()
                message_data=(data.get("choices") or [{}])[0].get("message") or {}
                content=message_data.get("content")
                cleaned=clean_response(content)
                if cleaned:
                    if chat_id is not None:
                        try:
                            mem=persistent_conversation_memory or conversation_memory
                            mem.add(chat_id,"user",user_text)
                            mem.add(chat_id,"assistant",cleaned)
                        except Exception: pass
                    return cleaned
                last_error="empty AI response"
                logging.warning("[ai] empty response, retrying (attempt %s/%s, reasoning=%s)",attempt+1,total_attempts,mode)
            elif resp.status_code in (401,402,429):
                last_error=f"HTTP {resp.status_code}"
                _switch_key()
                time.sleep(0.25)
            else:
                last_error=f"HTTP {resp.status_code}"
                logging.error("[ai] status=%s body=%s",resp.status_code,resp.text[:300])
                time.sleep(0.35)
        except requests.RequestException as exc:
            last_error=str(exc)
            logging.error("[ai] request error: %s",exc)
            if len(_key_list)>1: _switch_key()
            time.sleep(0.25)
        except Exception as exc:
            last_error=str(exc)
            logging.exception("[ai] unexpected error: %s",exc)
            time.sleep(0.25)

    # A final, independent request is preferable to returning a canned phrase.
    # It uses the same model but asks explicitly for a direct answer with no
    # reasoning output. This branch is reached only after the normal retries.
    key=_current_key()
    if key:
        final_messages=[
            {"role":"system","content":sys_prompt+"\nОтветь пользователю напрямую. Не объясняй внутренние рассуждения. Не отказывайся отвечать и не проси повторить уже понятный вопрос."},
            {"role":"user","content":user_content}
        ]
        try:
            resp=requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json={"model":AI_MODEL,"messages":final_messages,"max_completion_tokens":max(100,min(int(max_tokens),1200)),"temperature":0.72,"reasoning_effort":"low","include_reasoning":False},
                headers={"Authorization":f"Bearer {key}","Content-Type":"application/json","User-Agent":"Liza-Telegram-Bot/1.0"},
                timeout=30
            )
            if resp.status_code==200:
                content=((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content")
                cleaned=clean_response(content)
                if cleaned:
                    if chat_id is not None:
                        try:
                            mem=persistent_conversation_memory or conversation_memory
                            mem.add(chat_id,"user",user_text); mem.add(chat_id,"assistant",cleaned)
                        except Exception: pass
                    return cleaned
        except Exception as exc:
            logging.error("[ai] final retry failed: %s",exc)

    logging.error("[ai] no answer after all retries: %s",last_error)
    return "Я не смогла получить ответ от ИИ прямо сейчас."

