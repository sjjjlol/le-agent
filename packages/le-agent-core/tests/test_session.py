import json
from pathlib import Path

import pytest
from le_agent_ai import TextContent, UserMessage
from le_agent_ai.models import AgentMessage
from le_agent_core import BranchDivergence, SessionTreeNode
from le_agent_core.session import JsonlSessionStore, MemorySessionStore, SessionEntry, SessionRepository


def _user_texts(messages: list[AgentMessage]) -> list[str]:
    result: list[str] = []
    for message in messages:
        assert isinstance(message, UserMessage)
        result.append(message.content[0].text)
    return result


@pytest.mark.asyncio
async def test_session_leaf_navigation_creates_a_branch_without_rewriting_history() -> None:
    repository = SessionRepository(MemorySessionStore())
    session = await repository.create()
    first = await session.append_message(UserMessage(content=[TextContent(text="first")]))
    second = await session.append_message(UserMessage(content=[TextContent(text="second")]))
    await session.move_to(first)
    await session.append_message(UserMessage(content=[TextContent(text="branch")]))

    assert _user_texts(await session.build_context_messages()) == ["first", "branch"]
    assert len(await session.entries()) == 3  # pointer movement is not persisted as an entry
    assert second in {entry.id for entry in await session.entries()}


@pytest.mark.asyncio
async def test_session_tree_resolves_labels_and_branch_divergence() -> None:
    repository = SessionRepository(MemorySessionStore())
    session = await repository.create()
    root = await session.append_message(UserMessage(content=[TextContent(text="root")]))
    old_leaf = await session.append_message(UserMessage(content=[TextContent(text="old branch")]))
    await session.move_to(root)
    target = await session.append_message(UserMessage(content=[TextContent(text="new branch")]))
    await session.append_label(target, "checkpoint")

    tree = await session.get_tree()
    divergence = await session.branch_divergence(old_leaf, target)

    assert isinstance(tree[0], SessionTreeNode)
    assert isinstance(divergence, BranchDivergence)
    assert len(tree) == 1
    assert tree[0].entry.id == root
    assert {child.entry.id for child in tree[0].children} == {old_leaf, target}
    target_node = next(child for child in tree[0].children if child.entry.id == target)
    assert target_node.label == "checkpoint"
    assert await session.get_entry(target) == target_node.entry
    assert divergence.common_ancestor_id == root
    assert [entry.id for entry in divergence.abandoned_entries] == [old_leaf]


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
    assert context[0].summary == "summary of old context"
    assert context[1] == recent
    assert len(await session.entries()) == 3


@pytest.mark.asyncio
async def test_jsonl_store_round_trips_session_entries(tmp_path: Path) -> None:
    repository = SessionRepository(JsonlSessionStore(tmp_path))
    session = await repository.create()
    await session.append_message(UserMessage(content=[TextContent(text="persist me")]))

    reopened = await repository.open(session.id)

    assert _user_texts(await reopened.build_context_messages()) == ["persist me"]
    header = (tmp_path / f"{session.id}.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert '"version": 2' in header


@pytest.mark.asyncio
async def test_jsonl_store_reads_version_one_leaf_entries_without_showing_them(tmp_path: Path) -> None:
    identifier = "legacy"
    root = SessionEntry(
        id="root",
        parent_id=None,
        timestamp=1.0,
        type="message",
        payload={"message": UserMessage(content=[TextContent(text="root")]).model_dump(mode="json")},
    )
    abandoned = SessionEntry(
        id="old",
        parent_id="root",
        timestamp=2.0,
        type="message",
        payload={"message": UserMessage(content=[TextContent(text="old")]).model_dump(mode="json")},
    )
    leaf = SessionEntry(id="leaf", parent_id="old", timestamp=3.0, type="leaf", payload={"target_id": "root"})
    path = tmp_path / f"{identifier}.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"type":"session","version":1,"id":"legacy","created_at":1}',
                json.dumps(root.to_dict()),
                json.dumps(abandoned.to_dict()),
                json.dumps(leaf.to_dict()),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = await SessionRepository(JsonlSessionStore(tmp_path)).open(identifier)

    assert await session.leaf_id() == "root"
    assert [node.entry.id for node in await session.get_tree()] == ["root"]


@pytest.mark.asyncio
async def test_jsonl_store_recovers_from_a_torn_final_append(tmp_path: Path) -> None:
    repository = SessionRepository(JsonlSessionStore(tmp_path))
    session = await repository.create()
    await session.append_message(UserMessage(content=[TextContent(text="durable")]))
    (tmp_path / f"{session.id}.jsonl").open("a", encoding="utf-8").write('{"id":')

    reopened = await repository.open(session.id)

    assert _user_texts(await reopened.build_context_messages()) == ["durable"]
