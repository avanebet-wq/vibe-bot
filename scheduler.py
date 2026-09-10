# -*- coding: utf-8 -*-
"""Scheduler API for settings and scheduled publications."""
from settings_core import (
    _scheduler_tick,
    _delayed_delete,
    _scheduler_loop,
    start_scheduler,
)

scheduler_tick = _scheduler_tick
delayed_delete = _delayed_delete
scheduler_loop = _scheduler_loop

__all__ = ["scheduler_tick", "delayed_delete", "scheduler_loop", "start_scheduler"]
