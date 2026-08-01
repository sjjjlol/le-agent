"""Minimal event-driven tool-calling loop modeled after pi-agent-core."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from le_agent_ai.models import (
    AgentMessage,
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    Model,
    ProviderContext,
    TextContent,
    ToolCallContent,
    ToolDefinition,
    ToolResultMessage,
    UserMessage,
)
from le_agent_ai.provider import Provider
from le_agent_ai.stream import AsyncEventStream
from pydantic import BaseModel

TArgs = TypeVar("TArgs", bound=BaseModel)
ToolUpdate = Callable[[str], Awaitable[None] | None]
AgentListener = Callable[["AgentEvent"], Awaitable[None] | None]


@dataclass(slots=True)
class ToolResult:
    content: list[TextContent]
    is_error: bool = False
    terminate: bool = False
    details: Any = None

    @classmethod
    def text(cls, value: str, *, is_error: bool = False, terminate: bool = False) -> "ToolResult":
        return cls(content=[TextContent(text=value)], is_error=is_error, terminate=terminate)


class AgentTool(Generic[TArgs]):
    name: str
    description: str
    args_model: type[TArgs]
    execution_mode: Literal["parallel", "sequential"] = "parallel"

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.args_model.model_json_schema(),
        )

    async def execute(self, args: TArgs, *, on_update: ToolUpdate | None = None) -> ToolResult | str:
        raise NotImplementedError


@dataclass(slots=True)
class AgentContext:
    system_prompt: str
    messages: list[AgentMessage]
    tools: list[AgentTool[Any]]


@dataclass(slots=True)
class AgentEvent:
    type: Literal[
        "agent_start",
        "agent_end",
        "turn_start",
        "turn_end",
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
    ]
    message: AgentMessage | None = None
    assistant_event: Any = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_result: ToolResultMessage | None = None
    tool_results: list[ToolResultMessage] = field(default_factory=list)
    messages: list[AgentMessage] | None = None


TransformContext = Callable[[list[AgentMessage]], Awaitable[list[AgentMessage]] | list[AgentMessage]]
ConvertToLlm = Callable[[list[AgentMessage]], Awaitable[list[Any]] | list[Any]]
BeforeToolCall = Callable[[ToolCallContent, BaseModel, AgentContext], Awaitable[str | None] | str | None]
AfterToolCall = Callable[[ToolCallContent, ToolResult], Awaitable[ToolResult] | ToolResult]
PrepareNextTurn = Callable[[AgentContext], Awaitable[None] | None]
ShouldStopAfterTurn = Callable[[AgentContext, AssistantMessage, list[ToolResultMessage]], Awaitable[bool] | bool]
MessageSupplier = Callable[[], Awaitable[list[UserMessage]] | list[UserMessage]]


@dataclass(slots=True)
class AgentLoopConfig:
    model: Model
    provider: Provider
    transform_context: TransformContext | None = None
    convert_to_llm: ConvertToLlm | None = None
    before_tool_call: BeforeToolCall | None = None
    after_tool_call: AfterToolCall | None = None
    prepare_next_turn: PrepareNextTurn | None = None
    should_stop_after_turn: ShouldStopAfterTurn | None = None
    get_steering_messages: MessageSupplier | None = None
    get_follow_up_messages: MessageSupplier | None = None
    tool_execution: Literal["parallel", "sequential"] = "parallel"
    api_key: str | None = None


async def _maybe(value: Awaitable[Any] | Any) -> Any:
    return await value if isinstance(value, Awaitable) else value


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Any]:
    converted: list[Any] = []
    for message in messages:
        if isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage)):
            converted.append(message)
        elif isinstance(message, CompactionSummaryMessage):
            converted.append(UserMessage(content=[TextContent(text=f"<summary>\n{message.summary}\n</summary>")]))
        elif isinstance(message, BranchSummaryMessage):
            converted.append(
                UserMessage(content=[TextContent(text=f"<branch-summary>\n{message.summary}\n</branch-summary>")])
            )
        else:
            converted.append(UserMessage(content=[TextContent(text=message.content)]))
    return converted


def agent_loop(
    prompts: list[AgentMessage], context: AgentContext, config: AgentLoopConfig
) -> AsyncEventStream[AgentEvent, list[AgentMessage]]:
    stream: AsyncEventStream[AgentEvent, list[AgentMessage]] = AsyncEventStream()
    asyncio.create_task(_run(prompts, context, config, stream, add_prompts=True))
    return stream


def agent_loop_continue(
    context: AgentContext, config: AgentLoopConfig
) -> AsyncEventStream[AgentEvent, list[AgentMessage]]:
    if not context.messages:
        raise ValueError("cannot continue an empty context")
    if isinstance(context.messages[-1], AssistantMessage):
        raise ValueError("cannot continue after an assistant message")
    stream: AsyncEventStream[AgentEvent, list[AgentMessage]] = AsyncEventStream()
    asyncio.create_task(_run([], context, config, stream, add_prompts=False))
    return stream


async def _emit(stream: AsyncEventStream[AgentEvent, list[AgentMessage]], event: AgentEvent) -> None:
    stream.push(event)


async def _run(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    stream: AsyncEventStream[AgentEvent, list[AgentMessage]],
    *,
    add_prompts: bool,
) -> None:
    new_messages: list[AgentMessage] = []
    try:
        await _emit(stream, AgentEvent(type="agent_start"))
        await _emit(stream, AgentEvent(type="turn_start"))
        if add_prompts:
            for prompt in prompts:
                context.messages.append(prompt)
                new_messages.append(prompt)
                await _emit(stream, AgentEvent(type="message_start", message=prompt))
                await _emit(stream, AgentEvent(type="message_end", message=prompt))
        first_turn = True
        pending: list[UserMessage] = []
        while True:
            has_tools = True
            while has_tools or pending:
                if not first_turn:
                    await _emit(stream, AgentEvent(type="turn_start"))
                first_turn = False
                for message in pending:
                    context.messages.append(message)
                    new_messages.append(message)
                    await _emit(stream, AgentEvent(type="message_start", message=message))
                    await _emit(stream, AgentEvent(type="message_end", message=message))
                pending = []

                assistant = await _stream_assistant(context, config, stream)
                new_messages.append(assistant)
                if assistant.stop_reason in {"error", "aborted"}:
                    await _emit(stream, AgentEvent(type="turn_end", message=assistant))
                    await _emit(stream, AgentEvent(type="agent_end", messages=new_messages))
                    stream.finish(new_messages)
                    return
                calls = [block for block in assistant.content if isinstance(block, ToolCallContent)]
                tool_results, terminate = await _execute_calls(calls, context, config, stream)
                context.messages.extend(tool_results)
                new_messages.extend(tool_results)
                for result in tool_results:
                    await _emit(stream, AgentEvent(type="message_start", message=result))
                    await _emit(stream, AgentEvent(type="message_end", message=result))
                await _emit(stream, AgentEvent(type="turn_end", message=assistant, tool_results=tool_results))
                if config.prepare_next_turn:
                    await _maybe(config.prepare_next_turn(context))
                should_stop = config.should_stop_after_turn and await _maybe(
                    config.should_stop_after_turn(context, assistant, tool_results)
                )
                if should_stop:
                    await _emit(stream, AgentEvent(type="agent_end", messages=new_messages))
                    stream.finish(new_messages)
                    return
                has_tools = bool(calls) and not terminate
                if config.get_steering_messages:
                    pending = await _maybe(config.get_steering_messages())
            if not config.get_follow_up_messages:
                break
            pending = await _maybe(config.get_follow_up_messages())
            if not pending:
                break
        await _emit(stream, AgentEvent(type="agent_end", messages=new_messages))
        stream.finish(new_messages)
    except asyncio.CancelledError:
        stream.fail(asyncio.CancelledError())
        raise
    except Exception as error:
        stream.fail(error)


async def _stream_assistant(
    context: AgentContext, config: AgentLoopConfig, stream: AsyncEventStream[AgentEvent, list[AgentMessage]]
) -> AssistantMessage:
    messages = context.messages
    if config.transform_context:
        messages = await _maybe(config.transform_context(messages))
    converter = config.convert_to_llm or default_convert_to_llm
    converted = await _maybe(converter(messages))
    provider_context = ProviderContext(
        system_prompt=context.system_prompt,
        messages=converted,
        tools=[tool.definition() for tool in context.tools],
    )
    provider_stream = await config.provider.stream(config.model, provider_context, api_key=config.api_key)
    started = False
    async for provider_event in provider_stream:
        if provider_event.type == "start":
            started = True
            await _emit(stream, AgentEvent(type="message_start"))
        elif provider_event.type not in {"done", "usage"}:
            await _emit(stream, AgentEvent(type="message_update", assistant_event=provider_event))
    assistant = await provider_stream.result()
    if not started:
        await _emit(stream, AgentEvent(type="message_start", message=assistant))
    context.messages.append(assistant)
    await _emit(stream, AgentEvent(type="message_end", message=assistant))
    return assistant


async def _execute_calls(
    calls: list[ToolCallContent],
    context: AgentContext,
    config: AgentLoopConfig,
    stream: AsyncEventStream[AgentEvent, list[AgentMessage]],
) -> tuple[list[ToolResultMessage], bool]:
    if not calls:
        return [], False
    by_name = {tool.name: tool for tool in context.tools}
    sequential = config.tool_execution == "sequential" or any(
        by_name.get(call.name, None) is not None and by_name[call.name].execution_mode == "sequential" for call in calls
    )

    async def execute(call: ToolCallContent) -> tuple[ToolResultMessage, bool]:
        tool = by_name.get(call.name)
        await _emit(stream, AgentEvent(type="tool_execution_start", tool_call_id=call.id, tool_name=call.name))
        if tool is None:
            result = ToolResult.text(f"unknown tool: {call.name}", is_error=True)
        else:
            try:
                args = tool.args_model.model_validate(call.arguments)
                if config.before_tool_call:
                    reason = await _maybe(config.before_tool_call(call, args, context))
                    if reason:
                        result = ToolResult.text(reason, is_error=True)
                    else:
                        result = await _tool_execute(tool, args, stream, call)
                else:
                    result = await _tool_execute(tool, args, stream, call)
                if config.after_tool_call:
                    result = await _maybe(config.after_tool_call(call, result))
            except Exception as error:
                result = ToolResult.text(str(error), is_error=True)
        message = ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=result.content,
            is_error=result.is_error,
            timestamp=time.time(),
        )
        await _emit(
            stream,
            AgentEvent(type="tool_execution_end", tool_call_id=call.id, tool_name=call.name, tool_result=message),
        )
        return message, result.terminate

    if sequential:
        executed = [await execute(call) for call in calls]
    else:
        executed = list(await asyncio.gather(*(execute(call) for call in calls)))
    return [message for message, _ in executed], bool(executed) and all(terminate for _, terminate in executed)


async def _tool_execute(
    tool: AgentTool[Any],
    args: BaseModel,
    stream: AsyncEventStream[AgentEvent, list[AgentMessage]],
    call: ToolCallContent,
) -> ToolResult:
    async def update(text: str) -> None:
        await _emit(
            stream,
            AgentEvent(type="tool_execution_update", tool_call_id=call.id, tool_name=call.name, assistant_event=text),
        )

    raw = await tool.execute(args, on_update=update)
    return raw if isinstance(raw, ToolResult) else ToolResult.text(raw)
