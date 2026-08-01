"""Concrete provider adapters and lossless canonical-message conversion."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from .models import (
    AssistantContent,
    AssistantMessage,
    Model,
    ProviderContext,
    StreamEvent,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from .stream import AsyncEventStream


def _text(blocks: list[TextContent]) -> str:
    return "".join(block.text for block in blocks)


def openai_messages(context: ProviderContext) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if context.system_prompt:
        messages.append({"role": "system", "content": context.system_prompt})
    for message in context.messages:
        if isinstance(message, UserMessage):
            messages.append({"role": "user", "content": _text(message.content)})
        elif isinstance(message, AssistantMessage):
            text = message.text or None
            tool_calls = [
                {
                    "id": block.id,
                    "type": "function",
                    "function": {"name": block.name, "arguments": json.dumps(block.arguments)},
                }
                for block in message.content
                if isinstance(block, ToolCallContent)
            ]
            payload: dict[str, Any] = {"role": "assistant", "content": text}
            if tool_calls:
                payload["tool_calls"] = tool_calls
            messages.append(payload)
        elif isinstance(message, ToolResultMessage):
            messages.append({"role": "tool", "tool_call_id": message.tool_call_id, "content": _text(message.content)})
    return messages


def anthropic_messages(context: ProviderContext) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for message in context.messages:
        if isinstance(message, UserMessage):
            messages.append({"role": "user", "content": _text(message.content)})
        elif isinstance(message, AssistantMessage):
            content: list[dict[str, Any]] = []
            if message.text:
                content.append({"type": "text", "text": message.text})
            for block in message.content:
                if isinstance(block, ToolCallContent):
                    content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.arguments})
            messages.append({"role": "assistant", "content": content})
        elif isinstance(message, ToolResultMessage):
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": _text(message.content),
                            "is_error": message.is_error,
                        }
                    ],
                }
            )
    return messages


def _openai_tools(context: ProviderContext) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in context.tools
    ]


def _anthropic_tools(context: ProviderContext) -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        for tool in context.tools
    ]


class OpenAICompatibleProvider:
    """Chat Completions adapter suitable for OpenAI-compatible endpoints."""

    def __init__(self, *, base_url: str | None = None) -> None:
        self._base_url = base_url

    async def stream(
        self,
        model: Model,
        context: ProviderContext,
        *,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncEventStream[StreamEvent, AssistantMessage]:
        stream: AsyncEventStream[StreamEvent, AssistantMessage] = AsyncEventStream()

        async def run() -> None:
            try:
                client = AsyncOpenAI(
                    api_key=api_key,
                    base_url=model.base_url or self._base_url,
                    timeout=timeout_seconds,
                )
                response = await client.chat.completions.create(  # type: ignore[call-overload]
                    model=model.id,
                    messages=openai_messages(context),
                    tools=_openai_tools(context) or None,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                content: list[AssistantContent] = []
                tool_parts: dict[int, dict[str, str]] = {}
                usage = Usage()
                finish_reason = "stop"
                stream.push(StreamEvent(type="start"))
                async for chunk in response:
                    if chunk.usage:
                        usage = Usage(
                            input_tokens=chunk.usage.prompt_tokens or 0,
                            output_tokens=chunk.usage.completion_tokens or 0,
                        )
                        stream.push(StreamEvent(type="usage", usage=usage))
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish_reason = "tool_use" if choice.finish_reason == "tool_calls" else choice.finish_reason
                    delta = choice.delta
                    if delta.content:
                        content.append(TextContent(text=delta.content))
                        stream.push(StreamEvent(type="text_delta", delta=delta.content))
                    for tool in delta.tool_calls or []:
                        index = tool.index
                        part = tool_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if tool.id:
                            part["id"] = tool.id
                        if tool.function and tool.function.name:
                            part["name"] = tool.function.name
                        if tool.function and tool.function.arguments:
                            part["arguments"] += tool.function.arguments
                            stream.push(
                                StreamEvent(
                                    type="tool_call_delta",
                                    tool_call_id=part["id"] or None,
                                    tool_name=part["name"] or None,
                                    delta=tool.function.arguments,
                                )
                            )
                for part in tool_parts.values():
                    try:
                        arguments = json.loads(part["arguments"] or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    content.append(ToolCallContent(id=part["id"], name=part["name"], arguments=arguments))
                message = AssistantMessage(
                    content=content,
                    provider=model.provider,
                    model=model.id,
                    stop_reason=cast(
                        Any,
                        finish_reason if finish_reason in {"stop", "tool_use", "length"} else "stop",
                    ),
                    usage=usage,
                )
                stream.push(StreamEvent(type="done", message=message, usage=usage))
                stream.finish(message)
            except asyncio.CancelledError:
                message = AssistantMessage(content=[], provider=model.provider, model=model.id, stop_reason="aborted")
                stream.push(StreamEvent(type="error", error_message="request aborted", message=message))
                stream.finish(message)
            except Exception as error:  # provider failures are data, not loop exceptions
                message = AssistantMessage(
                    content=[], provider=model.provider, model=model.id, stop_reason="error", error_message=str(error)
                )
                stream.push(StreamEvent(type="error", error_message=str(error), message=message))
                stream.finish(message)

        asyncio.create_task(run())
        return stream


class AnthropicProvider:
    def __init__(self, *, base_url: str | None = None) -> None:
        self._base_url = base_url

    async def stream(
        self,
        model: Model,
        context: ProviderContext,
        *,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncEventStream[StreamEvent, AssistantMessage]:
        stream: AsyncEventStream[StreamEvent, AssistantMessage] = AsyncEventStream()

        async def run() -> None:
            try:
                client = AsyncAnthropic(
                    api_key=api_key,
                    base_url=model.base_url or self._base_url,
                    timeout=timeout_seconds,
                )
                response = await client.messages.create(
                    model=model.id,
                    max_tokens=model.max_output_tokens,
                    system=context.system_prompt,
                    messages=anthropic_messages(context),  # type: ignore[arg-type]
                    tools=_anthropic_tools(context) or None,  # type: ignore[arg-type]
                    stream=True,
                )
                content: list[AssistantContent] = []
                tool_blocks: dict[int, dict[str, Any]] = {}
                usage = Usage()
                stop_reason = "stop"
                stream.push(StreamEvent(type="start"))
                async for event in response:  # type: ignore[union-attr]
                    event_type = getattr(event, "type", "")
                    if event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        text = getattr(delta, "text", None)
                        partial_json = getattr(delta, "partial_json", None)
                        if text:
                            content.append(TextContent(text=text))
                            stream.push(StreamEvent(type="text_delta", delta=text))
                        if partial_json:
                            index = getattr(event, "index", 0)
                            part = tool_blocks.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            part["arguments"] += partial_json
                            stream.push(
                                StreamEvent(
                                    type="tool_call_delta",
                                    tool_call_id=part["id"] or None,
                                    tool_name=part["name"] or None,
                                    delta=partial_json,
                                )
                            )
                    elif event_type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if getattr(block, "type", None) == "tool_use":
                            tool_blocks[getattr(event, "index", 0)] = {
                                "id": getattr(block, "id", ""),
                                "name": getattr(block, "name", ""),
                                "arguments": "",
                            }
                    elif event_type == "message_delta":
                        delta = getattr(event, "delta", None)
                        if getattr(delta, "stop_reason", None) == "tool_use":
                            stop_reason = "tool_use"
                        raw_usage = getattr(event, "usage", None)
                        if raw_usage:
                            usage = Usage(
                                input_tokens=getattr(raw_usage, "input_tokens", usage.input_tokens),
                                output_tokens=getattr(raw_usage, "output_tokens", 0),
                            )
                    elif event_type == "message_start":
                        message = getattr(event, "message", None)
                        raw_usage = getattr(message, "usage", None)
                        if raw_usage:
                            usage = Usage(input_tokens=getattr(raw_usage, "input_tokens", 0), output_tokens=0)
                for part in tool_blocks.values():
                    try:
                        arguments = json.loads(part["arguments"] or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    content.append(ToolCallContent(id=part["id"], name=part["name"], arguments=arguments))
                message = AssistantMessage(
                    content=content,
                    provider=model.provider,
                    model=model.id,
                    stop_reason=cast(Any, stop_reason),
                    usage=usage,
                )
                stream.push(StreamEvent(type="done", message=message, usage=usage))
                stream.finish(message)
            except asyncio.CancelledError:
                message = AssistantMessage(content=[], provider=model.provider, model=model.id, stop_reason="aborted")
                stream.push(StreamEvent(type="error", error_message="request aborted", message=message))
                stream.finish(message)
            except Exception as error:
                message = AssistantMessage(
                    content=[], provider=model.provider, model=model.id, stop_reason="error", error_message=str(error)
                )
                stream.push(StreamEvent(type="error", error_message=str(error), message=message))
                stream.finish(message)

        asyncio.create_task(run())
        return stream
