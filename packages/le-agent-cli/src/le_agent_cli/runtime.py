"""Replaceable application runtime shared by interactive commands and the TUI."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from le_agent_core.loop import AgentEvent
from le_agent_core.session import Session

from .app import AppBundle

RuntimeListener = Callable[[AgentEvent], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    model_name: str | None = None
    resume: str | None = None
    session: Session | None = None


RuntimeFactory = Callable[[RuntimeRequest], Awaitable[AppBundle]]


class RuntimeController:
    def __init__(self, initial: AppBundle, factory: RuntimeFactory) -> None:
        self.bundle = initial
        self._factory = factory
        self._listeners: list[RuntimeListener] = []
        self._agent_unsubscribe: Callable[[], None] | None = None
        self._started = False

    def subscribe(self, listener: RuntimeListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            self._listeners.remove(listener)

        return unsubscribe

    async def start(self) -> None:
        if self._started:
            return
        agent = await self.bundle.harness.restore()
        self._agent_unsubscribe = agent.subscribe(self._forward)
        self._started = True

    async def prompt(self, text: str) -> None:
        await self.start()
        await self.bundle.harness.prompt(text)

    def steer(self, text: str) -> None:
        if self.bundle.harness.agent:
            self.bundle.harness.agent.steer(text)

    def follow_up(self, text: str) -> None:
        if self.bundle.harness.agent:
            self.bundle.harness.agent.follow_up(text)

    def abort(self) -> None:
        if self.bundle.harness.agent:
            self.bundle.harness.agent.abort()

    async def switch_model(self, model_name: str) -> None:
        await self._replace(RuntimeRequest(model_name=model_name, session=self.bundle.session))
        model = self.bundle.harness.config.model
        await self.bundle.session.append_model_change(model.provider, model.id)

    async def new_session(self) -> None:
        await self._replace(RuntimeRequest(model_name=self.bundle.model_name))

    async def resume(self, identifier: str) -> None:
        if identifier == self.bundle.session.id:
            return
        await self._replace(RuntimeRequest(model_name=self.bundle.model_name, resume=identifier))

    async def wait_for_idle(self) -> None:
        await self._wait_for_idle()

    async def reload_context(self) -> None:
        """Rebuild AgentState after compaction or pointer navigation and rebind event forwarding."""
        await self._wait_for_idle()
        if self._agent_unsubscribe:
            self._agent_unsubscribe()
        self.bundle.harness.agent = None
        self._started = False
        self._agent_unsubscribe = None
        await self.start()

    async def close(self) -> None:
        await self._wait_for_idle()
        if self._agent_unsubscribe:
            self._agent_unsubscribe()
        self._agent_unsubscribe = None
        self._started = False
        await self.bundle.session.close()

    async def _replace(self, request: RuntimeRequest) -> None:
        await self._wait_for_idle()
        previous = self.bundle
        replacement = await self._factory(request)
        if replacement.session is not previous.session:
            try:
                await previous.session.close()
            except Exception:
                await replacement.session.close()
                raise
        if self._agent_unsubscribe:
            self._agent_unsubscribe()
        self.bundle = replacement
        self._started = False
        self._agent_unsubscribe = None
        await self.start()

    async def _wait_for_idle(self) -> None:
        agent = self.bundle.harness.agent
        if agent is None:
            return
        try:
            await agent.wait_for_idle()
        except asyncio.CancelledError:
            pass

    async def _forward(self, event: AgentEvent) -> None:
        for listener in list(self._listeners):
            result = listener(event)
            if isinstance(result, Awaitable):
                await result
