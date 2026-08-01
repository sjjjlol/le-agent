import pytest
from le_agent_ai import FauxProvider, Model, ScriptedResponse
from le_agent_core import AgentLoopConfig
from le_agent_core.compaction import CompactionSettings, estimate_context_tokens, should_compact
from le_agent_core.harness import AgentHarness
from le_agent_core.session import MemorySessionStore, SessionRepository


@pytest.mark.asyncio
async def test_harness_persists_each_completed_message_to_session() -> None:
    repository = SessionRepository(MemorySessionStore())
    session = await repository.create()
    provider = FauxProvider([ScriptedResponse.text("hello")])
    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    harness = AgentHarness(session=session, config=AgentLoopConfig(model=model, provider=provider))

    await harness.prompt("hi")

    assert [message.role for message in await session.build_context_messages()] == ["user", "assistant"]


def test_token_estimate_prefers_last_provider_usage_and_compaction_threshold() -> None:
    assert estimate_context_tokens([]) == 0
    assert should_compact(900, 1000, CompactionSettings(reserve_tokens=128, keep_recent_tokens=100))
