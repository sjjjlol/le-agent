"""Provider protocol shared by concrete API adapters and deterministic tests."""

from __future__ import annotations

from typing import Protocol

from .models import AssistantMessage, Model, ProviderContext, StreamEvent
from .stream import AsyncEventStream


class Provider(Protocol):
    async def stream(
        self,
        model: Model,
        context: ProviderContext,
        *,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncEventStream[StreamEvent, AssistantMessage]: ...
