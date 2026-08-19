"""将 coding session event 归一化为 benchmark trajectory。"""

from collections.abc import Callable
from time import monotonic

from le_agent.events import MessageEndEvent, ToolExecutionEndEvent, ToolExecutionStartEvent
from le_agent.messages import AssistantMessage, Usage
from le_agent.types import JSONValue
from le_agent_coding.benchmark.models import TokenUsage, TrajectoryStep
from le_agent_coding.events import CodingSessionEvent

MAX_RESULT_CHARS = 4_000
_TRUNCATION_SUFFIX = "…[已截断]"


class TrajectoryCollector:
    """采集单个 coding session 的稳定 benchmark 诊断数据。

    collector 只保留 assistant 完整消息及工具执行的开始、结束事件，避免把流式
    partial snapshot 重复写入 artifact；它不改变 session 或 agent loop 的行为。
    """

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self._steps: list[TrajectoryStep] = []
        self._final_text = ""
        self._tool_calls = 0
        self._tool_errors = 0
        self._agent_error: str | None = None
        self._usage_input = 0
        self._usage_output = 0
        self._usage_cache_read = 0
        self._usage_cache_write = 0
        self._usage_reasoning: int | None = None
        self._usage_total_tokens = 0
        self._has_reported_usage = False

    @property
    def steps(self) -> tuple[TrajectoryStep, ...]:
        """返回已归一化的 trajectory 步骤。"""
        return tuple(self._steps)

    @property
    def final_text(self) -> str:
        """返回最后一个 assistant 完整消息中的可见文本。"""
        return self._final_text

    @property
    def tool_calls(self) -> int:
        """返回已开始执行的工具调用数。"""
        return self._tool_calls

    @property
    def tool_errors(self) -> int:
        """返回以 error 结束的工具调用数。"""
        return self._tool_errors

    @property
    def usage(self) -> TokenUsage | None:
        """返回 provider 上报的累计用量；未上报时返回 ``None``。"""
        if not self._has_reported_usage:
            return None
        return TokenUsage(
            input=self._usage_input,
            output=self._usage_output,
            cache_read=self._usage_cache_read,
            cache_write=self._usage_cache_write,
            reasoning=self._usage_reasoning,
            total_tokens=self._usage_total_tokens,
        )

    @property
    def agent_error(self) -> str | None:
        """返回最后一个 provider error assistant 消息的错误详情。"""
        return self._agent_error

    def record(self, event: CodingSessionEvent) -> None:
        """记录一个 session event 中可用于 benchmark 诊断的部分。"""
        if isinstance(event, ToolExecutionStartEvent):
            self._tool_calls += 1
            self._append_step(
                kind="tool_execution_start",
                tool_name=event.tool_name,
                tool_call_id=event.tool_call_id,
                arguments=dict(event.args),
            )
            return

        if isinstance(event, ToolExecutionEndEvent):
            result, truncated = _bounded_result(event.result.text)
            if event.is_error:
                self._tool_errors += 1
            self._append_step(
                kind="tool_execution_end",
                tool_name=event.tool_name,
                tool_call_id=event.tool_call_id,
                result=result,
                is_error=event.is_error,
                truncated=truncated,
            )
            return

        if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
            message = event.message
            is_error = message.stop_reason == "error"
            self._final_text = message.text
            self._record_usage(message.usage)
            if is_error:
                self._agent_error = message.error_message
            self._append_step(
                kind="assistant_message",
                text=message.text,
                is_error=is_error,
            )

    def _append_step(
        self,
        *,
        kind: str,
        text: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        arguments: dict[str, JSONValue] | None = None,
        result: str | None = None,
        is_error: bool | None = None,
        truncated: bool = False,
    ) -> None:
        self._steps.append(
            TrajectoryStep(
                sequence=len(self._steps),
                elapsed_ms=int((self._clock() - self._started_at) * 1_000),
                kind=kind,
                text=text,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                arguments=arguments,
                result=result,
                is_error=is_error,
                truncated=truncated,
            )
        )

    def _record_usage(self, usage: Usage) -> None:
        counts = (
            usage.input,
            usage.output,
            usage.cache_read,
            usage.cache_write,
            usage.reasoning or 0,
            usage.total_tokens,
        )
        if any(counts):
            self._has_reported_usage = True

        self._usage_input += usage.input
        self._usage_output += usage.output
        self._usage_cache_read += usage.cache_read
        self._usage_cache_write += usage.cache_write
        self._usage_total_tokens += usage.total_tokens
        if usage.reasoning is not None:
            self._usage_reasoning = (self._usage_reasoning or 0) + usage.reasoning


def _bounded_result(result: str) -> tuple[str, bool]:
    """限制 tool 输出长度，避免 benchmark artifact 被单个结果撑大。"""
    if len(result) <= MAX_RESULT_CHARS:
        return result, False
    return result[: MAX_RESULT_CHARS - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX, True
