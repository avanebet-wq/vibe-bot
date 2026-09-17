# -*- coding: utf-8 -*-
"""Compatibility facade for the settings subsystem.

The implementation lives in settings_core.py. This module intentionally
re-exports the public API so existing imports and bot behaviour stay unchanged.
"""
from settings_core import *  # noqa: F401,F403

# updated 2026-09-18
