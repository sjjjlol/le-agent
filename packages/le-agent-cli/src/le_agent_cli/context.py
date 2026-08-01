"""Shared context-window accounting for commands and terminal status."""

from __future__ import annotations

from dataclasses import dataclass

from le_agent_core.compaction import estimate_context_tokens

from .app import AppBundle


@dataclass(frozen=True, slots=True)
class ContextUsage:
    used_tokens: int
    context_window: int
    percent: int


def context_usage(bundle: AppBundle) -> ContextUsage:
    agent = bundle.harness.agent
    messages = agent.state.messages if agent else []
    used = estimate_context_tokens(messages)
    window = bundle.harness.config.model.context_window
    percent = min(100, round(used * 100 / window)) if window else 0
    return ContextUsage(used_tokens=used, context_window=window, percent=percent)


def format_context_usage(usage: ContextUsage) -> str:
    return (
        f"上下文：{usage.percent}% · {usage.used_tokens:,} / "
        f"{usage.context_window:,} tokens"
    )
