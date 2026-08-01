import asyncio

import pytest
from le_agent_ai.stream import AsyncEventStream


@pytest.mark.asyncio
async def test_event_stream_yields_events_then_final_result() -> None:
    stream: AsyncEventStream[str, int] = AsyncEventStream()
    stream.push("first")
    stream.push("second")
    stream.finish(2)

    assert [event async for event in stream] == ["first", "second"]
    assert await stream.result() == 2


@pytest.mark.asyncio
async def test_event_stream_waits_for_later_event() -> None:
    stream: AsyncEventStream[str, None] = AsyncEventStream()

    async def publish() -> None:
        await asyncio.sleep(0)
        stream.push("ready")
        stream.finish(None)

    task = asyncio.create_task(publish())
    assert [event async for event in stream] == ["ready"]
    await task
