from __future__ import annotations

import asyncio
from typing import Any

import pytest
from le_agent_ai import (
    AssistantMessage,
    FauxProvider,
    Model,
    ScriptedResponse,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from le_agent_ai.models import BranchSummaryMessage, CompactionSummaryMessage, CustomMessage, ThinkingContent
from le_agent_core import Agent, AgentContext, AgentLoopConfig, AgentTool, ToolResult, agent_loop, agent_loop_continue
from le_agent_core.compaction import (
    CompactionSettings,
    SummaryRequest,
    compact_session,
    estimate_context_tokens,
    estimate_message_tokens,
)
from le_agent_core.harness import AgentHarness
from le_agent_core.loop import default_convert_to_llm
from le_agent_core.session import (
    JsonlSessionStore,
    MemorySessionStore,
    Session,
    SessionEntry,
    SessionRepository,
)
from pydantic import BaseModel


@pytest.mark.parametrize(
    "value, message",
    [
        ({"id": "x", "parent_id": None, "timestamp": 1, "type": "bad"}, "invalid session entry type"),
        ({"parent_id": None, "timestamp": 1, "type": "custom"}, "missing id"),
        ({"id": "x", "parent_id": 3, "timestamp": 1, "type": "custom"}, "invalid parent_id"),
        ({"id": "x", "parent_id": None, "timestamp": "now", "type": "custom"}, "invalid timestamp"),
    ],
)
def test_session_entry_rejects_invalid_shapes(value: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SessionEntry.from_dict(value)


@pytest.mark.asyncio
async def test_session_metadata_projection_and_error_edges(tmp_path) -> None:
    store = MemorySessionStore()
    session = await SessionRepository(store).create()
    root = await session.append_message(UserMessage(content=[TextContent(text="root")]))
    await session.append_thinking_level_change("high")
    await session.append_active_tools_change(["read"])
    await session.append_session_info("hello\nworld")
    await session.append_custom("notice", {"ok": True})
    await session.append_label(root, "checkpoint")
    await session.append_label(root, None)
    await session.close()
    await session.close()

    with pytest.raises(RuntimeError, match="closed"):
        await session.append_model_change("faux", "test")
    with pytest.raises(KeyError, match="entry not found"):
        await session.get_entry("missing")
    with pytest.raises(KeyError, match="entry not found"):
        await session.move_to("missing")
    with pytest.raises(KeyError, match="entry not found"):
        await session.append_label("missing", "label")
    with pytest.raises(KeyError, match="session not found"):
        await store.load("missing")

    json_store = JsonlSessionStore(tmp_path)
    with pytest.raises(KeyError, match="session not found"):
        await json_store.load("missing")
    with pytest.raises(KeyError, match="session not found"):
        await json_store.append("missing", SessionEntry("x", None, 1, "custom", {}))


@pytest.mark.asyncio
async def test_session_projects_all_summary_message_types_and_detects_broken_trees() -> None:
    session = await SessionRepository(MemorySessionStore()).create()
    user = UserMessage(content=[TextContent(text="tail")])
    await session.append_message(user)
    await session.append_compaction("compact", first_kept_entry_id=None, tokens_before=20, retained_tail=[user])
    await session.append_branch_summary("branch", "old")
    branch_entries = tuple(await session.branch())

    projected = await session.messages_for_entries(branch_entries)

    assert [message.role for message in projected] == [
        "user",
        "compaction_summary",
        "user",
        "branch_summary",
    ]
    assert await session.entry_id_for_message(UserMessage(content=[TextContent(text="absent")])) is None

    broken = Session("broken", MemorySessionStore(), [SessionEntry("x", "missing", 1, "custom", {})])
    with pytest.raises(ValueError, match="broken session parent"):
        await broken.get_tree()
    with pytest.raises(ValueError, match="broken session parent"):
        await broken.branch()

    roles = await SessionRepository(MemorySessionStore()).create()
    for message in [
        AssistantMessage(content=[TextContent(text="assistant")]),
        ToolResultMessage(tool_call_id="c", tool_name="read", content=[TextContent(text="tool")]),
        CustomMessage(custom_type="note", content="custom"),
        CompactionSummaryMessage(summary="summary", tokens_before=1),
        BranchSummaryMessage(summary="branch", from_id="old"),
    ]:
        await roles.append_message(message)
    assert [message.role for message in await roles.build_context_messages()] == [
        "assistant",
        "tool_result",
        "custom",
        "compaction_summary",
        "branch_summary",
    ]


def test_token_estimation_covers_canonical_message_variants() -> None:
    assistant = AssistantMessage(
        content=[ThinkingContent(thinking="think"), ToolCallContent(id="c", name="read", arguments={"path": "x"})],
        usage=Usage(input_tokens=10, output_tokens=2),
    )
    tool = ToolResultMessage(tool_call_id="c", tool_name="read", content=[TextContent(text="result")])
    summary = CompactionSummaryMessage(summary="summary", tokens_before=100)
    custom = CustomMessage(custom_type="note", content="custom")

    assert all(estimate_message_tokens(message) > 0 for message in [assistant, tool, summary, custom])
    assert estimate_context_tokens([assistant, UserMessage(content=[TextContent(text="tail")])]) == 13

    converted = default_convert_to_llm(
        [summary, BranchSummaryMessage(summary="branch", from_id="old"), custom]
    )
    assert [message.role for message in converted] == ["user", "user", "user"]


@pytest.mark.asyncio
async def test_incremental_compaction_passes_previous_summary_and_can_be_a_noop() -> None:
    session = await SessionRepository(MemorySessionStore()).create()
    recent = UserMessage(content=[TextContent(text="recent")])
    await session.append_compaction("previous", first_kept_entry_id=None, tokens_before=10, retained_tail=[])
    await session.append_message(UserMessage(content=[TextContent(text="old " * 20)]))
    await session.append_message(recent)
    requests: list[SummaryRequest] = []

    async def summarize(request: SummaryRequest) -> str:
        requests.append(request)
        return "next"

    assert await compact_session(session, CompactionSettings(keep_recent_tokens=2), summarize)
    assert requests[0].previous_summary == "previous"

    one = await SessionRepository(MemorySessionStore()).create()
    await one.append_message(UserMessage(content=[TextContent(text="only")]))
    assert not await compact_session(one, CompactionSettings(keep_recent_tokens=100), summarize)


class ValueArgs(BaseModel):
    value: int


class UpdatingTool(AgentTool[ValueArgs]):
    name = "update"
    description = "updates"
    args_model = ValueArgs
    execution_mode = "sequential"

    async def execute(self, args: ValueArgs, *, on_update: Any = None) -> ToolResult:
        if on_update:
            await on_update(f"value={args.value}")
        return ToolResult.text(str(args.value))


@pytest.mark.asyncio
async def test_loop_hooks_rewrite_tools_and_stop_without_an_extra_model_call() -> None:
    provider = FauxProvider(
        [ScriptedResponse(tool_calls=[("ok", "update", {"value": 2}), ("bad", "missing", {})], stop_reason="tool_use")]
    )
    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    hook_calls: list[str] = []

    async def before(call, args, context):
        del args, context
        hook_calls.append(f"before:{call.id}")
        return None

    async def after(call, result):
        hook_calls.append(f"after:{call.id}")
        result.content = [TextContent(text="rewritten")]
        return result

    async def stop(_context, _assistant, results):
        return len(results) == 2

    context = AgentContext(system_prompt="", messages=[], tools=[UpdatingTool()])
    stream = agent_loop(
        [UserMessage(content=[TextContent(text="go")])],
        context,
        AgentLoopConfig(
            model=model,
            provider=provider,
            before_tool_call=before,
            after_tool_call=after,
            should_stop_after_turn=stop,
        ),
    )
    events = [event async for event in stream]
    messages = await stream.result()

    assert hook_calls == ["before:ok", "after:ok"]
    assert messages[-2].content[0].text == "rewritten"  # type: ignore[union-attr]
    assert messages[-1].is_error  # type: ignore[union-attr]
    assert any(event.type == "tool_execution_update" for event in events)
    assert len(provider.requests) == 1


def test_continue_rejects_invalid_context_shapes() -> None:
    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    config = AgentLoopConfig(model=model, provider=FauxProvider([]))
    with pytest.raises(ValueError, match="empty context"):
        agent_loop_continue(AgentContext("", [], []), config)
    with pytest.raises(ValueError, match="assistant message"):
        agent_loop_continue(AgentContext("", [AssistantMessage(content=[])], []), config)


@pytest.mark.asyncio
async def test_agent_queue_abort_unsubscribe_and_busy_guards() -> None:
    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    agent = Agent(AgentLoopConfig(model=model, provider=FauxProvider([])))
    agent.steer("steer")
    agent.follow_up("follow")
    assert (await agent._drain_steering())[0].content[0].text == "steer"
    assert (await agent._drain_follow_up())[0].content[0].text == "follow"

    events: list[str] = []

    def listener(event) -> None:
        events.append(event.type)

    unsubscribe = agent.subscribe(listener)
    unsubscribe()
    agent._active = asyncio.create_task(asyncio.sleep(0.01))
    await agent.prompt("queued")
    assert (await agent._drain_steering())[0].content[0].text == "queued"
    with pytest.raises(RuntimeError, match="already running"):
        await agent.continue_run()
    agent.abort()
    with pytest.raises(asyncio.CancelledError):
        await agent.wait_for_idle()
    assert events == []


@pytest.mark.asyncio
async def test_harness_navigation_noop_and_requires_summarizer_for_abandoned_branch() -> None:
    session = await SessionRepository(MemorySessionStore()).create()
    root = await session.append_message(UserMessage(content=[TextContent(text="root")]))
    old = await session.append_message(UserMessage(content=[TextContent(text="old")]))
    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    harness = AgentHarness(session=session, config=AgentLoopConfig(model=model, provider=FauxProvider([])))

    same = await harness.navigate_tree(old)
    assert same.new_leaf_id == old
    with pytest.raises(RuntimeError, match="summarizer"):
        await harness.navigate_tree(root, summarize=True)


@pytest.mark.asyncio
async def test_harness_legacy_navigation_continue_and_auto_compaction() -> None:
    session = await SessionRepository(MemorySessionStore()).create()
    root = await session.append_message(UserMessage(content=[TextContent(text="old " * 80)]))
    await session.append_message(UserMessage(content=[TextContent(text="continue")]))
    requests: list[SummaryRequest] = []

    async def summarize(request: SummaryRequest) -> str:
        requests.append(request)
        return "summary"

    provider = FauxProvider([ScriptedResponse.text("done")])
    model = Model(provider="faux", id="scripted", context_window=1, max_output_tokens=10)
    harness = AgentHarness(
        session=session,
        config=AgentLoopConfig(model=model, provider=provider),
        compaction_settings=CompactionSettings(reserve_tokens=0, keep_recent_tokens=2),
        summarizer=summarize,
    )
    await harness.continue_run()
    assert requests

    await harness.move_to(root, summary="legacy summary")
    assert (await session.build_context_messages())[-1].role == "branch_summary"

    empty = AgentHarness(
        session=await SessionRepository(MemorySessionStore()).create(),
        config=AgentLoopConfig(model=model, provider=FauxProvider([])),
    )
    with pytest.raises(RuntimeError, match="empty session"):
        await empty.navigate_tree("missing")
    with pytest.raises(RuntimeError, match="summarizer"):
        await empty.compact()
