"""内置 benchmark 的确定性 fake provider 脚本。"""

from __future__ import annotations

from collections.abc import Callable

from le_agent.messages import AssistantMessage, TextContent, ToolCall, Usage
from le_agent.types import JSONValue
from le_agent_ai.events import (
    AssistantDoneEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    ToolCallEndEvent,
)
from le_agent_ai.fake import FakeProvider

_MODEL = "benchmark-fake"


def _tool_response(
    call_id: str,
    name: str,
    arguments: dict[str, JSONValue],
) -> list[AssistantMessageEvent]:
    """构造请求一次真实工具执行的完整 assistant 流。"""
    call = ToolCall(id=call_id, name=name, arguments=arguments)
    message = AssistantMessage(
        model=_MODEL,
        content=[call],
        usage=Usage(input=5, output=2, total_tokens=7),
        stop_reason="toolUse",
    )
    return [
        AssistantStartEvent(partial=AssistantMessage(model=_MODEL)),
        ToolCallEndEvent(content_index=0, tool_call=call, partial=message),
        AssistantDoneEvent(reason="toolUse", message=message),
    ]


def _text_response(text: str) -> list[AssistantMessageEvent]:
    """构造带非零用量的最终文本 assistant 流。"""
    message = AssistantMessage(
        model=_MODEL,
        content=[TextContent(text=text)],
        usage=Usage(input=5, output=3, total_tokens=8),
        stop_reason="stop",
    )
    return [
        AssistantStartEvent(partial=AssistantMessage(model=_MODEL)),
        TextDeltaEvent(content_index=0, delta=text, partial=message),
        AssistantDoneEvent(reason="stop", message=message),
    ]


def _repository_lookup_script() -> list[list[AssistantMessageEvent]]:
    """返回定位问候语实现所需的只读工具回合。"""
    return [
        _tool_response("lookup-read-app", "read", {"path": "app.py"}),
        _tool_response("lookup-read-service", "read", {"path": "welcome/service.py"}),
        _tool_response("lookup-read-render", "read", {"path": "welcome/render.py"}),
        _text_response(
            "最终实现位于 welcome/render.py 的 format_salutation；build_welcome 调用了该函数。"
        ),
    ]


def _targeted_edit_script() -> list[list[AssistantMessageEvent]]:
    """返回调整免运费门槛所需的读取和精确编辑回合。"""
    return [
        _tool_response("pricing-read", "read", {"path": "pricing.py"}),
        _tool_response(
            "pricing-edit",
            "edit",
            {
                "path": "pricing.py",
                "edits": [{"oldText": "total < 50", "newText": "total < 75"}],
            },
        ),
        _text_response("已将普通用户免运费门槛从 50 调整为 75，会员规则保持不变。"),
    ]


def _failing_test_fix_script() -> list[list[AssistantMessageEvent]]:
    """返回修复 slugify 并验证结果所需的工具回合。"""
    return [
        _tool_response(
            "slugify-test-before",
            "bash",
            {"command": "python -m unittest discover -s tests -t . -q"},
        ),
        _tool_response("slugify-read", "read", {"path": "slugify.py"}),
        _tool_response(
            "slugify-edit",
            "edit",
            {
                "path": "slugify.py",
                "edits": [{"oldText": '.split(" ")', "newText": ".split()"}],
            },
        ),
        _tool_response(
            "slugify-test-after",
            "bash",
            {"command": "python -m unittest discover -s tests -t . -q"},
        ),
        _text_response("已修复 slugify 的空白分隔逻辑，并重新运行测试确认通过。"),
    ]


type FakeScript = Callable[[], list[list[AssistantMessageEvent]]]

_SCRIPTS: dict[str, FakeScript] = {
    "repository_lookup": _repository_lookup_script,
    "targeted_edit": _targeted_edit_script,
    "failing_test_fix": _failing_test_fix_script,
}

_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    "repository_lookup": ("read", "read", "read"),
    "targeted_edit": ("read", "edit"),
    "failing_test_fix": ("bash", "read", "edit", "bash"),
}


def create_fake_provider(task_id: str) -> FakeProvider:
    """为一个 trial 创建独立的、会消费流队列的 fake provider。"""
    try:
        script = _SCRIPTS[task_id]
    except KeyError as error:
        raise ValueError(f"未知 fake benchmark task: {task_id}") from error
    return FakeProvider(script())


def fake_tool_names(task_id: str) -> tuple[str, ...]:
    """返回脚本会发出的工具名称，供诊断和教学说明使用。"""
    try:
        return _TOOL_NAMES[task_id]
    except KeyError as error:
        raise ValueError(f"未知 fake benchmark task: {task_id}") from error
