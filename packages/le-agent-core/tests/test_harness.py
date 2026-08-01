import pytest
from le_agent_ai import FauxProvider, Model, ScriptedResponse, TextContent, UserMessage
from le_agent_core import AgentLoopConfig
from le_agent_core.compaction import CompactionSettings, SummaryRequest, estimate_context_tokens, should_compact
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


@pytest.mark.asyncio
async def test_harness_direct_navigation_only_moves_the_session_pointer() -> None:
    repository = SessionRepository(MemorySessionStore())
    session = await repository.create()
    root = await session.append_message(UserMessage(content=[TextContent(text="root")]))
    abandoned = await session.append_message(UserMessage(content=[TextContent(text="abandoned")]))
    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    harness = AgentHarness(
        session=session,
        config=AgentLoopConfig(model=model, provider=FauxProvider([])),
    )
    await harness.restore()
    entries_before = await session.entries()

    result = await harness.navigate_tree(root)

    assert result.old_leaf_id == abandoned
    assert result.new_leaf_id == root
    assert result.common_ancestor_id == root
    assert result.summary_entry_id is None
    assert await session.entries() == entries_before
    assert await session.leaf_id() == root
    assert harness.agent is None


@pytest.mark.asyncio
async def test_harness_summary_navigation_is_atomic_and_appends_summary_under_target() -> None:
    repository = SessionRepository(MemorySessionStore())
    session = await repository.create()
    root = await session.append_message(UserMessage(content=[TextContent(text="root")]))
    await session.append_message(UserMessage(content=[TextContent(text="old one")]))
    old_leaf = await session.append_message(UserMessage(content=[TextContent(text="old two")]))
    await session.move_to(root)
    target = await session.append_message(UserMessage(content=[TextContent(text="target")]))
    await session.move_to(old_leaf)
    seen: list[str] = []

    async def summarize(request: SummaryRequest) -> str:
        assert request.kind == "branch"
        assert request.custom_instructions == "保留实现结论"
        seen.extend(message.content[0].text for message in request.messages if isinstance(message, UserMessage))
        return "旧分支总结"

    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    harness = AgentHarness(
        session=session,
        config=AgentLoopConfig(model=model, provider=FauxProvider([])),
        summarizer=summarize,
    )

    result = await harness.navigate_tree(target, summarize=True, instructions="保留实现结论")

    assert seen == ["old one", "old two"]
    assert result.old_leaf_id == old_leaf
    assert result.common_ancestor_id == root
    assert result.summary_entry_id is not None
    assert result.new_leaf_id == result.summary_entry_id
    summary_entry = await session.get_entry(result.summary_entry_id)
    assert summary_entry.parent_id == target
    assert summary_entry.payload == {"summary": "旧分支总结", "from_id": old_leaf}
    assert [message.role for message in await session.build_context_messages()] == ["user", "user", "branch_summary"]


@pytest.mark.asyncio
async def test_harness_summary_failure_keeps_leaf_and_history_unchanged() -> None:
    repository = SessionRepository(MemorySessionStore())
    session = await repository.create()
    root = await session.append_message(UserMessage(content=[TextContent(text="root")]))
    old_leaf = await session.append_message(UserMessage(content=[TextContent(text="old")]))
    await session.move_to(root)
    target = await session.append_message(UserMessage(content=[TextContent(text="target")]))
    await session.move_to(old_leaf)
    entries_before = await session.entries()

    async def fail(_request: SummaryRequest) -> str:
        raise RuntimeError("summary failed")

    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    harness = AgentHarness(
        session=session,
        config=AgentLoopConfig(model=model, provider=FauxProvider([])),
        summarizer=fail,
    )

    with pytest.raises(RuntimeError, match="summary failed"):
        await harness.navigate_tree(target, summarize=True)

    assert await session.leaf_id() == old_leaf
    assert await session.entries() == entries_before


def test_token_estimate_prefers_last_provider_usage_and_compaction_threshold() -> None:
    assert estimate_context_tokens([]) == 0
    assert should_compact(900, 1000, CompactionSettings(reserve_tokens=128, keep_recent_tokens=100))
