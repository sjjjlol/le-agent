"""Slash command completion palette."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ..commands import CommandSpec


class CommandPalette(OptionList):
    def set_commands(self, commands: list[CommandSpec]) -> None:
        self.clear_options()
        for command in commands:
            label = Text.assemble((f"/{command.name}", "bold #88c0d0"))
            if command.argument_hint:
                label.append(f" {command.argument_hint}", style="#8f9baa")
            label.append(f"  {command.description}", style="#8f9baa")
            self.add_option(Option(label, id=command.name))
        self.highlighted = 0 if commands else None
        self.display = bool(commands)

    def selected_name(self) -> str | None:
        option = self.highlighted_option
        return option.id if option and option.id else None

    def move(self, direction: int) -> None:
        if self.option_count == 0:
            return
        current = self.highlighted or 0
        self.highlighted = (current + direction) % self.option_count
