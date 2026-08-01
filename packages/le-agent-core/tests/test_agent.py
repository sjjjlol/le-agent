import asyncio

import pytest
from le_agent_ai import AssistantMessage, AsyncEventStream, FauxProvider, Model, ScriptedResponse
from le_agent_ai.models import StreamEvent
from le_agent_core import Agent, AgentLoopConfig


@pytest.mark.asyncio
async def test_agent_follow_up_runs_after_initial_completion() -> None:
    provider = FauxProvider([ScriptedResponse.text("first"), ScriptedResponse.text("second")])
    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    agent = Agent(AgentLoopConfig(model=model, provider=provider))
    agent.follow_up("then continue")

    await agent.prompt("start")

    assert [request.messages[-1].content[0].text for request in provider.requests] == ["start", "then continue"]
    assert agent.state.messages[-1].text == "second"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_agent_abort_cancels_the_provider_producer() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingProvider:
        async def stream(self, model, context, *, api_key=None, timeout_seconds=None):
            del context, api_key, timeout_seconds
            stream: AsyncEventStream[StreamEvent, AssistantMessage] = AsyncEventStream()

            async def produce() -> None:
                try:
                    stream.push(StreamEvent(type="start"))
                    started.set()
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    stream.finish(
                        AssistantMessage(
                            content=[],
                            provider=model.provider,
                            model=model.id,
                            stop_reason="aborted",
                            error_code="aborted",
                        )
                    )

            stream.attach(asyncio.create_task(produce()))
            return stream

    model = Model(provider="blocking", id="test", context_window=1000, max_output_tokens=100)
    agent = Agent(AgentLoopConfig(model=model, provider=BlockingProvider()))  # type: ignore[arg-type]
    prompt = asyncio.create_task(agent.prompt("start"))
    await asyncio.wait_for(started.wait(), timeout=1)

    agent.abort()
    with pytest.raises(asyncio.CancelledError):
        await prompt
    await asyncio.wait_for(cancelled.wait(), timeout=1)
