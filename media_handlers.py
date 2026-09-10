# -*- coding: utf-8 -*-
"""Media/service-message handling boundary for the settings subsystem."""
from settings_core import _extract_media, _on_media_message, _on_service_message

extract_media = _extract_media
on_media_message = _on_media_message
on_service_message = _on_service_message

__all__ = ["extract_media", "on_media_message", "on_service_message"]
