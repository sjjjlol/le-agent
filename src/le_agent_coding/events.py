"""Pi-compatible coding-session events consumed by frontends and SDK users.

本模块定义 Coding 层特有的会话事件——应用层编排状态的变更通知。

三层事件体系回顾：
1. Provider 层事件 (le_agent/provider_events.py)：
   text_delta, toolcall_start 等——模型正在生成什么
2. Agent 层事件 (le_agent/events.py)：
   turn_start, message_end, tool_execution_end 等——Agent 这一轮正在做什么
3. Coding/Session 层事件 (本模块)：
   compaction_start, agent_settled, queue_update 等——应用会话还在编排什么

事件边界设计：
- Provider 事件不泄漏到上层，被 Agent Loop 消费并转换为 Agent 事件
- Agent 事件被 Harness 产出，CodingSession 透传大部分给前端
- CodingSession 在特定时机插入自己的事件（如压缩、重试）

关键事件说明：
- SessionAgentEndEvent: Agent Run 结束（但 Session 可能还有后续编排）
- AgentSettledEvent: 真正安定——压缩、重试、队列处理全部完成
- QueueUpdateEvent: steering/follow-up 队列状态更新
- CompactionStartEvent/CompactionEndEvent: 上下文压缩开始/结束
- AutoRetryStartEvent/AutoRetryEndEvent: 自动重试（如上下文溢出后）

前端状态机提示：
- 收到 AgentStartEvent: 显示"正在思考..."
- 收到 MessageUpdateEvent: 流式更新文本
- 收到 AgentEndEvent: 可以收起思考动画，但保持 loading
- 收到 AgentSettledEvent: 真正结束 loading，允许新输入

为什么需要 settled：
AgentEnd 后 Session 可能还要做：
1. 上下文溢出后的自动压缩
2. 压缩后的自动重试
3. 队列消息的继续处理
只有这些都完成，才是真正的"可交互"状态。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from le_agent.events import AgentEvent
from le_agent.messages import AgentMessage, WireModel
from le_agent.session.entries import SessionEntry


class SessionAgentEndEvent(WireModel):
    """Agent Run 结束事件（但 Session 可能还有后续编排）。

    与 AgentEndEvent 的区别：
    - AgentEndEvent (le_agent/events.py): 纯 Loop 结束
    - SessionAgentEndEvent: CodingSession 包装的结束，包含重试信息

    字段说明：
    - messages: 本轮产生的新消息列表
    - will_retry: 是否还会自动重试（如上下文溢出压缩后）

    前端提示：
    收到此事件时可以更新消息列表，但如果 will_retry 为 True，
    应保持 loading 状态，等待后续的 AutoRetryStartEvent。
    """

    type: Literal["agent_end"] = "agent_end"
    messages: list[AgentMessage] = Field(default_factory=list)
    will_retry: bool = Field(False)


class AgentSettledEvent(WireModel):
    """Agent 已真正安定——所有编排（压缩、重试、队列）都已完成。

    这是前端应该退出"loading"状态的信号。

    与 SessionAgentEndEvent 的区别：
    - SessionAgentEndEvent: Agent Run 结束，但 Session 可能还要处理
    - AgentSettledEvent: Session 编排也完成，可以安全交互

    使用场景：
    - TUI 收到此事件后，退出 running 状态，允许用户输入
    - Print mode 收到此事件后，知道整个流程真正结束

    事件序列示例（含溢出重试）：
        ... 正常 Agent 事件 ...
        AgentEndEvent (will_retry=False)
        CompactionStartEvent (reason="overflow")
        CompactionEndEvent (will_retry=True)
        AutoRetryStartEvent
        ... 重试的 Agent 事件 ...
        AgentEndEvent (will_retry=False)
        AgentSettledEvent  <-- 这里才是真正结束
    """

    type: Literal["agent_settled"] = "agent_settled"


class QueueUpdateEvent(WireModel):
    """消息队列状态更新事件。

    当用户通过 steer() 或 follow_up() 添加队列消息时触发，
    让前端可以显示队列状态（如"2 条消息等待处理"）。

    字段说明：
    - steering: steering 队列中的消息文本列表
    - follow_up: follow-up 队列中的消息文本列表

    前端展示建议：
    - 显示队列计数徽章
    - 允许查看和删除队列中的消息
    - 区分 steering（会尽快处理）和 follow-up（等当前结束后处理）
    """

    type: Literal["queue_update"] = "queue_update"
    steering: tuple[str, ...] = ()
    follow_up: tuple[str, ...] = Field(())


CompactionReason = Literal["manual", "threshold", "overflow"]
"""上下文压缩触发原因类型。

- "manual": 用户手动触发（如 /compact 命令）
- "threshold": 自动触发（token 数超过阈值）
- "overflow": 上下文溢出错误后的自动恢复
"""


class CompactionStartEvent(WireModel):
    """上下文压缩开始事件。

    触发场景：
    1. 用户手动执行压缩命令
    2. 上下文自动达到阈值
    3. 模型返回上下文溢出错误

    字段说明：
    - reason: 压缩触发原因（manual/threshold/overflow）

    前端展示建议：
    - 显示"正在压缩上下文..."提示
    - 如果是 overflow，显示警告图标
    """

    type: Literal["compaction_start"] = "compaction_start"
    reason: CompactionReason


class CompactionEndEvent(WireModel):
    """上下文压缩结束事件。

    字段说明：
    - reason: 压缩触发原因
    - result: 压缩结果（摘要内容，可选）
    - aborted: 是否被中止（如用户取消）
    - will_retry: 压缩后是否会自动重试
    - error_message: 错误信息（如果失败）

    与 AutoRetry 的关系：
    如果是 overflow 导致的压缩且成功，will_retry 通常为 True，
    随后会触发 AutoRetryStartEvent。

    前端展示建议：
    - 显示压缩结果摘要（如"已压缩 10 条消息"）
    - 如果 will_retry，保持 loading 状态
    - 如果 aborted，显示警告
    """

    type: Literal["compaction_end"] = "compaction_end"
    reason: CompactionReason
    result: object | None = None
    aborted: bool = False
    will_retry: bool = Field(False)
    error_message: str | None = Field(None)


class EntryAppendedEvent(WireModel):
    type: Literal["entry_appended"] = "entry_appended"
    entry: SessionEntry


class SessionInfoChangedEvent(WireModel):
    type: Literal["session_info_changed"] = "session_info_changed"
    name: str | None = None


class ThinkingLevelChangedEvent(WireModel):
    type: Literal["thinking_level_changed"] = "thinking_level_changed"
    level: str


class AutoRetryStartEvent(WireModel):
    """自动重试开始事件（如上下文溢出压缩后的重试）。

    字段说明：
    - attempt: 当前尝试次数（从 1 开始）
    - max_attempts: 最大尝试次数
    - delay_ms: 延迟毫秒数（当前实现为 0）
    - error_message: 触发重试的错误信息

    使用场景：
    - 上下文溢出后，压缩完成，重新发送请求
    - 未来可能用于其他可恢复错误

    前端展示建议：
    - 显示"正在重试..."提示
    - 显示尝试次数（如"第 1 次重试"）
    """

    type: Literal["auto_retry_start"] = "auto_retry_start"
    attempt: int
    max_attempts: int
    delay_ms: int
    error_message: str


class AutoRetryEndEvent(WireModel):
    """自动重试结束事件。

    字段说明：
    - success: 是否成功
    - attempt: 实际尝试次数
    - final_error: 最终错误信息（如果失败）

    事件序列：
    成功后通常会收到 AgentSettledEvent，表示整个流程结束。
    """

    type: Literal["auto_retry_end"] = "auto_retry_end"
    success: bool
    attempt: int
    final_error: str | None = Field(None)


type SessionOwnEvent = Annotated[
    SessionAgentEndEvent
    | AgentSettledEvent
    | QueueUpdateEvent
    | CompactionStartEvent
    | CompactionEndEvent
    | EntryAppendedEvent
    | SessionInfoChangedEvent
    | ThinkingLevelChangedEvent
    | AutoRetryStartEvent
    | AutoRetryEndEvent,
    Field(discriminator="type"),
]
"""Coding 层特有的会话事件联合类型。

使用 Pydantic 的 Field(discriminator="type") 实现类型收窄。

事件类型映射：
- "agent_end" -> SessionAgentEndEvent
- "agent_settled" -> AgentSettledEvent
- "queue_update" -> QueueUpdateEvent
- "compaction_start" -> CompactionStartEvent
- "compaction_end" -> CompactionEndEvent
- "entry_appended" -> EntryAppendedEvent
- "session_info_changed" -> SessionInfoChangedEvent
- "thinking_level_changed" -> ThinkingLevelChangedEvent
- "auto_retry_start" -> AutoRetryStartEvent
- "auto_retry_end" -> AutoRetryEndEvent
"""

type CodingSessionEvent = AgentEvent | SessionOwnEvent
"""完整的 CodingSession 事件联合类型。

包含：
- AgentEvent (le_agent/events.py): 基础 Agent 事件
- SessionOwnEvent: Coding 层特有事件

前端通过监听 CodingSessionEvent 可以获取完整的事件流。
"""

type AgentSessionEvent = CodingSessionEvent
