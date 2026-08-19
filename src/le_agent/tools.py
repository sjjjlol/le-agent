"""Pi-compatible provider-neutral tool definitions and execution results.

本模块定义 LeAgent Agent 的工具协议——模型可见的工具描述与本地执行器的统一接口。

核心设计——双向视图：
AgentTool 同时包含两类信息：
1. 给模型看的（schema）：name、description、parameters（JSON Schema）
2. 给运行时用的（executor）：execute_fn、取消信号、进度回调
3. 给前端用的（renderer）：render_call、render_result（可选）

这种设计让 Agent Loop 只关心核心能力（调用工具、获取结果），
而具体工具实现（文件操作、shell 命令等）在 le_agent_coding 层完成。

类型协议说明：
- ToolExecutor: 工具执行函数签名
- ToolCancellationToken: 取消检查协议
- ToolCallRenderer/ToolResultRenderer: 前端渲染函数签名

工具生命周期：
1. 模型请求 ToolCall（在 AssistantMessage.content 中）
2. Agent Loop 查找对应 AgentTool（通过 name）
3. 调用 AgentTool.execute() 执行
4. 返回 AgentToolResult
5. 包装为 ToolResultMessage 回填 transcript

错误处理：
工具异常不应炸掉整个 Agent Loop，而应包装为错误 AgentToolResult，
让模型有机会看到错误并调整下一步。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field, model_validator

from le_agent.messages import ImageContent, TextContent, ToolCall, WireModel
from le_agent.types import JSONValue


class ToolCancellationToken(Protocol):
    def is_cancelled(self) -> bool:
        """Return whether tool execution should stop."""
        ...


class AgentToolResult(WireModel):
    """工具执行结果：工具产生的最终或部分输出。

    这是工具与 Agent Loop 之间的标准结果格式。

    字段说明：
    - content: 结果内容（文本或图片的有序列表）
    - details: 扩展细节（任意 JSON，如 exit_code、文件路径等）
    - added_tool_names: 此工具执行期间新增的可用工具名称（动态工具注册）
    - terminate: 是否应终止当前 Agent Run（某些特殊工具使用）

    便利构造：
    构造函数接受 content="字符串" 作为快捷方式，内部自动包装为 [TextContent(text="字符串")]。

    与 ToolResultMessage 的区别：
    - AgentToolResult: 工具层返回的结果对象
    - ToolResultMessage: 消息层包装，包含 tool_call_id、tool_name 等元数据

    属性方法：
    - text: 提取所有文本内容的拼接
    """

    content: list[TextContent | ImageContent] = Field(default_factory=list)
    details: JSONValue = None
    added_tool_names: list[str] | None = None
    terminate: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_text_content(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        content = data.get("content")
        if isinstance(content, str):
            data["content"] = [TextContent(text=content)] if content else []
        return data

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.content if isinstance(block, TextContent))


class ToolCallRenderer(Protocol):
    def __call__(self, arguments: Mapping[str, JSONValue]) -> str | None:
        """Return a frontend-friendly tool invocation, or ``None``."""
        ...


class ToolResultRenderer(Protocol):
    def __call__(self, result: AgentToolResult, *, expanded: bool) -> str | None:
        """Return frontend markup for a tool result, or ``None``."""
        ...


ToolUpdateCallback = Callable[[AgentToolResult], None]


class ToolExecutor(Protocol):
    def __call__(
        self,
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> Awaitable[AgentToolResult]:
        """Execute one validated tool call."""
        ...


ToolExecutionMode = Literal["sequential", "parallel"]
ToolArgumentPreparer = Callable[[object], Mapping[str, JSONValue]]


@dataclass(frozen=True, slots=True)
class AgentTool:
    """Agent Loop 可调用的工具定义。

    核心设计——三层职责分离：
    1. Schema 层（给模型看）：name、label、description、parameters
       - parameters 是 JSON Schema，告诉模型如何构造参数
    2. 执行层（给运行时看）：execute_fn、prepare_arguments、execution_mode
       - execute_fn 是实际的工具执行逻辑
    3. 渲染层（给前端看）：render_call、render_result（可选）
       - 提供人类友好的展示，Agent Loop 不依赖这些

    字段说明：
    - name: 工具唯一标识（字母数字下划线，符合 JSON Schema 规范）
    - label: 人类友好的显示名称（如 "Read File"）
    - description: 工具功能描述（模型用此决定是否调用）
    - parameters: JSON Schema 对象，描述参数结构
    - execute_fn: 工具执行函数（ToolExecutor 协议）
    - prompt_snippet: 注入系统提示词的代码片段（可选）
    - prompt_guidelines: 注入系统提示词的使用指南（可选）
    - prepare_arguments: 参数预处理函数（如将 Pydantic 模型转为 dict）
    - execution_mode: 执行模式
      * "parallel": 可与其他工具并行执行（默认）
      * "sequential": 必须串行执行
    - render_call: 工具调用前端渲染函数（可选）
    - render_result: 工具结果前端渲染函数（可选）

    execute() 方法：
    包装 execute_fn，提供统一的调用签名。

    示例：
        read_tool = AgentTool(
            name="read",
            label="Read File",
            description="Read the contents of a file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"}
                },
                "required": ["path"]
            },
            execute_fn=read_file_executor,
        )
    """

    # schema/description 供模型选择工具，execute_fn 供本地运行时执行；可选 renderer
    # 只为前端提供展示能力，核心 Agent Loop 不依赖任何 UI 框架。
    name: str
    label: str
    description: str
    parameters: Mapping[str, JSONValue]
    execute_fn: ToolExecutor
    prompt_snippet: str | None = None
    prompt_guidelines: tuple[str, ...] = ()
    prepare_arguments: ToolArgumentPreparer | None = None
    execution_mode: ToolExecutionMode = "parallel"
    render_call: ToolCallRenderer | None = None
    render_result: ToolResultRenderer | None = None

    @property
    def input_schema(self) -> Mapping[str, JSONValue]:
        """Alias used by provider payload builders."""
        return self.parameters

    async def execute(
        self,
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        """Execute a tool with Pi-compatible call-id and progress semantics."""
        return await self.execute_fn(tool_call_id, arguments, signal, on_update)


__all__ = [
    "AgentTool",
    "AgentToolResult",
    "ToolCall",
    "ToolCallRenderer",
    "ToolCancellationToken",
    "ToolExecutionMode",
    "ToolResultRenderer",
    "ToolExecutor",
    "ToolUpdateCallback",
]
