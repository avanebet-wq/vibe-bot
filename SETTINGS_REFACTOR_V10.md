# Refactoring v10 — settings subsystem

The original settings implementation is preserved verbatim in `settings_core.py`
to avoid behavioural regressions. `settings.py` is now a compatibility facade and
continues to expose the same public API used by `handlers.py` and `main.py`.

Stable subsystem boundaries:
- `settings_callbacks.py` — callback API
- `scheduler.py` — scheduler API
- `group_settings.py` — group settings/navigation API
- `media_handlers.py` — media/service message API
- `miniapp_settings.py` — Mini App integration boundary
- `settings_ui.py` — existing UI/keyboard layer
- `settings_store.py` — existing persistence layer

No handler registration, callback data format, settings keys, scheduler format,
or user-facing commands were intentionally changed.
