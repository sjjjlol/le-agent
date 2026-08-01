"""Stateful facade around the low-level agent loop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from le_agent_ai.errors import ErrorCode
from le_agent_ai.models import AgentMessage, AssistantMessage, TextContent, UserMessage
from le_agent_ai.stream import AsyncEventStream

from .loop import AgentContext, AgentEvent, AgentLoopConfig, AgentTool, agent_loop, agent_loop_continue

Listener = Callable[[AgentEvent], Awaitable[None] | None]


@dataclass(slots=True)
class AgentState:
    system_prompt: str = ""
    messages: list[AgentMessage] = field(default_factory=list)
    is_streaming: bool = False
    streaming_message: AssistantMessage | None = None
    error_message: str | None = None
    error_code: ErrorCode | None = None


class Agent:
    def __init__(
        self,
        config: AgentLoopConfig,
        *,
        initial_state: AgentState | None = None,
        tools: list[AgentTool[Any]] | None = None,
    ) -> None:
        self.config = config
        self.state = initial_state or AgentState()
        self._tools = tools or []
        self._listeners: list[Listener] = []
        self._steering: list[UserMessage] = []
        self._follow_up: list[UserMessage] = []
        self._active: asyncio.Task[None] | None = None
        self.config.get_steering_messages = self._drain_steering
        self.config.get_follow_up_messages = self._drain_follow_up

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            self._listeners.remove(listener)

        return unsubscribe

    def steer(self, text: str) -> None:
        self._steering.append(UserMessage(content=[TextContent(text=text)]))

    def follow_up(self, text: str) -> None:
        self._follow_up.append(UserMessage(content=[TextContent(text=text)]))

    def abort(self) -> None:
        if self._active:
            self._active.cancel()

    async def wait_for_idle(self) -> None:
        if self._active:
            await asyncio.shield(self._active)

    async def prompt(self, text: str) -> None:
        if self._active and not self._active.done():
            self.steer(text)
            return
        self._active = asyncio.create_task(self._run_prompt(UserMessage(content=[TextContent(text=text)])))
        await self._active

    async def continue_run(self) -> None:
        if self._active and not self._active.done():
            raise RuntimeError("agent is already running")
        self._active = asyncio.create_task(self._run_continue())
        await self._active

    async def _run_prompt(self, message: UserMessage) -> None:
        context = AgentContext(system_prompt=self.state.system_prompt, messages=self.state.messages, tools=self._tools)
        await self._consume(agent_loop([message], context, self.config))

    async def _run_continue(self) -> None:
        context = AgentContext(system_prompt=self.state.system_prompt, messages=self.state.messages, tools=self._tools)
        await self._consume(agent_loop_continue(context, self.config))

    async def _consume(self, stream: AsyncEventStream[AgentEvent, list[AgentMessage]]) -> None:
        self.state.is_streaming = True
        try:
            async for event in stream:
                if event.type == "message_start" and isinstance(event.message, AssistantMessage):
                    self.state.streaming_message = event.message
                if event.type == "message_end" and isinstance(event.message, AssistantMessage):
                    self.state.streaming_message = None
                    self.state.error_message = event.message.error_message
                    self.state.error_code = event.message.error_code
                for listener in list(self._listeners):
                    result = listener(event)
                    if isinstance(result, Awaitable):
                        await result
            await stream.result()
        except asyncio.CancelledError:
            await stream.cancel()
            try:
                await stream.result()
            except asyncio.CancelledError:
                pass
            raise
        finally:
            self.state.is_streaming = False
            self.state.streaming_message = None

    async def _drain_steering(self) -> list[UserMessage]:
        messages, self._steering = self._steering, []
        return messages

    async def _drain_follow_up(self) -> list[UserMessage]:
        messages, self._follow_up = self._follow_up, []
        return messages
