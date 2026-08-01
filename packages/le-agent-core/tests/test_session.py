from pathlib import Path

import pytest
from le_agent_ai import TextContent, UserMessage
from le_agent_core.session import JsonlSessionStore, MemorySessionStore, SessionRepository


@pytest.mark.asyncio
async def test_session_leaf_navigation_creates_a_branch_without_rewriting_history() -> None:
    repository = SessionRepository(MemorySessionStore())
    session = await repository.create()
    first = await session.append_message(UserMessage(content=[TextContent(text="first")]))
    second = await session.append_message(UserMessage(content=[TextContent(text="second")]))
    await session.move_to(first)
    await session.append_message(UserMessage(content=[TextContent(text="branch")]))

    assert [message.content[0].text for message in await session.build_context_messages()] == ["first", "branch"]
    assert len(await session.entries()) == 4  # two messages, leaf movement, branch message
    assert second in {entry.id for entry in await session.entries()}


@pytest.mark.asyncio
async def test_compaction_keeps_history_in_log_but_replaces_context_with_summary_and_tail() -> None:
    repository = SessionRepository(MemorySessionStore())
    session = await repository.create()
    await session.append_message(UserMessage(content=[TextContent(text="old context")]))
    recent = UserMessage(content=[TextContent(text="recent context")])
    recent_id = await session.append_message(recent)
    await session.append_compaction(
        "summary of old context",
        first_kept_entry_id=recent_id,
        tokens_before=100,
        retained_tail=[recent],
    )

    context = await session.build_context_messages()

    assert context[0].role == "compaction_summary"
    assert context[0].summary == "summary of old context"  # type: ignore[union-attr]
    assert context[1] == recent
    assert len(await session.entries()) == 3


@pytest.mark.asyncio
async def test_jsonl_store_round_trips_session_entries(tmp_path: Path) -> None:
    repository = SessionRepository(JsonlSessionStore(tmp_path))
    session = await repository.create()
    await session.append_message(UserMessage(content=[TextContent(text="persist me")]))

    reopened = await repository.open(session.id)

    assert [message.content[0].text for message in await reopened.build_context_messages()] == ["persist me"]


@pytest.mark.asyncio
async def test_jsonl_store_recovers_from_a_torn_final_append(tmp_path: Path) -> None:
    repository = SessionRepository(JsonlSessionStore(tmp_path))
    session = await repository.create()
    await session.append_message(UserMessage(content=[TextContent(text="durable")]))
    (tmp_path / f"{session.id}.jsonl").open("a", encoding="utf-8").write('{"id":')

    reopened = await repository.open(session.id)

    assert [message.content[0].text for message in await reopened.build_context_messages()] == ["durable"]
