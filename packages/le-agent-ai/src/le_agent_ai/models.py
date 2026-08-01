"""Provider-neutral data model used on both sides of the model boundary."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


class LeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TextContent(LeModel):
    type: Literal["text"] = "text"
    text: str


class ThinkingContent(LeModel):
    type: Literal["thinking"] = "thinking"
    thinking: str


class ToolCallContent(LeModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


AssistantContent: TypeAlias = TextContent | ThinkingContent | ToolCallContent


class Usage(LeModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


class UserMessage(LeModel):
    role: Literal["user"] = "user"
    content: list[TextContent]
    timestamp: float = 0.0


class AssistantMessage(LeModel):
    role: Literal["assistant"] = "assistant"
    content: list[AssistantContent]
    provider: str = ""
    model: str = ""
    stop_reason: Literal["stop", "tool_use", "length", "error", "aborted"] = "stop"
    error_message: str | None = None
    usage: Usage = Field(default_factory=Usage)
    timestamp: float = 0.0

    @property
    def text(self) -> str:
        return "".join(part.text for part in self.content if isinstance(part, TextContent))


class ToolResultMessage(LeModel):
    role: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    tool_name: str
    content: list[TextContent]
    is_error: bool = False
    details: Any = None
    timestamp: float = 0.0


LlmMessage: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage


class CustomMessage(LeModel):
    role: Literal["custom"] = "custom"
    custom_type: str
    content: str
    display: bool = True
    timestamp: float = 0.0


class CompactionSummaryMessage(LeModel):
    role: Literal["compaction_summary"] = "compaction_summary"
    summary: str
    tokens_before: int
    timestamp: float = 0.0


class BranchSummaryMessage(LeModel):
    role: Literal["branch_summary"] = "branch_summary"
    summary: str
    from_id: str
    timestamp: float = 0.0


AgentMessage: TypeAlias = LlmMessage | CustomMessage | CompactionSummaryMessage | BranchSummaryMessage


class Model(LeModel):
    provider: str
    id: str
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    supports_tools: bool = True
    supports_thinking: bool = False
    thinking_level_map: dict[str, str | None] = Field(default_factory=dict)
    base_url: str | None = None


class ToolDefinition(LeModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ProviderContext(LeModel):
    system_prompt: str
    messages: list[LlmMessage]
    tools: list[ToolDefinition] = Field(default_factory=list)


class StreamEvent(LeModel):
    """Normalized provider event; fields not relevant to an event stay unset."""

    type: Literal[
        "start",
        "text_delta",
        "thinking_delta",
        "tool_call_delta",
        "usage",
        "done",
        "error",
    ]
    delta: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    usage: Usage | None = None
    message: AssistantMessage | None = None
    error_message: str | None = None
