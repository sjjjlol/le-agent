"""Harness policy hook for readonly, confirmation and trusted operation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from le_agent_ai import ToolCallContent
from le_agent_core import AgentContext
from pydantic import BaseModel


class PermissionMode(StrEnum):
    READONLY = "readonly"
    CONFIRM = "confirm"
    TRUST = "trust"


Approval = Callable[[ToolCallContent, BaseModel], Awaitable[str]]


class PermissionController:
    def __init__(self, mode: PermissionMode = PermissionMode.CONFIRM, approval: Approval | None = None) -> None:
        self.mode = mode
        self.approval = approval
        self._session_edit_allowed = False

    async def before_tool_call(self, call: ToolCallContent, args: BaseModel, context: AgentContext) -> str | None:
        del context
        if call.name == "read" or self.mode is PermissionMode.TRUST:
            return None
        if self.mode is PermissionMode.READONLY:
            return f"permission policy readonly blocks {call.name}"
        if call.name in {"write", "edit"} and self._session_edit_allowed:
            return None
        if self.approval is None:
            return f"permission confirmation required for {call.name}"
        decision = await self.approval(call, args)
        if decision == "allow_session_edit" and call.name in {"write", "edit"}:
            self._session_edit_allowed = True
            return None
        if decision == "allow_once":
            return None
        return f"user denied {call.name}"
