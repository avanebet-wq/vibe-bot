import os
from zoneinfo import ZoneInfo

TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY")

BOSSES_STR = os.environ.get("BOSSES", "8343784157, 8986901371")
BOSSES = [int(x.strip()) for x in BOSSES_STR.split(",") if x.strip().isdigit()]

AI_MODEL = "inclusionai/ling-3.0-flash-fin:free"
ALLOWED_GROUPS_RAW = ["4374303475", "3514059820"]
ALLOWED_GROUPS = [4374303475, -4374303475, -1004374303475, 3514059820, -3514059820, -1003514059820]
DENIED_MSG = "🚫 Я работаю только в группе VIBE, у групах з мого списку та в особистих чатах зі своїми творцями."
KYIV_TZ = ZoneInfo("Europe/Kyiv")
