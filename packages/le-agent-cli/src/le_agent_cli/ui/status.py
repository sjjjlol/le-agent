"""One-line model, permission and session status."""

from __future__ import annotations

from textual.widgets import Static

from ..app import AppBundle


class StatusBar(Static):
    def refresh_bundle(self, bundle: AppBundle, *, queue_size: int = 0) -> None:
        self.update(
            f"le-agent  ◆ {bundle.model_name}  ·  {bundle.policy.mode.value}  ·  "
            f"session {bundle.session.id[:8]}  ·  queue {queue_size}"
        )
