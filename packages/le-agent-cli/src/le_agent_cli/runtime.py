"""Replaceable application runtime shared by interactive commands and the TUI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from le_agent_core.loop import AgentEvent

from .app import AppBundle

RuntimeListener = Callable[[AgentEvent], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    model_name: str | None = None
    resume: str | None = None


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
        await self._replace(RuntimeRequest(model_name=model_name, resume=self.bundle.session.id))

    async def new_session(self) -> None:
        await self._replace(RuntimeRequest(model_name=self.bundle.model_name))

    async def resume(self, identifier: str) -> None:
        await self._replace(RuntimeRequest(model_name=self.bundle.model_name, resume=identifier))

    async def _replace(self, request: RuntimeRequest) -> None:
        if self.bundle.harness.agent:
            await self.bundle.harness.agent.wait_for_idle()
        replacement = await self._factory(request)
        if self._agent_unsubscribe:
            self._agent_unsubscribe()
        self.bundle = replacement
        self._started = False
        self._agent_unsubscribe = None
        await self.start()

    async def _forward(self, event: AgentEvent) -> None:
        for listener in list(self._listeners):
            result = listener(event)
            if isinstance(result, Awaitable):
                await result
