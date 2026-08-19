"""Event renderers for LeAgent coding frontends and print modes."""

from __future__ import annotations

from le_agent_coding.extensions.api import CustomMessageMarkup
from le_agent_coding.rendering.base import EventRenderer, PrintOutputMode
from le_agent_coding.rendering.json import JsonEventRenderer
from le_agent_coding.rendering.plain import FinalTextRenderer
from le_agent_coding.rendering.transcript import TranscriptRenderer


def create_event_renderer(
    mode: PrintOutputMode,
    *,
    custom_message_renderer: CustomMessageMarkup | None = None,
) -> EventRenderer:
    """Create a renderer for a print output mode."""
    if mode is PrintOutputMode.text:
        return FinalTextRenderer()
    if mode is PrintOutputMode.json:
        return JsonEventRenderer()
    return TranscriptRenderer(custom_message_renderer=custom_message_renderer)


__all__ = [
    "EventRenderer",
    "FinalTextRenderer",
    "JsonEventRenderer",
    "PrintOutputMode",
    "TranscriptRenderer",
    "create_event_renderer",
]
