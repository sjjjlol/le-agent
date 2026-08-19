from le_agent.events import MessageEndEvent, ToolExecutionEndEvent, ToolExecutionStartEvent
from le_agent.messages import AssistantMessage, TextContent, Usage
from le_agent.tools import AgentToolResult
from le_agent_coding.benchmark.collector import MAX_RESULT_CHARS, TrajectoryCollector


def test_collector_tracks_tools_usage_and_final_text() -> None:
    collector = TrajectoryCollector(clock=lambda: 10.0)
    collector.record(
        ToolExecutionStartEvent(tool_call_id="call-1", tool_name="read", args={"path": "app.py"})
    )
    collector.record(
        ToolExecutionEndEvent(
            tool_call_id="call-1",
            tool_name="read",
            result=AgentToolResult(content=[TextContent(text="source")]),
            is_error=False,
        )
    )
    collector.record(
        MessageEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="最终答案")],
                usage=Usage(input=11, output=7, total_tokens=18),
            )
        )
    )

    assert collector.final_text == "最终答案"
    assert collector.tool_calls == 1
    assert collector.tool_errors == 0
    assert collector.usage is not None
    assert collector.usage.total_tokens == 18
    assert [step.kind for step in collector.steps] == [
        "tool_execution_start",
        "tool_execution_end",
        "assistant_message",
    ]


def test_collector_marks_error_and_truncates_large_tool_result() -> None:
    collector = TrajectoryCollector(clock=lambda: 10.0)
    collector.record(
        ToolExecutionEndEvent(
            tool_call_id="call-1",
            tool_name="bash",
            result=AgentToolResult(content=[TextContent(text="x" * (MAX_RESULT_CHARS + 1))]),
            is_error=True,
        )
    )

    assert collector.tool_errors == 1
    assert collector.steps[0].truncated is True
    assert (collector.steps[0].result or "").endswith("…[已截断]")
    assert len(collector.steps[0].result or "") <= MAX_RESULT_CHARS


def test_collector_sums_usage_from_multiple_assistant_messages() -> None:
    collector = TrajectoryCollector(clock=lambda: 10.0)
    collector.record(
        MessageEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="中间回复")],
                usage=Usage(
                    input=11,
                    output=7,
                    cache_read=3,
                    cache_write=2,
                    reasoning=5,
                    total_tokens=28,
                ),
            )
        )
    )
    collector.record(
        MessageEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="最终回复")],
                usage=Usage(
                    input=13,
                    output=9,
                    cache_read=4,
                    cache_write=6,
                    total_tokens=32,
                ),
            )
        )
    )

    assert collector.final_text == "最终回复"
    assert collector.usage is not None
    assert collector.usage.input == 24
    assert collector.usage.output == 16
    assert collector.usage.cache_read == 7
    assert collector.usage.cache_write == 8
    assert collector.usage.reasoning == 5
    assert collector.usage.total_tokens == 60


def test_collector_does_not_double_count_one_hour_cache_writes() -> None:
    collector = TrajectoryCollector(clock=lambda: 10.0)
    collector.record(
        MessageEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="完成")],
                usage=Usage(
                    input=5,
                    output=3,
                    cache_write=7,
                    cache_write_1h=11,
                    total_tokens=15,
                ),
            )
        )
    )

    assert collector.usage is not None
    # cache_write 已包含 provider 的总写入量，分层字段不能再次累加。
    assert collector.usage.cache_write == 7


def test_collector_records_provider_error_without_inventing_usage() -> None:
    collector = TrajectoryCollector(clock=lambda: 10.0)
    collector.record(
        MessageEndEvent(
            message=AssistantMessage(
                stop_reason="error",
                error_message="provider failed",
            )
        )
    )

    assert collector.agent_error == "provider failed"
    assert collector.usage is None
    assert collector.steps[0].is_error is True
