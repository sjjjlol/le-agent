"""One-line model, permission and session status."""

from __future__ import annotations

from textual.widgets import Static

from ..app import AppBundle
from ..context import ContextUsage, context_usage

__all__ = ["ContextUsage", "StatusBar", "context_usage"]


class StatusBar(Static):
    def refresh_bundle(self, bundle: AppBundle, *, queue_size: int = 0) -> None:
        usage = context_usage(bundle)
        self.update(
            f"le-agent  ◆ {bundle.model_name}  ·  {bundle.policy.mode.value}  ·  "
            f"ctx {usage.percent}% ({usage.used_tokens:,}/{usage.context_window:,})  ·  "
            f"session {bundle.session.id[:8]}  ·  queue {queue_size}"
        )
