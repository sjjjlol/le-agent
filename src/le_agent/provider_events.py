"""Pi-compatible assistant stream events owned by the portable agent layer.

设计说明：采用三阶段事件生命周期（Start -> Delta -> End）实现流式增量传输，
同时允许消费者重建完整消息状态。每个事件都携带 `partial` 字段作为消息快照，
无需外部累积 delta 即可重建状态。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from le_agent.messages import AssistantMessage, ToolCall, WireModel

# 连接生命周期事件


class AssistantStartEvent(WireModel):
    """流式响应开始的初始事件，标记助手回复的起始点。"""

    type: Literal["start"] = "start"
    partial: AssistantMessage


# 文本内容事件 - 分块流式传输文本响应


class TextStartEvent(WireModel):
    """标记消息中一个新的文本内容块的开始。

    content_index 用于多内容块消息（如文本 + 工具调用混合）的索引定位。
    partial 携带此阶段的消息状态快照。
    """

    type: Literal["text_start"] = "text_start"
    content_index: int
    partial: AssistantMessage


class TextDeltaEvent(WireModel):
    """文本增量块事件。

    delta 字段包含增量内容片段（非累积），消费者需自行拼接以构建完整内容。
    """

    type: Literal["text_delta"] = "text_delta"
    content_index: int
    delta: str  # 增量内容片段（非累积）
    partial: AssistantMessage


class TextEndEvent(WireModel):
    """文本块的结束事件，包含完整累积后的内容。"""

    type: Literal["text_end"] = "text_end"
    content_index: int
    content: str  # 完整文本内容（所有 delta 的拼接结果）
    partial: AssistantMessage


# 思考过程事件 - 模型推理/思考链的流式传输


class ThinkingStartEvent(WireModel):
    """标记思考/推理内容块的开始。"""

    type: Literal["thinking_start"] = "thinking_start"
    content_index: int
    partial: AssistantMessage


class ThinkingDeltaEvent(WireModel):
    """思考过程的增量内容，在模型推理期间流式传输。"""

    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ThinkingEndEvent(WireModel):
    """思考块的结束事件；思考内容通常不对用户展示。"""

    type: Literal["thinking_end"] = "thinking_end"
    content_index: int
    content: str
    partial: AssistantMessage


# 工具调用事件 - 函数/工具调用的 JSON 参数流式传输


class ToolCallStartEvent(WireModel):
    """标记工具调用块的开始；此时工具名称已知但参数可能不完整。"""

    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int
    partial: AssistantMessage


class ToolCallDeltaEvent(WireModel):
    """工具调用参数的增量事件（流式 JSON 字符串）。

    delta 字段包含部分 JSON 字符串；消费者可选择增量解析或等待完整事件。
    """

    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int
    delta: str  # 部分 JSON 字符串；支持增量解析
    partial: AssistantMessage


class ToolCallEndEvent(WireModel):
    """工具调用块的结束事件；参数已完全解析并验证。"""

    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int
    tool_call: ToolCall  # 已验证的结构化 ToolCall 对象
    partial: AssistantMessage


# 终止原因类型定义
DoneReason = Literal["stop", "length", "toolUse"]
ErrorReason = Literal["aborted", "error"]


# 消息终结事件


class AssistantDoneEvent(WireModel):
    """助手响应正常完成的事件。

    reason 字段指示完成原因：
    - "stop": 自然结束
    - "length": 达到长度限制
    - "toolUse": 触发工具调用
    """

    type: Literal["done"] = "done"
    reason: DoneReason
    message: AssistantMessage  # 完整的最终消息


class AssistantErrorEvent(WireModel):
    """流式响应发生错误时的事件。"""

    type: Literal["error"] = "error"
    reason: ErrorReason
    error: AssistantMessage


# 联合类型定义：所有可能的事件类型，使用 type 字段作为判别式
type AssistantMessageEvent = Annotated[
    AssistantStartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | AssistantDoneEvent
    | AssistantErrorEvent,
    Field(discriminator="type"),  # Pydantic 根据此字段进行类型收窄
]
