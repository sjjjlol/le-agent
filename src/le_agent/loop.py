"""Pure Pi-compatible provider/tool agent loop.

本模块实现 LeAgent 的核心 Agent 循环——纯函数式的模型-工具协调器。

架构定位：
- 位于 le_agent 层，是纯函数（无状态），依赖注入所有副作用
- 不持有 transcript，不管理持久化，只做协调
- 可被 Harness 包装使用，也可独立用于测试/其他场景

核心循环结构（双层循环）：

外层循环（while True）- 处理 follow-up：
    1. 运行内层循环直到没有工具调用
    2. 检查是否有 follow-up 消息
    3. 如果有，作为下一轮 prompt 继续
    4. 如果没有，结束整个 run

内层循环（while has_more_tools or pending）- 处理模型-工具回合：
    1. 处理 steering 消息（如果有）
    2. 检查 max_turns 限制
    3. 调用 Provider 获取助手响应
    4. 若响应包含工具调用，逐个执行
    5. 将 ToolResultMessage 回填 transcript
    6. 回到步骤 1（模型会再次响应，看到工具结果）

为什么需要双层循环：
- 内层循环：模型-工具链（一个"回合"）
- 外层循环：整个 Agent Run 可能包含多个回合（follow-up 机制）

错误处理：
- Provider 错误、取消、工具异常都生成 AssistantMessage(stop_reason="error"/"aborted")
- 错误消息进入 transcript，模型在下一轮可以看到错误
- 这保持协议一致性，错误也是对话历史的一部分
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from le_agent.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from le_agent.messages import (
    AgentMessage,
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
)
from le_agent.provider import CancellationToken, ModelProvider
from le_agent.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
)
from le_agent.tools import AgentTool, AgentToolResult, ToolExecutionMode

BeforeToolCall = Callable[[ToolCall], Awaitable[tuple[bool, str | None]]]
"""工具调用前回调类型。

参数：
    ToolCall: 模型请求的工具调用

返回：
    tuple[bool, str | None]:
    - bool: 是否允许执行（False 表示阻止）
    - str | None: 阻止时的原因说明（可选）

使用场景：
- 权限检查：确认用户是否允许调用该工具
- 日志记录：记录工具调用请求
- 拦截修改：阻止某些危险操作
"""

AfterToolCall = Callable[
    [ToolCall, AgentToolResult, bool],
    Awaitable[tuple[AgentToolResult, bool]],
]
"""工具调用后回调类型。

参数：
    ToolCall: 被调用的工具
    AgentToolResult: 工具执行结果
    bool: 是否是取消导致的提前结束

返回：
    tuple[AgentToolResult, bool]:
    - AgentToolResult: 可能修改后的结果
    - bool: 是否应被视为错误

使用场景：
- 结果改写：修改工具输出（如脱敏）
- 错误标记：将某些结果标记为错误
- 后处理：添加元数据或格式化
"""


async def run_agent_loop(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    prompts: Sequence[AgentMessage] = (),
    max_turns: int | None = None,
    signal: CancellationToken | None = None,
    get_steering_messages: Callable[[], Sequence[AgentMessage]] | None = None,
    get_follow_up_messages: Callable[[], Sequence[AgentMessage]] | None = None,
    before_tool_call: BeforeToolCall | None = None,
    after_tool_call: AfterToolCall | None = None,
    tool_execution: ToolExecutionMode = "parallel",
) -> AsyncGenerator[AgentEvent, None]:
    """运行 Provider/工具循环，产生 Pi 兼容的 Agent 事件。

    这是 LeAgent 的核心引擎——纯函数式 Agent 循环。它不持有状态，所有状态通过参数传入，
    所有副作用通过回调和事件产出。

    参数说明（全部是关键字参数）：
        provider: 模型供应商适配器（如 OpenAICompatibleProvider）
        model: 模型名称（如 "gpt-4", "claude-sonnet-4-6"）
        system: 系统提示词
        messages: 可变的 transcript 列表（会被修改，添加新消息）
        tools: 可用工具列表
        prompts: 本轮要追加的新消息（可选）
        max_turns: 最大回合数限制（None 表示无限制）
        signal: 取消令牌（用于取消正在运行的循环）
        get_steering_messages: 获取 steering 消息的回调（在每回合开始时调用）
        get_follow_up_messages: 获取 follow-up 消息的回调（在每轮结束时调用）
        before_tool_call: 工具调用前回调（权限检查等）
        after_tool_call: 工具调用后回调（结果改写等）

    事件产出：
        AgentStartEvent: Run 开始
        TurnStartEvent: 新回合开始
        MessageStartEvent/MessageEndEvent: 消息追加
        MessageUpdateEvent: 助手消息流式更新
        ToolExecutionStartEvent/ToolExecutionEndEvent: 工具执行
        TurnEndEvent: 回合结束
        AgentEndEvent: Run 结束

    执行流程（双层循环）：
        外层循环 - 处理 follow-up：
            while True:
                运行内层循环
                获取 follow-up 消息
                如果有 follow-up，继续外层循环
                否则 break

        内层循环 - 处理模型-工具回合：
            while 有工具调用或 steering 消息:
                处理 steering 消息
                调用 Provider 获取助手响应
                若响应有工具调用:
                    逐个执行工具
                    将 ToolResultMessage 回填 transcript
                （循环继续，模型会再次响应，看到工具结果）

    关键设计——回填机制：
    工具结果必须成为消息（ToolResultMessage），而不能只在 UI 中打印。
    否则模型下一轮看不到文件内容或命令输出。

    错误处理：
    - Provider 错误：生成 AssistantMessage(stop_reason="error")
    - 取消：生成 AssistantMessage(stop_reason="aborted")
    - 工具异常：转成错误 AgentToolResult，模型可以看到错误信息

    示例：
        async for event in run_agent_loop(
            provider=openai_provider,
            model="gpt-4",
            system="You are a helpful assistant",
            messages=transcript,  # 会被修改
            tools=[read_tool, write_tool],
            prompts=[UserMessage(content="解释这段代码")],
        ):
            print(event.type)
    """
    # `new_messages` 只记录本次 run 新增到 transcript 的消息。
    # 这样 AgentEndEvent 可以精确返回“这一轮新增了什么”，
    # 调用方不需要自己去和旧 transcript 做 diff。
    new_messages = list(prompts)
    if prompts:
        # `messages` 是由调用方持有的可变 transcript。
        # run_agent_loop 的约定是：一旦某条 prompt 被提交到本轮，
        # 它必须立即进入 durable history，并且同时发出对应事件。
        messages.extend(prompts)

    yield AgentStartEvent()
    yield TurnStartEvent()
    for prompt in prompts:
        yield MessageStartEvent(message=prompt)
        yield MessageEndEvent(message=prompt)

    if max_turns is not None and max_turns < 1:
        error = _error_message(model, "max_turns must be at least 1")
        messages.append(error)
        new_messages.append(error)
        yield MessageStartEvent(message=error)
        yield MessageEndEvent(message=error)
        yield TurnEndEvent(message=error)
        yield AgentEndEvent(messages=new_messages)
        return

    # 工具按名称索引，避免每次 tool call 都线性扫描 `tools` 列表。
    tool_by_name = {tool.name: tool for tool in tools}
    # `turn` 统计模型回合数，而不是消息数。
    # 每次模型完整地产出一个 AssistantMessage 后，回合数才递增。
    turn = 1
    # 首轮的 TurnStartEvent 已在函数开头发出，后续轮次在循环里补发。
    first_turn = True
    # `pending` 是下一次进入模型前需要先注入 transcript 的消息。
    # 初始时只会包含 steering；follow-up 只在外层循环收尾时注入。
    pending = tuple(get_steering_messages() if get_steering_messages else ())

    # 外层循环消费 follow-up：只有工具链和后续消息都为空，整个 agent run 才结束。
    while True:
        # `has_more_tools` 表示上一条 assistant 消息是否请求了工具。
        # 初始设为 True 是为了保证至少跑一轮 provider，即使当前没有 pending。
        has_more_tools = True
        # 内层循环处理一个“模型 -> 工具 -> 模型”的链条。
        # 只要 assistant 继续请求工具，或者外部又塞进了 steering，
        # 当前大 run 就不能结束。
        while has_more_tools or pending:
            if not first_turn:
                yield TurnStartEvent()
            first_turn = False

            # `pending` 中的消息必须先落入 transcript，再让 provider 看到。
            # 这保证 steering/follow-up 与普通用户消息具有同样的历史地位，
            # 而不是只作为某种瞬时 UI 指令存在。
            for message in pending:
                messages.append(message)
                new_messages.append(message)
                yield MessageStartEvent(message=message)
                yield MessageEndEvent(message=message)
            pending = ()

            if max_turns is not None and turn > max_turns:
                error = _error_message(model, f"Agent stopped after max_turns={max_turns}")
                messages.append(error)
                new_messages.append(error)
                yield MessageStartEvent(message=error)
                yield MessageEndEvent(message=error)
                yield TurnEndEvent(message=error)
                yield AgentEndEvent(messages=new_messages)
                return

            # 每次 provider 调用都必须最终收敛成一个完整的 AssistantMessage。
            # 只有拿到最终消息，loop 才能回答两个关键问题：
            # 1. 这轮是正常结束、错误结束，还是被取消结束？
            # 2. 其中是否包含 tool calls，需要继续进入工具阶段？
            #
            # `_assistant_events()` 会把 provider 的流式事件翻译成统一 AgentEvent。
            # 这里一边转发事件给上层，一边捕获最后那个 MessageEndEvent，
            # 从中取出最终的 AssistantMessage 作为本轮决策依据。
            assistant = None
            async for event in _assistant_events(
                provider=provider,
                model=model,
                system=system,
                messages=_provider_context(messages),
                tools=tools,
                signal=signal,
            ):
                yield event
                if isinstance(event, MessageEndEvent) and isinstance(
                    event.message, AssistantMessage
                ):
                    assistant = event.message

            # 防御式兜底：正常情况下 `_assistant_events()` 一定会产出一个终结消息。
            # 如果 provider 违反协议，仍然合成一条错误 assistant message，
            # 以保持 transcript 和事件序列完整。
            if assistant is None:  # defensive: _assistant_events always terminates
                assistant = _error_message(model, "Provider produced no assistant message")
                yield MessageStartEvent(message=assistant)
                yield MessageEndEvent(message=assistant)

            messages.append(assistant)
            new_messages.append(assistant)
            # 错误或取消都是 terminal assistant turn：
            # 它们会被记入历史，但不会继续执行工具，也不会再开启后续回合。
            if assistant.stop_reason in {"error", "aborted"}:
                yield TurnEndEvent(message=assistant)
                yield AgentEndEvent(messages=new_messages)
                return

            # 工具不仅产生 UI 事件；其 ToolResultMessage 还要回填 transcript，
            # 下一轮模型请求才能读到真实执行结果。
            tool_results: list[ToolResultMessage] = []
            # 先冻结 tool calls，避免后续处理过程中读取一个可能变化的序列视图。
            calls = list(assistant.tool_calls)
            has_more_tools = bool(calls)
            async for event in _execute_tool_calls(
                calls,
                tool_by_name,
                signal,
                before_tool_call,
                after_tool_call,
                tool_execution=tool_execution,
            ):
                yield event
                if isinstance(event, MessageEndEvent) and isinstance(
                    event.message, ToolResultMessage
                ):
                    tool_results.append(event.message)
                    messages.append(event.message)
                    new_messages.append(event.message)

            yield TurnEndEvent(message=assistant, tool_results=tool_results)
            turn += 1
            # 一个回合结束后立即抓取 steering。
            # 这使得用户可以在工具刚跑完时“插话”，影响下一次模型响应，
            # 而不必等整个 run 彻底结束。
            pending = tuple(get_steering_messages() if get_steering_messages else ())

        # follow-up 与 steering 不同：它不会打断当前工具链。
        # 只有当内层循环自然耗尽（没有更多工具，也没有 pending steering）时，
        # follow-up 才会被当成下一轮新的输入，重新启动内层回合。
        follow_ups = tuple(get_follow_up_messages() if get_follow_up_messages else ())
        if follow_ups:
            pending = follow_ups
            continue
        break

    yield AgentEndEvent(messages=new_messages)


def _provider_context(messages: list[AgentMessage]) -> list[AgentMessage]:
    """Return replayable messages while retaining failures in durable history.

    Providers cannot consistently accept an assistant turn with no content. LeAgent
    persists terminal failures for diagnostics, but an empty failed or aborted
    turn is not model context and must not poison the next request.
    """
    return [
        message
        for message in messages
        if not (
            isinstance(message, AssistantMessage)
            and message.stop_reason in {"error", "aborted"}
            and not message.content
        )
    ]


async def _assistant_events(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    signal: CancellationToken | None,
) -> AsyncGenerator[AgentEvent, None]:
    source: AsyncIterator[AssistantMessageEvent] = provider.stream_response(
        model=model,
        system=system,
        messages=messages,
        tools=tools,
        signal=signal,
    )
    started = False
    async for event in source:
        if isinstance(event, AssistantStartEvent):
            started = True
            yield MessageStartEvent(message=event.partial)
        elif isinstance(event, AssistantDoneEvent):
            if not started:
                yield MessageStartEvent(message=event.message)
            yield MessageEndEvent(message=event.message)
        elif isinstance(event, AssistantErrorEvent):
            if not started:
                yield MessageStartEvent(message=event.error)
            yield MessageEndEvent(message=event.error)
        else:
            yield MessageUpdateEvent(
                message=event.partial,
                assistant_message_event=event,
            )


@dataclass(frozen=True, slots=True)
class _PreparedToolCall:
    call: ToolCall
    tool: AgentTool | None
    immediate_result: AgentToolResult | None = None
    immediate_is_error: bool = False


@dataclass(frozen=True, slots=True)
class _WorkerDone:
    index: int
    message: ToolResultMessage


@dataclass(frozen=True, slots=True)
class _WorkerFailed:
    error: BaseException


async def _execute_tool_calls(
    calls: list[ToolCall],
    tools: Mapping[str, AgentTool],
    signal: CancellationToken | None,
    before_tool_call: BeforeToolCall | None,
    after_tool_call: AfterToolCall | None,
    *,
    tool_execution: ToolExecutionMode,
) -> AsyncGenerator[AgentEvent, None]:
    run_in_parallel = tool_execution == "parallel" and all(
        tool is None or tool.execution_mode == "parallel"
        for call in calls
        for tool in (tools.get(call.name),)
    )
    if not run_in_parallel:
        for call in calls:
            yield _tool_start_event(call)
            prepared = await _prepare_tool_call(call, tools, signal, before_tool_call)
            outcomes: list[ToolResultMessage | None] = [None]
            async for event in _stream_tool_workers([prepared], signal, after_tool_call, outcomes):
                yield event
            message = outcomes[0]
            if message is None:
                raise RuntimeError(f"Tool worker produced no result: {call.name}")
            yield MessageStartEvent(message=message)
            yield MessageEndEvent(message=message)
        return

    prepared_calls: list[_PreparedToolCall] = []
    for call in calls:
        yield _tool_start_event(call)
        prepared_calls.append(await _prepare_tool_call(call, tools, signal, before_tool_call))

    outcomes = [None] * len(prepared_calls)
    async for event in _stream_tool_workers(prepared_calls, signal, after_tool_call, outcomes):
        yield event
    for prepared, message in zip(prepared_calls, outcomes, strict=True):
        if message is None:
            raise RuntimeError(f"Tool worker produced no result: {prepared.call.name}")
        yield MessageStartEvent(message=message)
        yield MessageEndEvent(message=message)


def _tool_start_event(call: ToolCall) -> ToolExecutionStartEvent:
    return ToolExecutionStartEvent(
        tool_call_id=call.id,
        tool_name=call.name,
        args=dict(call.arguments),
    )


async def _prepare_tool_call(
    call: ToolCall,
    tools: Mapping[str, AgentTool],
    signal: CancellationToken | None,
    before_tool_call: BeforeToolCall | None,
) -> _PreparedToolCall:
    tool = tools.get(call.name)
    if tool is None:
        return _PreparedToolCall(
            call=call,
            tool=None,
            immediate_result=_error_result(f"Tool {call.name} not found"),
            immediate_is_error=True,
        )
    prepared_call = call
    if tool.prepare_arguments is not None:
        try:
            arguments = dict(tool.prepare_arguments(call.arguments))
            prepared_call = call.model_copy(update={"arguments": arguments})
        except Exception as exc:  # noqa: BLE001 - argument preparation is a tool boundary
            return _PreparedToolCall(
                call=call,
                tool=None,
                immediate_result=_error_result(str(exc)),
                immediate_is_error=True,
            )
    if before_tool_call is not None:
        blocked, block_reason = await before_tool_call(prepared_call)
        if blocked:
            return _PreparedToolCall(
                call=prepared_call,
                tool=None,
                immediate_result=_error_result(block_reason or "Tool execution was blocked"),
                immediate_is_error=True,
            )
    if signal is not None and signal.is_cancelled():
        return _PreparedToolCall(
            call=prepared_call,
            tool=None,
            immediate_result=_error_result("Operation aborted"),
            immediate_is_error=True,
        )
    return _PreparedToolCall(call=prepared_call, tool=tool)


async def _stream_tool_workers(
    prepared_calls: list[_PreparedToolCall],
    signal: CancellationToken | None,
    after_tool_call: AfterToolCall | None,
    outcomes: list[ToolResultMessage | None],
) -> AsyncGenerator[AgentEvent, None]:
    queue: asyncio.Queue[AgentEvent | _WorkerDone | _WorkerFailed] = asyncio.Queue()
    tasks = [
        asyncio.create_task(_run_tool_worker(index, prepared, signal, after_tool_call, queue))
        for index, prepared in enumerate(prepared_calls)
    ]
    completed = 0
    try:
        while completed < len(tasks):
            item = await queue.get()
            if isinstance(item, _WorkerDone):
                outcomes[item.index] = item.message
                completed += 1
            elif isinstance(item, _WorkerFailed):
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise item.error
            else:
                yield item
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _run_tool_worker(
    index: int,
    prepared: _PreparedToolCall,
    signal: CancellationToken | None,
    after_tool_call: AfterToolCall | None,
    queue: asyncio.Queue[AgentEvent | _WorkerDone | _WorkerFailed],
) -> None:
    call = prepared.call

    def on_update(partial: AgentToolResult) -> None:
        queue.put_nowait(
            ToolExecutionUpdateEvent(
                tool_call_id=call.id,
                tool_name=call.name,
                args=dict(call.arguments),
                partial_result=partial.model_copy(deep=True),
            )
        )

    try:
        if prepared.immediate_result is not None:
            result = prepared.immediate_result
            is_error = prepared.immediate_is_error
        else:
            tool = prepared.tool
            if tool is None:
                raise RuntimeError(f"Prepared tool is missing: {call.name}")
            try:
                result = await tool.execute(call.id, call.arguments, signal, on_update)
                is_error = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - tools are an isolation boundary
                result = _error_result(str(exc))
                is_error = True

        if after_tool_call is not None:
            result, is_error = await after_tool_call(call, result, is_error)

        queue.put_nowait(
            ToolExecutionEndEvent(
                tool_call_id=call.id,
                tool_name=call.name,
                result=result,
                is_error=is_error,
            )
        )
        queue.put_nowait(
            _WorkerDone(
                index=index,
                message=ToolResultMessage(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    content=result.content,
                    details=result.details,
                    added_tool_names=result.added_tool_names,
                    is_error=is_error,
                ),
            )
        )
    except BaseException as exc:
        queue.put_nowait(_WorkerFailed(exc))


def _error_result(message: str) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=message)], details={})


def _error_message(model: str, message: str) -> AssistantMessage:
    return AssistantMessage(
        model=model,
        content=[],
        stop_reason="error",
        error_message=message,
    )
