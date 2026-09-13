# -*- coding: utf-8 -*-
"""Per-chat personality settings with bounded values."""
from database import db_get, db_set, db_update_json
from config import PERSONALITY_DEFAULTS

def get(chat_id):
    store = db_get("chat_personality", {})
    data = dict(PERSONALITY_DEFAULTS); data.update(store.get(str(chat_id), {}))
    return {k: max(0, min(100, int(v))) for k,v in data.items() if k in PERSONALITY_DEFAULTS}

def set_value(chat_id, key, value):
    if key not in PERSONALITY_DEFAULTS: return False
    try: value = max(0, min(100, int(value)))
    except (TypeError, ValueError): return False
    def mutate(store):
        store.setdefault(str(chat_id), {})[key] = value
        return store
    db_update_json("chat_personality", mutate, {})
    return True
