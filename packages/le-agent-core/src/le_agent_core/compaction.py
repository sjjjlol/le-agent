"""Pi-style context estimation and append-only compaction preparation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from le_agent_ai.models import (
    AgentMessage,
    AssistantMessage,
    CompactionSummaryMessage,
    ToolResultMessage,
    UserMessage,
)

from .session import Session


@dataclass(frozen=True, slots=True)
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: int = 16_384
    keep_recent_tokens: int = 20_000


def estimate_message_tokens(message: AgentMessage) -> int:
    if isinstance(message, UserMessage):
        characters = sum(len(part.text) for part in message.content)
    elif isinstance(message, AssistantMessage):
        characters = sum(
            len(getattr(part, "text", "")) + len(getattr(part, "thinking", "")) for part in message.content
        )
        characters += sum(len(str(getattr(part, "arguments", ""))) for part in message.content)
    elif isinstance(message, ToolResultMessage):
        characters = sum(len(part.text) for part in message.content)
    elif isinstance(message, CompactionSummaryMessage):
        characters = len(message.summary)
    else:
        characters = len(getattr(message, "summary", getattr(message, "content", "")))
    return max(1, (characters + 3) // 4)


def estimate_context_tokens(messages: list[AgentMessage]) -> int:
    """Use the latest valid provider usage, then add a conservative trailing estimate."""
    last_usage_index: int | None = None
    usage_tokens = 0
    for index, message in enumerate(messages):
        if isinstance(message, AssistantMessage) and message.stop_reason not in {"error", "aborted"}:
            total = message.usage.total_tokens
            if total > 0:
                last_usage_index = index
                usage_tokens = total
    if last_usage_index is None:
        return sum(estimate_message_tokens(message) for message in messages)
    return usage_tokens + sum(estimate_message_tokens(message) for message in messages[last_usage_index + 1 :])


def should_compact(context_tokens: int, context_window: int, settings: CompactionSettings) -> bool:
    return settings.enabled and context_tokens > context_window - settings.reserve_tokens


Summarizer = Callable[[list[AgentMessage], str | None], Awaitable[str]]


async def compact_session(session: Session, settings: CompactionSettings, summarizer: Summarizer) -> bool:
    messages = await session.build_context_messages()
    tokens_before = estimate_context_tokens(messages)
    retained: list[AgentMessage] = []
    retained_tokens = 0
    for message in reversed(messages):
        tokens = estimate_message_tokens(message)
        if retained and retained_tokens + tokens > settings.keep_recent_tokens:
            break
        retained.insert(0, message)
        retained_tokens += tokens
    history = messages[: len(messages) - len(retained)] if retained else messages
    if not history:
        return False
    previous_summary = next(
        (message.summary for message in messages if isinstance(message, CompactionSummaryMessage)),
        None,
    )
    summary = await summarizer(history, previous_summary)
    first_kept = await session.entry_id_for_message(retained[0]) if retained else None
    await session.append_compaction(
        summary, first_kept_entry_id=first_kept, tokens_before=tokens_before, retained_tail=retained
    )
    return True
