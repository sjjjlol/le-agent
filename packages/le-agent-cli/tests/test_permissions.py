import pytest
from le_agent_ai import ToolCallContent
from le_agent_cli.permissions import PermissionController, PermissionMode
from le_agent_core import AgentContext
from pydantic import BaseModel


class Args(BaseModel):
    path: str


@pytest.mark.asyncio
async def test_readonly_blocks_write_tool() -> None:
    policy = PermissionController(PermissionMode.READONLY)
    reason = await policy.before_tool_call(
        ToolCallContent(id="1", name="write", arguments={"path": "a"}), Args(path="a"), AgentContext("", [], [])
    )

    assert reason == "permission policy readonly blocks write"
