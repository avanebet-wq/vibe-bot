# -*- coding: utf-8 -*-
"""Fast offline smoke tests for pure storage/security helpers."""
import os, tempfile

# These tests are intentionally module-level and avoid Telegram network calls.
def test_security():
    from security import allow
    assert allow("test-key", 2, 60)
    assert allow("test-key", 2, 60)
    assert not allow("test-key", 2, 60)

def test_goals():
    import database
    old = database.DB_PATH
    # Existing runtime DB is not touched by the assertions below; use unique chat id.
    from goals import add, list_open, complete
    gid = "offline-test-goal"
    item = add(gid, "проверка")
    assert item and list_open(gid)
    assert complete(gid, item["id"])
    assert list_open(gid) == []

def run():
    test_security(); test_goals(); print("offline smoke tests: OK")

if __name__ == "__main__": run()

# V32 mini-games: schema/import smoke check
from minigames import ensure_schema as _ensure_minigame_schema
_ensure_minigame_schema()
