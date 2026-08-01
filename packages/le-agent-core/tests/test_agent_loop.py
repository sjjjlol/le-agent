from __future__ import annotations

from typing import Any

import pytest
from le_agent_ai import FauxProvider, Model, ScriptedResponse, TextContent, UserMessage
from le_agent_core import AgentContext, AgentLoopConfig, AgentTool, ToolResult, agent_loop
from pydantic import BaseModel


class EchoArgs(BaseModel):
    value: str


class EchoTool(AgentTool[EchoArgs]):
    name = "echo"
    description = "echoes a value"
    args_model = EchoArgs
    execution_mode = "parallel"

    async def execute(self, args: EchoArgs, *, on_update: Any = None) -> ToolResult:
        del on_update
        return ToolResult(content=[TextContent(text=f"echo:{args.value}")], details={"source": "echo"})


@pytest.mark.asyncio
async def test_loop_persists_assistant_and_tool_result_in_source_order() -> None:
    provider = FauxProvider(
        [
            ScriptedResponse.tool_call("call-1", "echo", {"value": "hi"}),
            ScriptedResponse.text("finished"),
        ]
    )
    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    context = AgentContext(system_prompt="help", messages=[], tools=[EchoTool()])
    config = AgentLoopConfig(model=model, provider=provider)

    stream = agent_loop([UserMessage(content=[TextContent(text="go")])], context, config)
    events = [event async for event in stream]
    messages = await stream.result()

    assert [message.role for message in messages] == ["user", "assistant", "tool_result", "assistant"]
    assert messages[2].content[0].text == "echo:hi"  # type: ignore[union-attr]
    assert messages[2].details == {"source": "echo"}  # type: ignore[union-attr]
    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "assistant_request_start",
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_end",
        "message_start",
        "message_end",
        "turn_end",
        "turn_start",
        "assistant_request_start",
        "message_start",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
