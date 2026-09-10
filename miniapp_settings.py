# -*- coding: utf-8 -*-
"""Mini App integration boundary.

The HTTP Mini App implementation itself remains in miniapp.py. This module
provides a dedicated settings-facing boundary without changing startup order.
"""
from miniapp import start_server

__all__ = ["start_server"]
