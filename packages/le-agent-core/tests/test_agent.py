import pytest
from le_agent_ai import FauxProvider, Model, ScriptedResponse
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
