"""Deterministic provider used for offline agent-loop tests and examples."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .errors import ErrorCode
from .models import (
    AssistantContent,
    AssistantMessage,
    Model,
    ProviderContext,
    StreamEvent,
    TextContent,
    ToolCallContent,
    Usage,
)
from .stream import AsyncEventStream


@dataclass(frozen=True, slots=True)
class ScriptedResponse:
    text_value: str = ""
    tool_calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    stop_reason: str = "stop"
    error_message: str | None = None
    error_code: ErrorCode | None = None

    @classmethod
    def text(cls, value: str) -> "ScriptedResponse":
        return cls(text_value=value)

    @classmethod
    def tool_call(cls, identifier: str, name: str, arguments: dict[str, Any]) -> "ScriptedResponse":
        return cls(tool_calls=[(identifier, name, arguments)], stop_reason="tool_use")


class FauxProvider:
    def __init__(self, responses: list[ScriptedResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ProviderContext] = []

    async def stream(
        self,
        model: Model,
        context: ProviderContext,
        *,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncEventStream[StreamEvent, AssistantMessage]:
        del api_key, timeout_seconds
        stream: AsyncEventStream[StreamEvent, AssistantMessage] = AsyncEventStream()
        self.requests.append(context)
        response = self._responses.pop(0) if self._responses else ScriptedResponse.text("")

        async def publish() -> None:
            content: list[AssistantContent] = []
            stream.push(StreamEvent(type="start"))
            if response.text_value:
                content.append(TextContent(text=response.text_value))
                stream.push(StreamEvent(type="text_delta", delta=response.text_value))
            for identifier, name, arguments in response.tool_calls:
                content.append(ToolCallContent(id=identifier, name=name, arguments=arguments))
                stream.push(
                    StreamEvent(
                        type="tool_call_delta",
                        tool_call_id=identifier,
                        tool_name=name,
                        delta=str(arguments),
                    )
                )
            stop_reason = "error" if response.error_message else response.stop_reason
            message = AssistantMessage(
                content=content,
                provider=model.provider,
                model=model.id,
                stop_reason=stop_reason,  # type: ignore[arg-type]
                error_message=response.error_message,
                error_code=response.error_code,
                usage=Usage(output_tokens=len(response.text_value) // 4 + 1),
            )
            if response.error_message:
                stream.push(
                    StreamEvent(
                        type="error",
                        error_message=response.error_message,
                        error_code=response.error_code,
                        message=message,
                    )
                )
            stream.push(StreamEvent(type="done", message=message, usage=message.usage))
            stream.finish(message)

        asyncio.create_task(publish())
        return stream
