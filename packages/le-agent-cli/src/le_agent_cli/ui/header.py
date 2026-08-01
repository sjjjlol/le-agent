"""Persistent compact le-agent brand and session header."""

from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text
from textual.widgets import Static

from ..app import AppBundle


class BrandHeader(Static):
    def __init__(self, bundle: AppBundle, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.bundle = bundle
        self.renderable = Text()

    def on_mount(self) -> None:
        self.refresh_bundle(self.bundle)

    def on_resize(self) -> None:
        self.refresh_bundle(self.bundle)

    def refresh_bundle(self, bundle: AppBundle) -> None:
        self.bundle = bundle
        width = self.size.width or self.app.size.width
        session = bundle.session_name or bundle.session.id[:8]
        if width <= 48:
            self.styles.height = 1
            prefix = "◆ le-agent · "
            available = max(1, width - cell_len(prefix))
            if cell_len(session) > available:
                while session and cell_len(session) > available - 1:
                    session = session[:-1]
                session += "…"
            rendered = Text.assemble(("◆ ", "bold #88c0d0"), ("le-agent", "bold"), (f" · {session}", "#6f7a89"))
        else:
            self.styles.height = 2
            rendered = Text.assemble(
                ("◆ le-agent", "bold #88c0d0"),
                ("  Python coding agent\n", "#6f7a89"),
                (f"  session {session}", "#8f9baa"),
                (f" · {bundle.model_name}", "#6f7a89"),
            )
        self.renderable = rendered
        self.update(rendered)
