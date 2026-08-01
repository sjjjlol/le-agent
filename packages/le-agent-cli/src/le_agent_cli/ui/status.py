"""One-line model, permission and session status."""

from __future__ import annotations

from le_agent_core.compaction import estimate_context_tokens
from textual.widgets import Static

from ..app import AppBundle


def context_usage(bundle: AppBundle) -> tuple[int, int, int]:
    agent = bundle.harness.agent
    messages = agent.state.messages if agent else []
    used = estimate_context_tokens(messages)
    window = bundle.harness.config.model.context_window
    percent = min(100, round(used * 100 / window)) if window else 0
    return used, window, percent


class StatusBar(Static):
    def refresh_bundle(self, bundle: AppBundle, *, queue_size: int = 0) -> None:
        used, window, percent = context_usage(bundle)
        self.update(
            f"le-agent  ◆ {bundle.model_name}  ·  {bundle.policy.mode.value}  ·  "
            f"ctx {percent}% ({used}/{window})  ·  session {bundle.session.id[:8]}  ·  queue {queue_size}"
        )
