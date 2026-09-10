# -*- coding: utf-8 -*-
"""Callback layer for the settings subsystem.

Kept as a stable module boundary; the callback implementation remains in
settings_core.py to preserve exact runtime behaviour.
"""
from settings_core import _on_settings_callback, _dispatch_callback

on_settings_callback = _on_settings_callback
dispatch_callback = _dispatch_callback

__all__ = ["on_settings_callback", "dispatch_callback"]
