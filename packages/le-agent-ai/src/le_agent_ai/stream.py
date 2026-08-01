"""An async event stream with a separately awaitable final result."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Generic, TypeVar

TEvent = TypeVar("TEvent")
TResult = TypeVar("TResult")


class AsyncEventStream(Generic[TEvent, TResult]):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[TEvent | object] = asyncio.Queue()
        self._end = object()
        self._result: asyncio.Future[TResult] = asyncio.get_running_loop().create_future()
        self._finished = False
        self._producer: asyncio.Task[Any] | None = None

    def attach(self, producer: asyncio.Task[Any]) -> None:
        """Attach the producer task so cancellation propagates through stream layers."""
        if self._producer is not None:
            raise RuntimeError("event stream already has a producer")
        self._producer = producer

    async def cancel(self) -> None:
        producer = self._producer
        if producer is None or producer is asyncio.current_task():
            return
        producer.cancel()
        try:
            await producer
        except asyncio.CancelledError:
            pass

    def push(self, event: TEvent) -> None:
        if self._finished:
            raise RuntimeError("cannot push an event after stream completion")
        self._queue.put_nowait(event)

    def finish(self, result: TResult) -> None:
        if self._finished:
            return
        self._finished = True
        self._result.set_result(result)
        self._queue.put_nowait(self._end)

    def fail(self, error: BaseException) -> None:
        if self._finished:
            return
        self._finished = True
        self._result.set_exception(error)
        self._queue.put_nowait(self._end)

    async def result(self) -> TResult:
        return await self._result

    def __aiter__(self) -> AsyncIterator[TEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[TEvent]:
        while True:
            item = await self._queue.get()
            if item is self._end:
                return
            yield item  # type: ignore[misc]
