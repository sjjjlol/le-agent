from le_agent_ai.models import (
    AssistantMessage,
    ProviderContext,
    TextContent,
    ToolCallContent,
    ToolDefinition,
    ToolResultMessage,
    UserMessage,
)
from le_agent_ai.providers import anthropic_messages, openai_messages


def test_openai_messages_preserve_tool_call_adjacency() -> None:
    context = ProviderContext(
        system_prompt="system",
        messages=[
            UserMessage(content=[TextContent(text="inspect")]),
            AssistantMessage(content=[ToolCallContent(id="call-1", name="read", arguments={"path": "a.py"})]),
            ToolResultMessage(tool_call_id="call-1", tool_name="read", content=[TextContent(text="line 1")]),
        ],
        tools=[ToolDefinition(name="read", description="read", input_schema={"type": "object"})],
    )

    messages = openai_messages(context)

    assert messages[0] == {"role": "system", "content": "system"}
    assert messages[1]["role"] == "user"
    assert messages[2]["tool_calls"][0]["function"]["name"] == "read"
    assert messages[3] == {"role": "tool", "tool_call_id": "call-1", "content": "line 1"}


def test_anthropic_messages_use_tool_result_user_blocks() -> None:
    context = ProviderContext(
        system_prompt="system",
        messages=[
            UserMessage(content=[TextContent(text="inspect")]),
            AssistantMessage(content=[ToolCallContent(id="call-1", name="read", arguments={"path": "a.py"})]),
            ToolResultMessage(tool_call_id="call-1", tool_name="read", content=[TextContent(text="line 1")]),
        ],
    )

    messages = anthropic_messages(context)

    assert messages[0]["role"] == "user"
    assert messages[1]["content"][0]["type"] == "tool_use"
    assert messages[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "line 1", "is_error": False}],
    }
