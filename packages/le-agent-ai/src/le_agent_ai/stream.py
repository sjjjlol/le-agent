"""An async event stream with a separately awaitable final result."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Generic, TypeVar

TEvent = TypeVar("TEvent")
TResult = TypeVar("TResult")


class AsyncEventStream(Generic[TEvent, TResult]):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[TEvent | object] = asyncio.Queue()
        self._end = object()
        self._result: asyncio.Future[TResult] = asyncio.get_running_loop().create_future()
        self._finished = False

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
