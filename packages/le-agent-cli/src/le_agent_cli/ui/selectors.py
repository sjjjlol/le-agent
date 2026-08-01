"""Reusable modal selector shell used by model, session and settings commands."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static


class SearchableSelector(ModalScreen[str | None]):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.title_text = title

    def compose(self) -> ComposeResult:
        yield Static(self.title_text)
        yield Input(placeholder="输入关键词筛选…", id="selector-search")
        yield OptionList(id="selector-options")
