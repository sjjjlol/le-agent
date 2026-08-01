import pytest
from le_agent_ai import TextContent, UserMessage
from le_agent_core.compaction import CompactionSettings, compact_session
from le_agent_core.session import MemorySessionStore, SessionRepository


@pytest.mark.asyncio
async def test_compaction_is_an_append_only_context_projection() -> None:
    session = await SessionRepository(MemorySessionStore()).create()
    await session.append_message(UserMessage(content=[TextContent(text="old " * 100)]))
    await session.append_message(UserMessage(content=[TextContent(text="recent " * 30)]))

    async def summarize(messages, previous_summary):
        assert previous_summary is None
        assert messages
        return "summary"

    changed = await compact_session(session, CompactionSettings(keep_recent_tokens=20), summarize)
    context = await session.build_context_messages()

    assert changed is True
    assert context[0].role == "compaction_summary"
    assert len(await session.entries()) == 3
