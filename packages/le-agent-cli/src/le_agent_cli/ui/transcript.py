"""Scrollable transcript with compact role-aware rows."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static


class Transcript(VerticalScroll):
    async def append_message(self, text: str, kind: str = "assistant") -> None:
        row = Static(text, classes=f"message {kind}-message")
        await self.mount(row)
        self.scroll_end(animate=False)
