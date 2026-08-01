"""Reusable modal selector shell used by model, session and settings commands."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


@dataclass(frozen=True, slots=True)
class SelectorItem:
    value: str
    label: str
    description: str = ""


class SearchableSelector(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, title: str, items: list[SelectorItem], *, current: str | None = None) -> None:
        super().__init__()
        self.title_text = title
        self.items = items
        self.current = current

    def compose(self) -> ComposeResult:
        yield Static(self.title_text, id="selector-title")
        yield Input(placeholder="输入关键词筛选…", id="selector-search")
        yield OptionList(id="selector-options")

    def on_mount(self) -> None:
        self._refresh_options()
        self.query_one("#selector-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "selector-search":
            self._refresh_options()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "selector-options" and event.option.id:
            self.dismiss(str(event.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _refresh_options(self) -> None:
        query = self.query_one("#selector-search", Input).value.casefold().strip()
        matches = [
            item
            for item in self.items
            if not query or query in f"{item.value} {item.label} {item.description}".casefold()
        ]
        matches.sort(key=lambda item: item.value != self.current)
        options = self.query_one("#selector-options", OptionList)
        options.clear_options()
        for item in matches:
            marker = "◆ " if item.value == self.current else "  "
            label = Text.assemble((f"{marker}{item.label}", "bold #88c0d0"))
            if item.description:
                label.append(f"  {item.description}", style="#8f9baa")
            options.add_option(Option(label, id=item.value))
        options.highlighted = 0 if matches else None
