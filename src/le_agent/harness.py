"""Stateful reusable agent harness built on the Pi-compatible loop.

本模块实现 AgentHarness——LeAgent 的可复用有状态 Agent 大脑。

架构定位：
- AgentHarness 位于 le_agent 层，是可复用的有状态包装器
- 它在纯函数式的 run_agent_loop 外层增加状态管理能力
- 不依赖任何 coding 应用或 UI 策略，可独立使用

核心职责：
1. transcript 内存所有权：持有 _messages 列表，提供线程安全的访问
2. 单 run 保证：同一时刻只有一个 agent loop 在运行
3. 取消机制：通过 SimpleCancellationToken 支持取消正在运行的 loop
4. 消息队列：steering（过程引导）和 follow-up（后续）队列管理

与 CodingSession 的关系：
- Harness 是纯内存的，不做持久化
- CodingSession 包装 Harness，增加持久化、压缩等应用层功能
- Harness 的事件通过 subscribe() 发布给所有监听者（包括 CodingSession）

使用示例：
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=some_provider,
            model="gpt-4",
            system="You are a helpful assistant",
            tools=[read_tool, write_tool],
        ),
        messages=initial_messages,
    )

    # 启动新的 agent run
    async for event in harness.prompt("解释这段代码"):
        print(event)

    # 从当前状态继续（不添加新消息）
    async for event in harness.continue_():
        print(event)

    # 在运行中插入引导消息
    harness.steer("关注性能问题")
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Literal

from le_agent.events import AgentEvent
from le_agent.loop import AfterToolCall, BeforeToolCall, run_agent_loop
from le_agent.messages import (
    AgentMessage,
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from le_agent.provider import ModelProvider
from le_agent.tools import AgentTool, ToolExecutionMode

EventListener = Callable[[AgentEvent], Awaitable[None] | None]
QueueMode = Literal["one_at_a_time", "all"]


@dataclass(frozen=True, slots=True)
class QueuedMessages:
    """消息队列状态快照。

    字段说明：
    - steering: 过程引导消息队列（尽快插入当前 run）
    - follow_up: 后续消息队列（当前 run 结束后开启新 run）

    区别：
    - steering：在当前 run 的工具回合结束后立即交给模型
    - follow_up：等当前 run 完全结束后，开启新的 agent run

    使用场景：
    - steering：用户想修正正在进行的回答方向
    - follow_up：用户想基于当前结果继续提问
    """

    steering: tuple[AgentMessage, ...] = ()
    follow_up: tuple[AgentMessage, ...] = ()

    @property
    def count(self) -> int:
        return len(self.steering) + len(self.follow_up)


@dataclass(slots=True)
class AgentHarnessConfig:
    """AgentHarness 的配置数据类。

    字段说明：
    - provider: 模型供应商适配器（实现 ModelProvider 协议）
    - model: 模型名称（如 "gpt-4", "claude-sonnet-4-6"）
    - system: 系统提示词（定义助手行为的指令）
    - tools: 可用工具列表（模型可以请求调用的功能）
    - max_turns: 最大回合数限制（防止无限循环，None 表示无限制）
    - queue_mode: 队列排空模式
      * "one_at_a_time": 一次只处理一条队列消息（默认，给模型逐条响应的机会）
      * "all": 一次性处理当前所有队列消息
    - before_tool_call: 工具调用前回调（可用于权限检查、日志记录）
    - after_tool_call: 工具调用后回调（可用于结果改写、后处理）

    设计说明：
    使用 dataclass(slots=True) 减少内存占用，同时保持不可变语义。
    """

    provider: ModelProvider
    model: str
    system: str
    tools: list[AgentTool] = field(default_factory=list)
    max_turns: int | None = None
    queue_mode: QueueMode = "one_at_a_time"
    tool_execution: ToolExecutionMode = "parallel"
    before_tool_call: BeforeToolCall | None = None
    after_tool_call: AfterToolCall | None = None


class SimpleCancellationToken:
    """简单的取消令牌，用于取消正在运行的 Agent Loop。

    设计说明：
    - 比 asyncio.Event 更轻量，不需要 loop 引用
    - 线程安全（Python GIL 保证）
    - 只能取消一次，不可重置

    使用方式：
    1. 创建令牌并传给 run_agent_loop
    2. 调用 cancel() 设置取消标志
    3. Loop 定期检查 is_cancelled()，若取消则优雅退出
    """

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class AgentHarness:
    """可复用的有状态 Agent 大脑，独立于 coding/UI 策略。

    核心职责：
    1. transcript 所有权：持有 _messages 列表，提供安全的修改接口
    2. 单 run 保证：_running 标志确保同一时刻只有一个 loop
    3. 取消机制：_current_signal 支持运行中取消
    4. 消息队列：steering 和 follow_up 队列管理

    关键不变量：
    - 若 is_running 为 True，prompt()/continue_() 会抛出 RuntimeError
    - 取消后，_append_interrupted_tool_results() 为未完成的工具调用生成错误结果
    - 事件监听者是同步或异步函数，返回值会被 await（如果是协程）

    与 Loop 的关系：
    - Harness 是包装器，Loop 是纯函数
    - Harness 把配置、消息列表、回调传给 run_agent_loop
    - Loop 通过回调（get_steering_messages/get_follow_up_messages）与 Harness 交互

    线程安全：
    - 不是线程安全的，应在单线程（通常是事件循环）中使用
    - 取消可以从其他线程调用（SimpleCancellationToken 是线程安全的）
    """

    def __init__(
        self,
        config: AgentHarnessConfig,
        *,
        messages: Sequence[AgentMessage] = (),
    ) -> None:
        self._config = config
        self._messages = list(messages)
        self._listeners: list[EventListener] = []
        self._current_signal: SimpleCancellationToken | None = None
        self._running = False
        self._steering_queue: deque[AgentMessage] = deque()
        self._follow_up_queue: deque[AgentMessage] = deque()

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        return tuple(self._messages)

    @property
    def config(self) -> AgentHarnessConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def queued_messages(self) -> QueuedMessages:
        return QueuedMessages(tuple(self._steering_queue), tuple(self._follow_up_queue))

    @property
    def pending_message_count(self) -> int:
        return self.queued_messages.count

    def has_queued_messages(self) -> bool:
        return bool(self._steering_queue or self._follow_up_queue)

    def append_message(self, message: AgentMessage) -> None:
        self._messages.append(message)

    def replace_messages(self, messages: Sequence[AgentMessage]) -> None:
        self._messages = list(messages)

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe

    def cancel(self) -> None:
        if self._current_signal is not None:
            self._current_signal.cancel()

    def steer(self, content: str) -> QueuedMessages:
        """添加过程引导消息到 steering 队列。

        使用场景：
        当 Agent 正在运行时，用户想要修正回答方向或提供额外上下文。
        例如："关注性能问题"、"使用更简单的解释"

        参数说明：
            content: 引导消息的文本内容

        返回：
            QueuedMessages: 当前队列状态快照

        处理时机：
        steering 消息在当前 run 的工具回合结束后立即交给模型。
        如果当前没有运行中的 run，消息会留在队列中等待下次 run。

        与 follow_up 的区别：
        - steer: 尽快插入当前 run
        - follow_up: 等当前 run 结束后开启新 run
        """
        return self.steer_message(UserMessage(content=content))

    def steer_message(self, message: AgentMessage) -> QueuedMessages:
        self._steering_queue.append(message)
        return self.queued_messages

    def follow_up(self, content: str) -> QueuedMessages:
        """添加后续消息到 follow_up 队列。

        使用场景：
        当 Agent 正在运行时，用户想要基于当前结果继续提问。
        例如："还有呢？"、"给我个例子"

        参数说明：
            content: 跟进消息的文本内容

        返回：
            QueuedMessages: 当前队列状态快照

        处理时机：
        follow_up 消息在当前 run 完全结束后，开启新的 agent run 时处理。
        这允许模型先完成当前思考，再处理新问题。

        与 steer 的区别：
        - steer: 尽快插入当前 run，打断当前流程
        - follow_up: 等待当前 run 完成，作为新 run 的开始
        """
        return self.follow_up_message(UserMessage(content=content))

    def follow_up_message(self, message: AgentMessage) -> QueuedMessages:
        self._follow_up_queue.append(message)
        return self.queued_messages

    def clear_queues(self) -> QueuedMessages:
        snapshot = self.queued_messages
        self._steering_queue.clear()
        self._follow_up_queue.clear()
        return snapshot

    def pop_latest_follow_up(self) -> AgentMessage | None:
        return self._follow_up_queue.pop() if self._follow_up_queue else None

    def pop_latest_steering(self) -> AgentMessage | None:
        return self._steering_queue.pop() if self._steering_queue else None

    def prompt_message(self, message: AgentMessage) -> AsyncIterator[AgentEvent]:
        """启动新的 Agent Run，追加一条消息到历史，然后运行 Loop。

        参数说明：
            message: 要追加的用户消息（可以是 UserMessage、CustomMessage 等）

        返回：
            AsyncIterator[AgentEvent]: 事件流，包含 turn_start、message_update、
            tool_execution_*、turn_end、agent_end 等事件

        前置条件：
            当前没有正在运行的 Agent Run（is_running 为 False）

        执行流程：
        1. 检查未在运行中
        2. 为之前中断的工具调用追加错误结果
        3. 设置 _running = True
        4. 调用 _run() 启动 Loop

        与 prompt() 的区别：
        - prompt() 接受字符串，自动包装为 UserMessage
        - prompt_message() 接受任意 AgentMessage，更灵活

        使用示例：
            async for event in harness.prompt_message(UserMessage(content="你好")):
                if isinstance(event, MessageEndEvent):
                    print(event.message.text)
        """
        self._ensure_not_running()
        self._append_interrupted_tool_results()
        self._running = True
        return self._run(prompts=(message,))

    def prompt(self, content: str) -> AsyncIterator[AgentEvent]:
        return self.prompt_message(UserMessage(content=content))

    def continue_(self) -> AsyncIterator[AgentEvent]:
        """从当前状态继续 Agent Run，不追加新消息。

        使用场景：
        1. 会话恢复后：从历史状态继续对话
        2. 自动重试：上下文溢出压缩后继续
        3. 队列消息：处理之前排队但尚未发送的消息
        4. 工具中断恢复：用户取消后重新运行

        返回：
            AsyncIterator[AgentEvent]: 事件流

        前置条件：
            当前没有正在运行的 Agent Run

        与 prompt() 的区别：
        - prompt(): 添加新用户消息然后运行
        - continue_(): 不添加新消息，从当前 transcript 继续

        示例：
            # 恢复会话后从历史状态继续
            async for event in harness.continue_():
                render(event)
        """
        self._ensure_not_running()
        self._append_interrupted_tool_results()
        self._running = True
        return self._run()

    async def _run(
        self,
        *,
        prompts: Sequence[AgentMessage] = (),
    ) -> AsyncIterator[AgentEvent]:
        """内部方法：运行 Agent Loop 并广播事件。

        参数说明：
            prompts: 本轮要追加到历史的新消息（可选）

        执行流程：
        1. 创建新的 SimpleCancellationToken
        2. 调用 run_agent_loop 启动纯函数式循环
        3. 将 Harness 的队列排空函数作为回调传给 Loop
        4. 将 Loop 产生的每个事件广播给所有监听者
        5. 清理：处理取消状态、重置运行标志

        关键设计——队列回调注入：
        - get_steering_messages：在工具回合结束后调用，获取引导消息
        - get_follow_up_messages：在当前 run 结束时调用，获取后续消息
        - 这样 Loop 不依赖 UI 或会话策略，保持纯函数特性

        取消处理：
        - 如果取消，为未完成的工具调用追加错误结果
        - 确保 transcript 保持自洽（每个 ToolCall 都有对应的 ToolResultMessage）
        """
        signal = SimpleCancellationToken()
        self._current_signal = signal
        try:
            # Harness 拥有唯一的可变 transcript，并保证同一时刻只有一个 loop 修改它。
            # 两个 drain callback 把队列语义注入纯循环，而不让循环依赖 UI 或会话策略。
            async for event in run_agent_loop(
                provider=self._config.provider,
                model=self._config.model,
                system=self._config.system,
                messages=self._messages,
                prompts=prompts,
                tools=self._config.tools,
                max_turns=self._config.max_turns,
                signal=signal,
                get_steering_messages=self._drain_steering_messages,
                get_follow_up_messages=self._drain_follow_up_messages,
                before_tool_call=self._config.before_tool_call,
                after_tool_call=self._config.after_tool_call,
                tool_execution=self._config.tool_execution,
            ):
                await self._notify(event)
                yield event
        finally:
            if signal.is_cancelled():
                self._append_interrupted_tool_results()
            if self._current_signal is signal:
                self._current_signal = None
            self._running = False

    async def _notify(self, event: AgentEvent) -> None:
        for listener in list(self._listeners):
            result = listener(event)
            if isawaitable(result):
                await result

    def _ensure_not_running(self) -> None:
        if self._running:
            raise RuntimeError(
                "AgentHarness is already running; use steer() or follow_up() to queue messages."
            )

    def _drain_steering_messages(self) -> tuple[AgentMessage, ...]:
        return self._drain_queue(self._steering_queue)

    def _drain_follow_up_messages(self) -> tuple[AgentMessage, ...]:
        return self._drain_queue(self._follow_up_queue)

    def _drain_queue(self, queue: deque[AgentMessage]) -> tuple[AgentMessage, ...]:
        if not queue:
            return ()
        # one_at_a_time 让模型有机会逐条响应；all 则把当前快照一次性交给下一轮。
        if self._config.queue_mode == "all":
            messages = tuple(queue)
            queue.clear()
            return messages
        return (queue.popleft(),)

    def append_interrupted_tool_results(self) -> int:
        before = len(self._messages)
        self._append_interrupted_tool_results()
        return len(self._messages) - before

    def _append_interrupted_tool_results(self) -> None:
        returned_ids = {
            message.tool_call_id
            for message in self._messages
            if isinstance(message, ToolResultMessage)
        }
        for message in tuple(self._messages):
            if not isinstance(message, AssistantMessage):
                continue
            for call in message.tool_calls:
                if call.id in returned_ids:
                    continue
                returned_ids.add(call.id)
                self._messages.append(
                    ToolResultMessage(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        content=[TextContent(text="Tool call interrupted by user")],
                        is_error=True,
                    )
                )
