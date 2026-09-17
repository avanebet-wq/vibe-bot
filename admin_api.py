# -*- coding: utf-8 -*-
"""Read-only aggregate data provider for the Mini App dashboard."""
def dashboard(chat_id):
    from reliability import health
    from stats import _chat_data, _aggregate_users, _hourly
    from moderation import _chat_bucket
    from chat_personality import get as get_personality
    from mood_state import get as get_mood
    from goals import list_open as list_goals
    data=_chat_data(chat_id); users=_aggregate_users(data,7); hourly=_hourly(data,7)
    return {
        "health": health(), "messages_7d": sum(users.values()),
        "active_7d": len(users), "messages_24h": sum(_aggregate_users(data,1).values()),
        "top_users": [{"id":u,"name":data.get("names",{}).get(u,u),"messages":n} for u,n in sorted(users.items(),key=lambda x:-x[1])[:10]],
        "peak_hour": max(range(24),key=lambda h: hourly[h]) if any(hourly) else None,
        "liza": data.get("liza",{}), "moderation": data.get("moderation",{}),
        "personality": get_personality(chat_id), "mood": get_mood(chat_id),
        "open_goals": len(list_goals(chat_id)), "moderation_config": _chat_bucket(chat_id).get("config",{}),
    }

# updated 2026-09-18
