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


@pytest.mark.asyncio
async def test_permission_modes_cover_trust_confirmation_and_session_edit_approval() -> None:
    call = ToolCallContent(id="1", name="write", arguments={"path": "a"})
    context = AgentContext("", [], [])
    args = Args(path="a")

    assert await PermissionController(PermissionMode.TRUST).before_tool_call(call, args, context) is None
    assert (
        await PermissionController(PermissionMode.CONFIRM).before_tool_call(call, args, context)
        == "permission confirmation required for write"
    )

    decisions = iter(["allow_session_edit", "deny"])

    async def approve(_call, _args):
        return next(decisions)

    policy = PermissionController(PermissionMode.CONFIRM, approve)
    assert await policy.before_tool_call(call, args, context) is None
    assert await policy.before_tool_call(call, args, context) is None
    bash = ToolCallContent(id="2", name="bash", arguments={})
    assert await policy.before_tool_call(bash, args, context) == "user denied bash"

    async def allow_once(_call, _args):
        return "allow_once"

    once = PermissionController(PermissionMode.CONFIRM, allow_once)
    assert await once.before_tool_call(bash, args, context) is None
    read = ToolCallContent(id="3", name="read", arguments={})
    assert await once.before_tool_call(read, args, context) is None
