"""Pi-compatible provider-neutral content and transcript message models.

本模块定义 LeAgent Agent 的核心数据模型——消息（Message）。

为什么消息很重要：
- 消息是可重放的对话事实，持久化后可以恢复会话
- 消息是模型可见的上下文，决定模型能"看到"什么信息
- 消息是跨供应商的统一协议，不依赖底层模型 API

核心设计：
1. WireModel：所有网络模型的基类，自动处理 snake_case <-> camelCase 转换
2. 有序内容块：AssistantMessage.content 是文本、思考和工具调用的有序列表
3. 严格模式：extra="forbid" 防止拼写错误的字段被静默忽略

消息类型总览：
- UserMessage：用户输入（文本或图片）
- AssistantMessage：助手输出（文本、思考、工具调用的有序组合）
- ToolResultMessage：工具执行结果
- CustomMessage：扩展/应用自定义消息
- BranchSummaryMessage/CompactionSummaryMessage：会话树摘要

阅读顺序建议：
1. WireModel（了解序列化规则）
2. AssistantContent 类型别名（了解内容块类型）
3. UserMessage/AssistantMessage/ToolResultMessage（三大核心消息）
4. AgentMessage 联合类型（了解类型收窄）
"""

from __future__ import annotations

from time import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from le_agent.types import JSONValue


def _to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


def current_timestamp_ms() -> int:
    """Return the current Unix timestamp in milliseconds."""
    return int(time() * 1000)


# Python 代码使用 snake_case，wire format 使用 Pi 兼容的 camelCase；把转换集中在
# 基类后，Provider、会话存储和前端就不需要各自维护一套字段映射。
class WireModel(BaseModel):
    """严格模型基类：Python 字段用 snake_case，JSON 序列化用 camelCase。

    设计说明：
    - alias_generator=_to_camel 自动将 snake_case 转换为 camelCase
    - extra="forbid" 拒绝未知字段，防止拼写错误被静默忽略
    - validate_by_name=True 允许用 Python 字段名构造
    - validate_by_alias=True 允许用 JSON 别名构造
    - serialize_by_alias=True 序列化时使用 camelCase

    为什么这样设计：
    Provider、会话存储和前端只需要处理一种 JSON 格式（camelCase），
    而 Python 代码保持 PEP8 风格（snake_case）。转换逻辑集中在基类，
    避免每个字段重复写 Field(alias="...")。
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        alias_generator=_to_camel,
    )


class UsageCost(WireModel):
    """Billed response cost in USD."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


class Usage(WireModel):
    """单次助手响应的 Token 使用统计。

    字段说明：
    - input: 输入 token 数（发送给模型的消息）
    - output: 输出 token 数（模型生成的内容）
    - cache_read: 从缓存读取的 token 数（Anthropic 的 prompt caching）
    - cache_write: 写入缓存的 token 数
    - cache_write_1h: 1小时内将被移除的缓存 token（Anthropic 特有）
    - reasoning: 推理过程的 token 数（如果有）
    - total_tokens: 总 token 数（input + output）
    - cost: 估算成本（USD）

    用途：
    - 上下文窗口管理：检查是否接近模型限制
    - 成本估算：统计会话总成本
    - 性能分析：input/output 比例、缓存命中率
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_1h: int | None = None
    reasoning: int | None = None
    total_tokens: int = 0
    cost: UsageCost = UsageCost()


class TextContent(WireModel):
    type: Literal["text"] = "text"
    text: str
    text_signature: str | None = None


class ThinkingContent(WireModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    thinking_signature: str | None = None
    redacted: bool = False


class ImageContent(WireModel):
    type: Literal["image"] = "image"
    data: str
    mime_type: str


class ToolCall(WireModel):
    """工具调用块：模型请求调用某个工具的完整描述。

    这是 AssistantMessage.content 中的一种内容块类型。

    字段说明：
    - id: 工具调用的唯一标识，用于关联 ToolResultMessage
    - name: 要调用的工具名称（必须在可用工具列表中）
    - arguments: 工具参数（JSON 对象，必须符合工具的 JSON Schema）
    - thought_signature: 可选的签名/校验和（用于安全场景）

    生命周期：
    1. 模型生成 ToolCall（在 AssistantMessage.content 中）
    2. Agent Loop 识别到 tool_calls，逐个执行
    3. 执行结果包装为 ToolResultMessage
    4. ToolResultMessage 追加到消息历史
    5. 新历史再次发送给模型

    为什么 id 很重要：
    模型一次可能请求多个工具调用，id 用于将结果与请求一一对应。
    """

    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    arguments: dict[str, JSONValue] = Field(default_factory=dict)
    thought_signature: str | None = None


type UserContent = str | list[TextContent | ImageContent]
type AssistantContent = TextContent | ThinkingContent | ToolCall
type ToolResultContent = TextContent | ImageContent


class UserMessage(WireModel):
    """用户消息：用户输入的文本或图片。

    字段说明：
    - content: 用户输入内容，可以是：
      * 简单字符串（内部自动转换为单文本块）
      * 文本+图片块列表（支持多模态输入）
    - timestamp: 消息发送时间戳（毫秒）

    属性方法：
    - text: 提取纯文本内容（图片会被过滤）

    使用示例：
        # 简单文本
        UserMessage(content="解释这段代码")

        # 文本+图片
        UserMessage(content=[
            TextContent(text="这张图有什么问题？"),
            ImageContent(data="base64...", mime_type="image/png")
        ])
    """

    role: Literal["user"] = "user"
    content: UserContent
    timestamp: int = Field(default_factory=current_timestamp_ms)

    @property
    def text(self) -> str:
        return content_text(self.content)


class AssistantDiagnosticError(WireModel):
    name: str | None = None
    message: str
    stack: str | None = None
    code: str | int | None = None


class AssistantMessageDiagnostic(WireModel):
    type: str
    timestamp: int = Field(default_factory=current_timestamp_ms)
    error: AssistantDiagnosticError | None = None
    details: dict[str, JSONValue] | None = None


StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]


class AssistantMessage(WireModel):
    """助手消息：模型输出的完整表示，包含有序内容块。

    关键设计——有序内容块：
    content 字段不是简单字符串，而是 list[TextContent | ThinkingContent | ToolCall]。
    模型可能按任意顺序生成：先思考、再文本、再工具调用。
    这个列表保留了生成顺序，因此既能持久化，也能在恢复会话后原样重放给 Provider。

    为什么不用简单字符串：
    - 需要区分文本、思考过程、工具调用
    - 工具调用需要结构化数据（id, name, arguments）
    - 思考过程可能要对用户隐藏

    属性方法：
    - text: 提取所有文本块的拼接
    - thinking_text: 提取所有思考块的拼接
    - tool_calls: 提取所有工具调用块

    字段说明：
    - content: 有序内容块列表（核心字段）
    - api/provider/model: 响应对应的 API 端点和模型信息
    - response_model/response_id: 供应商返回的原始标识
    - usage: Token 使用统计（输入/输出/缓存/成本）
    - stop_reason: 停止原因（stop/length/toolUse/error/aborted）
    - error_message: 错误时的详细错误信息
    - timestamp: 消息生成时间戳（毫秒）

    便利构造：
    构造函数接受 content="字符串" 作为快捷方式，内部自动包装为 [TextContent(text="字符串")]。
    但序列化时始终是块列表格式。
    """

    role: Literal["assistant"] = "assistant"
    # 文本、thinking 与工具调用按生成顺序共同持久化；最终消息是重放时的权威表示。
    content: list[AssistantContent] = Field(default_factory=list)
    api: str = "unknown"
    provider: str = "unknown"
    model: str = "unknown"
    response_model: str | None = None
    response_id: str | None = None
    diagnostics: list[AssistantMessageDiagnostic] | None = None
    usage: Usage = Usage()
    stop_reason: StopReason = "stop"
    error_message: str | None = None
    timestamp: int = Field(default_factory=current_timestamp_ms)

    @model_validator(mode="before")
    @classmethod
    def _normalize_convenient_content(cls, value: object) -> object:
        """Accept a string only as a Python construction convenience.

        The stored model and serialized protocol are always block based. This
        keeps provider and test construction concise without creating a second
        message representation.
        """
        if not isinstance(value, dict):
            return value
        data = dict(value)
        content = data.get("content")
        if isinstance(content, str):
            data["content"] = [TextContent(text=content)] if content else []
        usage = data.get("usage")
        if usage is None:
            data["usage"] = Usage()
        return data

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.content if isinstance(block, TextContent))

    @property
    def thinking_text(self) -> str:
        return "".join(
            block.thinking for block in self.content if isinstance(block, ThinkingContent)
        )

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(block for block in self.content if isinstance(block, ToolCall))


class ToolResultMessage(WireModel):
    """工具结果消息：工具执行完成后的输出，送还给模型。

    这是工具调用生命周期的终点。当 Agent Loop 执行完 ToolCall 后，
    将结果包装为此消息类型，追加到消息历史中。

    字段说明：
    - tool_call_id: 对应 ToolCall.id，让模型知道这是哪个工具的结果
    - tool_name: 工具名称（便于调试和展示）
    - content: 工具输出内容（文本或图片的有序列表）
    - details: 扩展细节（如 exit_code、文件路径等，供应商无关）
    - added_tool_names: 此工具执行期间新增的可用工具（动态工具注册）
    - is_error: 是否表示错误结果（模型应知道工具执行失败）
    - timestamp: 结果生成时间戳

    为什么需要 tool_call_id：
    模型可能同时请求多个工具调用（如同时读取多个文件）。
    tool_call_id 确保模型能将结果与请求正确对应。

    便利构造：
    构造函数接受 content="字符串" 作为快捷方式，内部自动包装为 [TextContent(text="字符串")]。
    """

    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str
    tool_name: str
    content: list[ToolResultContent] = Field(default_factory=list)
    details: JSONValue = None
    added_tool_names: list[str] | None = None
    is_error: bool = False
    timestamp: int = Field(default_factory=current_timestamp_ms)

    @model_validator(mode="before")
    @classmethod
    def _normalize_convenient_content(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        content = data.get("content")
        if isinstance(content, str):
            data["content"] = [TextContent(text=content)] if content else []
        return data

    @property
    def text(self) -> str:
        return content_text(self.content)


class BashExecutionMessage(WireModel):
    role: Literal["bashExecution"] = "bashExecution"
    command: str
    output: str
    exit_code: int | None = None
    cancelled: bool = False
    truncated: bool = False
    full_output_path: str | None = None
    timestamp: int = Field(default_factory=current_timestamp_ms)
    exclude_from_context: bool = False


class CustomMessage(WireModel):
    role: Literal["custom"] = "custom"
    custom_type: str
    content: UserContent
    display: bool = True
    details: JSONValue = None
    timestamp: int = Field(default_factory=current_timestamp_ms)

    @property
    def text(self) -> str:
        return content_text(self.content)


class BranchSummaryMessage(WireModel):
    role: Literal["branchSummary"] = "branchSummary"
    summary: str
    from_id: str
    timestamp: int = Field(default_factory=current_timestamp_ms)


class CompactionSummaryMessage(WireModel):
    role: Literal["compactionSummary"] = "compactionSummary"
    summary: str
    tokens_before: int
    timestamp: int = Field(default_factory=current_timestamp_ms)


type AgentMessage = Annotated[
    UserMessage
    | AssistantMessage
    | ToolResultMessage
    | BashExecutionMessage
    | CustomMessage
    | BranchSummaryMessage
    | CompactionSummaryMessage,
    Field(discriminator="role"),
]
"""AgentMessage：所有消息类型的联合类型。

使用 Pydantic 的 Annotated + Field(discriminator="role") 实现类型收窄：
- 序列化时：根据 role 字段确定具体类型
- 反序列化时：根据 role 字段构造对应类型的实例

role 值映射：
- "user" -> UserMessage
- "assistant" -> AssistantMessage
- "toolResult" -> ToolResultMessage
- "bashExecution" -> BashExecutionMessage
- "custom" -> CustomMessage
- "branchSummary" -> BranchSummaryMessage
- "compactionSummary" -> CompactionSummaryMessage

为什么用联合类型：
- 类型安全：编译时检查消息类型
- 模式验证：自动验证字段完整性
- 可扩展：新增消息类型只需扩展联合类型
"""


def assistant_content(
    text: str,
    tool_calls: list[ToolCall] | tuple[ToolCall, ...] = (),
) -> list[AssistantContent]:
    """Build canonical ordered assistant blocks from parser accumulators."""
    blocks: list[AssistantContent] = [TextContent(text=text)] if text else []
    blocks.extend(tool_calls)
    return blocks


def content_text(content: str | list[Any]) -> str:
    """Return visible text from string or text/image content."""
    if isinstance(content, str):
        return content
    return "".join(block.text for block in content if isinstance(block, TextContent))


def message_to_user(message: AgentMessage) -> UserMessage:
    """Convert custom/session-only messages to provider-compatible user context."""
    return UserMessage(content=message_text(message), timestamp=message.timestamp)


def message_text(message: AgentMessage) -> str:
    """Return the user-visible text represented by an agent message."""
    if isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage, CustomMessage)):
        return message.text
    if isinstance(message, (BranchSummaryMessage, CompactionSummaryMessage)):
        return message.summary
    if isinstance(message, BashExecutionMessage):
        return message.output
    return ""
