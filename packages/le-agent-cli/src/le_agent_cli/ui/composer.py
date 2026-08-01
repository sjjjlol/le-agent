"""Multiline composer with explicit steer and follow-up submission modes."""

from __future__ import annotations

from typing import Literal

from textual import events
from textual.message import Message
from textual.widgets import TextArea

Delivery = Literal["steer", "follow_up"]


class Composer(TextArea):
    class Submitted(Message):
        def __init__(self, text: str, delivery: Delivery) -> None:
            super().__init__()
            self.text = text
            self.delivery = delivery

    class CompletionRequested(Message):
        def __init__(self, *, execute_if_exact: bool = False) -> None:
            super().__init__()
            self.execute_if_exact = execute_if_exact

    class CompletionMoved(Message):
        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction

    class RecallRequested(Message):
        pass

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "shift+enter":
            self.insert("\n")
        elif key == "alt+enter":
            self._submit("follow_up")
        elif key == "enter" and self.text.lstrip().startswith("/"):
            self.post_message(self.CompletionRequested(execute_if_exact=True))
        elif key == "enter":
            self._submit("steer")
        elif key == "tab" and self.text.lstrip().startswith("/"):
            self.post_message(self.CompletionRequested())
        elif key in {"up", "down"} and self.text.lstrip().startswith("/"):
            self.post_message(self.CompletionMoved(-1 if key == "up" else 1))
        elif key == "alt+up":
            self.post_message(self.RecallRequested())
        else:
            return
        event.prevent_default()
        event.stop()

    def _submit(self, delivery: Delivery) -> None:
        value = self.text.strip()
        if not value:
            return
        self.text = ""
        self.post_message(self.Submitted(value, delivery))
