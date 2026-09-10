# -*- coding: utf-8 -*-
"""Safe plugin registry. Plugin modules expose register(bot) optionally."""
import importlib
import logging
import os

LOG = logging.getLogger(__name__)

def discover(bot):
    loaded = []
    names = [x.strip() for x in os.environ.get("LIZA_PLUGINS", "").split(",") if x.strip()]
    for name in names:
        # Flat GitHub layout: plugins are ordinary .py modules in the project root.
        # Keep accepting the old plugins.<name> notation when a package exists elsewhere.
        module_name = name.strip()
        if module_name.startswith("plugins."):
            module_name = module_name.split(".", 1)[1]
        try:
            mod = importlib.import_module(module_name)
            fn = getattr(mod, "register", None)
            if callable(fn):
                fn(bot)
                loaded.append(name)
        except Exception:
            LOG.exception("plugin failed: %s", name)
    return loaded
