from __future__ import annotations

import hashlib
import subprocess
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest

from conftest import isolate_home
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
    TextDeltaEvent,
    ToolCallEndEvent,
)
from le_agent.tools import AgentTool
from le_agent_ai import FakeProvider
from le_agent_coding.benchmark import runner as runner_module
from le_agent_coding.benchmark.fake import create_fake_provider
from le_agent_coding.benchmark.models import BenchmarkTask, EvaluationResult, TrialStatus
from le_agent_coding.benchmark.runner import BenchmarkRunnerConfig, run_benchmark
from le_agent_coding.benchmark.tasks import load_builtin_tasks


@pytest.fixture(autouse=True)
def _isolate_benchmark_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_home(monkeypatch, tmp_path)


def _task(task_id: str) -> BenchmarkTask:
    return next(task for task in load_builtin_tasks() if task.id == task_id)


def _config(
    tmp_path: Path,
    provider_factory: Callable[[BenchmarkTask, int], ModelProvider],
    **changes: object,
) -> BenchmarkRunnerConfig:
    config = BenchmarkRunnerConfig(
        provider_name="fake",
        model="benchmark-fake",
        trials=1,
        seed=300,
        keep_workspaces=False,
        workspace_root=tmp_path / "workspaces",
        provider_factory=provider_factory,
    )
    return replace(config, **changes)


def _text_events(text: str) -> list[AssistantMessageEvent]:
    message = AssistantMessage(model="benchmark-fake", content=[TextContent(text=text)])
    return [
        AssistantStartEvent(partial=AssistantMessage(model="benchmark-fake")),
        TextDeltaEvent(content_index=0, delta=text, partial=message),
        AssistantDoneEvent(reason="stop", message=message),
    ]


def _tool_events(call: ToolCall) -> list[AssistantMessageEvent]:
    message = AssistantMessage(model="benchmark-fake", content=[call], stop_reason="toolUse")
    return [
        AssistantStartEvent(partial=AssistantMessage(model="benchmark-fake")),
        ToolCallEndEvent(content_index=0, tool_call=call, partial=message),
        AssistantDoneEvent(reason="toolUse", message=message),
    ]


class _RaisingProvider:
    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, messages, tools, signal

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            raise RuntimeError("provider exploded " + "x" * 4_000)
            yield  # pragma: no cover

        return iterator()


class _HangingProvider:
    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, messages, tools, signal

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            await anyio.sleep_forever()
            yield  # pragma: no cover

        return iterator()


class _CancellationObservingProvider:
    def __init__(self, observed_signal_states: list[bool]) -> None:
        self._observed_signal_states = observed_signal_states

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, messages, tools

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            assert signal is not None
            try:
                await anyio.sleep_forever()
            except anyio.get_cancelled_exc_class():
                with anyio.CancelScope(shield=True):
                    await anyio.sleep(0.01)
                    self._observed_signal_states.append(signal.is_cancelled())
                raise
            yield  # pragma: no cover

        return iterator()


class _CancellationErrorProvider:
    def __init__(self, error: Exception, observed_signal_states: list[bool]) -> None:
        self._error = error
        self._observed_signal_states = observed_signal_states

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, messages, tools

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            assert signal is not None
            try:
                await anyio.sleep_forever()
            except anyio.get_cancelled_exc_class():
                with anyio.CancelScope(shield=True):
                    await anyio.sleep(0.01)
                    self._observed_signal_states.append(signal.is_cancelled())
                    raise self._error from None
            yield  # pragma: no cover

        return iterator()


class _ExternalCancellationProvider:
    def __init__(self, started: anyio.Event, observed_signal_states: list[bool]) -> None:
        self._started = started
        self._observed_signal_states = observed_signal_states

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, messages, tools

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            assert signal is not None
            self._started.set()
            try:
                await anyio.sleep_forever()
            except anyio.get_cancelled_exc_class():
                with anyio.CancelScope(shield=True):
                    self._observed_signal_states.append(signal.is_cancelled())
                raise
            yield  # pragma: no cover

        return iterator()


class _TimeoutRaisingProvider:
    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, messages, tools, signal

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            raise TimeoutError("provider timed out")
            yield  # pragma: no cover

        return iterator()


class _CwdRecordingProvider:
    def __init__(self, observed_cwds: list[Path]) -> None:
        self._observed_cwds = observed_cwds
        self._call_count = 0

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, tools, signal
        self._call_count += 1
        if self._call_count == 1:
            events = _tool_events(
                ToolCall(
                    id="record-cwd",
                    name="bash",
                    arguments={"command": "printf changed > app.py; pwd"},
                )
            )
        else:
            tool_result = next(
                message
                for message in reversed(messages)
                if isinstance(message, ToolResultMessage) and message.tool_call_id == "record-cwd"
            )
            self._observed_cwds.append(Path(tool_result.text.strip()))
            events = _text_events(
                "welcome/service.py 中的 build_welcome 调用了 "
                "welcome/render.py 中的 format_salutation。"
            )

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            for event in events:
                yield event

        return iterator()


class _ToolOutputRecordingProvider:
    def __init__(self, command: str, outputs: list[str]) -> None:
        self._command = command
        self._outputs = outputs
        self._call_count = 0

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del model, system, tools, signal
        self._call_count += 1
        if self._call_count == 1:
            events = _tool_events(
                ToolCall(id="record-output", name="bash", arguments={"command": self._command})
            )
        else:
            tool_result = next(
                message
                for message in reversed(messages)
                if isinstance(message, ToolResultMessage)
                and message.tool_call_id == "record-output"
            )
            self._outputs.append(tool_result.text)
            events = _text_events(
                "welcome/service.py 中的 build_welcome 调用了 "
                "welcome/render.py 中的 format_salutation。"
            )

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            for event in events:
                yield event

        return iterator()


@pytest.mark.anyio
async def test_run_benchmark_solves_all_tasks_through_coding_session(tmp_path: Path) -> None:
    tasks = load_builtin_tasks()
    workspace_root = tmp_path / "workspaces"

    run = await run_benchmark(
        _config(
            tmp_path,
            lambda task, trial: create_fake_provider(task.id),
            workspace_root=workspace_root,
        ),
        tasks,
    )

    assert run.task_ids == tuple(task.id for task in tasks)
    assert run.metrics.task_count == len(tasks)
    assert [result.status for result in run.results] == ["passed", "passed", "passed"]
    assert run.metrics.success_rate == 1.0
    assert all(result.trajectory for result in run.results)
    assert not workspace_root.exists() or not any(workspace_root.iterdir())


@pytest.mark.anyio
async def test_runner_passes_every_prompt_event_to_collector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded_event_types: list[str] = []
    original_record = runner_module.TrajectoryCollector.record

    def record_all_events(self: object, event: object) -> None:
        recorded_event_types.append(type(event).__name__)
        original_record(self, event)  # type: ignore[arg-type]

    monkeypatch.setattr(runner_module.TrajectoryCollector, "record", record_all_events)

    await run_benchmark(
        _config(tmp_path, lambda task, trial: create_fake_provider(task.id)),
        (_task("repository_lookup"),),
    )

    assert "AgentStartEvent" in recorded_event_types
    assert "AgentSettledEvent" in recorded_event_types
    assert "MessageEndEvent" in recorded_event_types


@pytest.mark.anyio
async def test_trials_use_isolated_workspaces_without_mutating_fixture(tmp_path: Path) -> None:
    task = _task("repository_lookup")
    fixture_file = task.fixture_path / "app.py"
    fixture_hash = hashlib.sha256(fixture_file.read_bytes()).hexdigest()
    observed_cwds: list[Path] = []
    observed_trials: list[int] = []

    def provider_factory(task: BenchmarkTask, trial: int) -> ModelProvider:
        observed_trials.append(trial)
        return _CwdRecordingProvider(observed_cwds)

    run = await run_benchmark(
        _config(
            tmp_path,
            provider_factory,
            trials=2,
        ),
        (task,),
    )

    assert [result.trial for result in run.results] == [1, 2]
    assert observed_trials == [1, 2]
    assert len(set(observed_cwds)) == 2
    assert all(path.is_relative_to(tmp_path / "workspaces") for path in observed_cwds)
    assert hashlib.sha256(fixture_file.read_bytes()).hexdigest() == fixture_hash
    assert all(not path.exists() for path in observed_cwds)


@pytest.mark.anyio
async def test_keep_workspaces_returns_existing_workspace(tmp_path: Path) -> None:
    task = _task("targeted_edit")

    run = await run_benchmark(
        _config(
            tmp_path,
            lambda task, trial: create_fake_provider(task.id),
            keep_workspaces=True,
        ),
        (task,),
    )

    workspace = run.results[0].workspace
    assert workspace is not None
    assert Path(workspace).is_dir()
    assert (Path(workspace) / "pricing.py").is_file()


@pytest.mark.anyio
async def test_shell_command_prefix_is_applied_to_benchmark_bash_tool(tmp_path: Path) -> None:
    outputs: list[str] = []

    run = await run_benchmark(
        _config(
            tmp_path,
            lambda task, trial: _ToolOutputRecordingProvider(
                'printf "$LE_AGENT_BENCH_PREFIX"', outputs
            ),
            shell_command_prefix="export LE_AGENT_BENCH_PREFIX=applied",
        ),
        (_task("repository_lookup"),),
    )

    assert run.results[0].status is TrialStatus.passed
    assert outputs == ["applied"]


@pytest.mark.anyio
async def test_agent_failure_is_bounded_and_next_task_still_runs(tmp_path: Path) -> None:
    tasks = (_task("repository_lookup"), _task("targeted_edit"))
    calls: list[str] = []

    def provider_factory(task: BenchmarkTask, trial: int) -> ModelProvider:
        calls.append(task.id)
        if task.id == "repository_lookup":
            return _RaisingProvider()
        return create_fake_provider(task.id)

    run = await run_benchmark(_config(tmp_path, provider_factory), tasks)

    assert [result.status for result in run.results] == [
        TrialStatus.agent_failure,
        TrialStatus.passed,
    ]
    assert calls == ["repository_lookup", "targeted_edit"]
    assert run.results[0].error is not None
    assert "provider exploded" in run.results[0].error
    assert len(run.results[0].error) <= 2_000


@pytest.mark.anyio
async def test_agent_error_event_skips_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluated = False

    def unexpected_evaluation(
        task: BenchmarkTask, workspace: Path, final_text: str
    ) -> EvaluationResult:
        nonlocal evaluated
        evaluated = True
        raise AssertionError("agent error 后不应评分")

    monkeypatch.setattr(runner_module, "evaluate", unexpected_evaluation)
    provider = FakeProvider(
        [
            [
                AssistantErrorEvent(
                    reason="error",
                    error=AssistantMessage(stop_reason="error", error_message="quota exhausted"),
                )
            ]
        ]
    )

    run = await run_benchmark(
        _config(tmp_path, lambda task, trial: provider),
        (_task("repository_lookup"),),
    )

    assert run.results[0].status is TrialStatus.agent_failure
    assert run.results[0].error == "AgentError: quota exhausted"
    assert evaluated is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error_message", "expected_error"),
    [
        (None, "AgentError: agent stopped with aborted"),
        ("cancelled upstream", "AgentError: cancelled upstream"),
    ],
)
async def test_aborted_agent_skips_evaluation_and_preserves_available_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_message: str | None,
    expected_error: str,
) -> None:
    evaluated = False

    def unexpected_evaluation(
        task: BenchmarkTask, workspace: Path, final_text: str
    ) -> EvaluationResult:
        nonlocal evaluated
        evaluated = True
        raise AssertionError("aborted agent 后不应评分")

    monkeypatch.setattr(runner_module, "evaluate", unexpected_evaluation)
    provider = FakeProvider(
        [
            [
                AssistantErrorEvent(
                    reason="aborted",
                    error=AssistantMessage(
                        stop_reason="aborted",
                        error_message=error_message,
                    ),
                )
            ]
        ]
    )

    run = await run_benchmark(
        _config(tmp_path, lambda task, trial: provider),
        (_task("repository_lookup"),),
    )

    assert run.results[0].status is TrialStatus.agent_failure
    assert run.results[0].error == expected_error
    assert evaluated is False


@pytest.mark.anyio
async def test_trial_timeout_is_classified_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = replace(_task("repository_lookup"), timeout_seconds=0.01)
    workspace_root = tmp_path / "workspaces"
    cancel_calls = 0
    close_calls = 0
    original_cancel = runner_module.CodingSession.cancel
    original_aclose = runner_module.CodingSession.aclose

    def record_cancel(session: object) -> None:
        nonlocal cancel_calls
        cancel_calls += 1
        original_cancel(session)  # type: ignore[arg-type]

    async def record_aclose(session: object) -> None:
        nonlocal close_calls
        close_calls += 1
        await original_aclose(session)  # type: ignore[arg-type]

    monkeypatch.setattr(runner_module.CodingSession, "cancel", record_cancel)
    monkeypatch.setattr(runner_module.CodingSession, "aclose", record_aclose)

    run = await run_benchmark(
        _config(
            tmp_path,
            lambda task, trial: _HangingProvider(),
            workspace_root=workspace_root,
        ),
        (task,),
    )

    assert run.results[0].status is TrialStatus.timeout
    assert run.results[0].error is not None
    assert cancel_calls == 1
    assert close_calls == 1
    assert not any(workspace_root.iterdir())


@pytest.mark.anyio
async def test_trial_timeout_cancels_agent_signal_before_prompt_task(
    tmp_path: Path,
) -> None:
    task = replace(_task("repository_lookup"), timeout_seconds=0.01)
    workspace_root = tmp_path / "workspaces"
    observed_signal_states: list[bool] = []

    run = await run_benchmark(
        _config(
            tmp_path,
            lambda task, trial: _CancellationObservingProvider(observed_signal_states),
            workspace_root=workspace_root,
        ),
        (task,),
    )

    assert run.results[0].status is TrialStatus.timeout
    assert observed_signal_states == [True]
    assert not any(workspace_root.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider_error",
    (RuntimeError("cancel cleanup failed"), TimeoutError("cancel cleanup timed out")),
    ids=("runtime-error", "timeout-error"),
)
async def test_trial_deadline_wins_over_provider_cancellation_error(
    tmp_path: Path,
    provider_error: Exception,
) -> None:
    task = replace(_task("repository_lookup"), timeout_seconds=0.01)
    observed_signal_states: list[bool] = []

    run = await run_benchmark(
        _config(
            tmp_path,
            lambda task, trial: _CancellationErrorProvider(provider_error, observed_signal_states),
        ),
        (task,),
    )

    assert observed_signal_states == [True]
    assert run.results[0].status is TrialStatus.timeout
    assert run.results[0].error == "TimeoutError: trial 超过 0.01 秒"


@pytest.mark.anyio
async def test_provider_timeout_error_is_an_agent_failure(tmp_path: Path) -> None:
    run = await run_benchmark(
        _config(tmp_path, lambda task, trial: _TimeoutRaisingProvider()),
        (_task("repository_lookup"),),
    )

    assert run.results[0].status is TrialStatus.agent_failure
    assert run.results[0].error == "TimeoutError: provider timed out"


@pytest.mark.anyio
async def test_evaluator_exception_is_evaluation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_evaluation(task: BenchmarkTask, workspace: Path, final_text: str) -> EvaluationResult:
        del task, workspace, final_text
        raise RuntimeError("evaluator exploded")

    monkeypatch.setattr(runner_module, "evaluate", fail_evaluation)

    run = await run_benchmark(
        _config(tmp_path, lambda task, trial: create_fake_provider(task.id)),
        (_task("repository_lookup"),),
    )

    assert run.results[0].status is TrialStatus.evaluation_failure
    assert run.results[0].error == "RuntimeError: evaluator exploded"


@pytest.mark.anyio
async def test_evaluation_failure_keeps_primary_cause_when_session_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleanup_events: list[str] = []
    original_rmtree = runner_module.shutil.rmtree

    def fail_evaluation(task: BenchmarkTask, workspace: Path, final_text: str) -> EvaluationResult:
        del task, workspace, final_text
        raise RuntimeError("evaluator exploded")

    async def fail_close(session: object) -> None:
        del session
        cleanup_events.append("close")
        raise RuntimeError("close exploded")

    def record_rmtree(path: Path) -> None:
        cleanup_events.append("rmtree")
        original_rmtree(path)

    monkeypatch.setattr(runner_module, "evaluate", fail_evaluation)
    monkeypatch.setattr(runner_module.CodingSession, "aclose", fail_close)
    monkeypatch.setattr(runner_module.shutil, "rmtree", record_rmtree)

    run = await run_benchmark(
        _config(tmp_path, lambda task, trial: create_fake_provider(task.id)),
        (_task("repository_lookup"),),
    )

    result = run.results[0]
    assert result.status is TrialStatus.evaluation_failure
    assert result.error is not None
    assert result.error.startswith("RuntimeError: evaluator exploded")
    assert "清理 session 失败: RuntimeError: close exploded" in result.error
    assert cleanup_events == ["close", "rmtree"]


@pytest.mark.anyio
async def test_bounded_primary_error_still_keeps_secondary_cleanup_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_evaluation(task: BenchmarkTask, workspace: Path, final_text: str) -> EvaluationResult:
        del task, workspace, final_text
        raise RuntimeError("primary exploded " + "x" * 4_000)

    async def fail_close(session: object) -> None:
        del session
        raise RuntimeError("close exploded")

    monkeypatch.setattr(runner_module, "evaluate", fail_evaluation)
    monkeypatch.setattr(runner_module.CodingSession, "aclose", fail_close)

    run = await run_benchmark(
        _config(tmp_path, lambda task, trial: create_fake_provider(task.id)),
        (_task("repository_lookup"),),
    )

    result = run.results[0]
    assert result.status is TrialStatus.evaluation_failure
    assert result.error is not None
    assert result.error.startswith("RuntimeError: primary exploded")
    assert "清理 session 失败: RuntimeError: close exploded" in result.error
    assert len(result.error) <= 2_000


@pytest.mark.anyio
async def test_timeout_keeps_primary_cause_and_all_cleanup_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = replace(_task("repository_lookup"), timeout_seconds=0.01)
    cleanup_events: list[str] = []

    async def fail_close(session: object) -> None:
        del session
        cleanup_events.append("close")
        raise RuntimeError("close exploded")

    def fail_rmtree(path: Path) -> None:
        del path
        cleanup_events.append("rmtree")
        raise OSError("rmtree exploded")

    monkeypatch.setattr(runner_module.CodingSession, "aclose", fail_close)
    monkeypatch.setattr(runner_module.shutil, "rmtree", fail_rmtree)

    run = await run_benchmark(
        _config(tmp_path, lambda task, trial: _HangingProvider()),
        (task,),
    )

    result = run.results[0]
    assert result.status is TrialStatus.timeout
    assert result.error is not None
    assert result.error.startswith("TimeoutError: trial 超过 0.01 秒")
    assert "清理 session 失败: RuntimeError: close exploded" in result.error
    assert "清理 workspace 失败: OSError: rmtree exploded" in result.error
    assert cleanup_events == ["close", "rmtree"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_factory", "initial_status"),
    [
        (lambda task, trial: create_fake_provider(task.id), TrialStatus.passed),
        (
            lambda task, trial: FakeProvider([_text_events("不知道。")]),
            TrialStatus.task_failed,
        ),
    ],
    ids=("passed", "task-failed"),
)
async def test_success_or_task_failure_uses_consistent_cleanup_failure_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_factory: Callable[[BenchmarkTask, int], ModelProvider],
    initial_status: TrialStatus,
) -> None:
    cleanup_events: list[str] = []
    original_rmtree = runner_module.shutil.rmtree

    async def fail_close(session: object) -> None:
        del session
        cleanup_events.append("close")
        raise RuntimeError("close exploded")

    def record_rmtree(path: Path) -> None:
        cleanup_events.append("rmtree")
        original_rmtree(path)

    monkeypatch.setattr(runner_module.CodingSession, "aclose", fail_close)
    monkeypatch.setattr(runner_module.shutil, "rmtree", record_rmtree)

    run = await run_benchmark(
        _config(tmp_path, provider_factory),
        (_task("repository_lookup"),),
    )

    result = run.results[0]
    assert initial_status in {TrialStatus.passed, TrialStatus.task_failed}
    assert result.status is TrialStatus.agent_failure
    assert result.reward == 0
    assert result.checks == ()
    assert result.error is not None
    assert result.error.startswith("CleanupError:")
    assert "清理 session 失败: RuntimeError: close exploded" in result.error
    assert cleanup_events == ["close", "rmtree"]


@pytest.mark.anyio
@pytest.mark.parametrize("close_fails", [False, True], ids=("close-ok", "close-fails"))
async def test_external_cancellation_signals_and_shields_all_cleanup_before_reraising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    close_fails: bool,
) -> None:
    provider_started = anyio.Event()
    observed_signal_states: list[bool] = []
    cleanup_events: list[str] = []
    caught: list[BaseException] = []
    returned_runs: list[object] = []
    workspace_root = tmp_path / "workspaces"
    original_cancel = runner_module.CodingSession.cancel
    original_rmtree = runner_module.shutil.rmtree

    def record_cancel(session: object) -> None:
        cleanup_events.append("cancel")
        original_cancel(session)  # type: ignore[arg-type]

    async def controlled_close(session: object) -> None:
        del session
        cleanup_events.append("close-start")
        await anyio.sleep(0)
        cleanup_events.append("close-finish")
        if close_fails:
            raise RuntimeError("close exploded")

    def record_rmtree(path: Path) -> None:
        cleanup_events.append("rmtree")
        original_rmtree(path)

    monkeypatch.setattr(runner_module.CodingSession, "cancel", record_cancel)
    monkeypatch.setattr(runner_module.CodingSession, "aclose", controlled_close)
    monkeypatch.setattr(runner_module.shutil, "rmtree", record_rmtree)

    async def invoke() -> None:
        try:
            returned_runs.append(
                await run_benchmark(
                    _config(
                        tmp_path,
                        lambda task, trial: _ExternalCancellationProvider(
                            provider_started, observed_signal_states
                        ),
                        workspace_root=workspace_root,
                    ),
                    (_task("repository_lookup"),),
                )
            )
        except BaseException as exc:
            caught.append(exc)
            raise

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await provider_started.wait()
        task_group.cancel_scope.cancel()

    assert returned_runs == []
    assert len(caught) == 1
    assert isinstance(caught[0], anyio.get_cancelled_exc_class())
    assert observed_signal_states == [True]
    assert cleanup_events == ["cancel", "close-start", "close-finish", "rmtree"]
    assert not workspace_root.exists() or not any(workspace_root.iterdir())


@pytest.mark.anyio
async def test_external_cancellation_during_close_is_not_swallowed_by_shield(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    close_started = anyio.Event()
    allow_close = anyio.Event()
    events: list[str] = []
    caught: list[BaseException] = []
    returned_runs: list[object] = []
    original_cancel = runner_module.CodingSession.cancel
    original_rmtree = runner_module.shutil.rmtree

    def record_cancel(session: object) -> None:
        events.append("cancel")
        original_cancel(session)  # type: ignore[arg-type]

    async def controlled_close(session: object) -> None:
        del session
        events.append("close-start")
        close_started.set()
        await allow_close.wait()
        events.append("close-finish")

    def record_rmtree(path: Path) -> None:
        events.append("rmtree")
        original_rmtree(path)

    monkeypatch.setattr(runner_module.CodingSession, "cancel", record_cancel)
    monkeypatch.setattr(runner_module.CodingSession, "aclose", controlled_close)
    monkeypatch.setattr(runner_module.shutil, "rmtree", record_rmtree)

    async def invoke() -> None:
        try:
            returned_runs.append(
                await run_benchmark(
                    _config(tmp_path, lambda task, trial: create_fake_provider(task.id)),
                    (_task("repository_lookup"),),
                )
            )
        except BaseException as exc:
            caught.append(exc)
            raise

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await close_started.wait()
        task_group.cancel_scope.cancel()
        allow_close.set()

    assert returned_runs == []
    assert len(caught) == 1
    assert isinstance(caught[0], anyio.get_cancelled_exc_class())
    assert events == ["close-start", "close-finish", "rmtree", "cancel"]


@pytest.mark.anyio
async def test_failed_check_is_task_failed_without_error(tmp_path: Path) -> None:
    run = await run_benchmark(
        _config(tmp_path, lambda task, trial: FakeProvider([_text_events("不知道。")])),
        (_task("repository_lookup"),),
    )

    result = run.results[0]
    assert result.status is TrialStatus.task_failed
    assert result.reward == 0
    assert result.checks
    assert result.error is None


@pytest.mark.anyio
async def test_run_metadata_contains_reproducibility_fields(tmp_path: Path) -> None:
    before = datetime.now(UTC)
    run = await run_benchmark(
        _config(tmp_path, lambda task, trial: create_fake_provider(task.id)),
        (_task("repository_lookup"),),
    )
    after = datetime.now(UTC)

    started_at = datetime.fromisoformat(run.started_at)
    finished_at = datetime.fromisoformat(run.finished_at)
    assert before <= started_at <= finished_at <= after
    assert started_at.utcoffset() == UTC.utcoffset(started_at)
    assert run.seed == 300
    assert run.provider == "fake"
    assert run.model == "benchmark-fake"
    assert run.task_ids == ("repository_lookup",)
    assert run.requested_trials == 1
    assert len(run.run_id) == 32
    assert run.git_commit is None or len(run.git_commit) == 40
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert run.git_commit == expected_commit


@pytest.mark.anyio
@pytest.mark.parametrize("tasks", [(), (_task("repository_lookup"),)])
async def test_invalid_config_or_empty_tasks_fail_before_provider_call(
    tmp_path: Path, tasks: tuple[BenchmarkTask, ...]
) -> None:
    calls = 0

    def provider_factory(task: BenchmarkTask, trial: int) -> ModelProvider:
        nonlocal calls
        calls += 1
        return create_fake_provider(task.id)

    config = _config(tmp_path, provider_factory, trials=0 if tasks else 1)

    with pytest.raises(ValueError):
        await run_benchmark(config, tasks)

    assert calls == 0


@pytest.mark.anyio
async def test_workspace_root_creation_failure_happens_before_provider_call(tmp_path: Path) -> None:
    workspace_root = tmp_path / "not-a-directory"
    workspace_root.write_text("occupied", encoding="utf-8")
    called = False

    def provider_factory(task: BenchmarkTask, trial: int) -> ModelProvider:
        nonlocal called
        called = True
        return create_fake_provider(task.id)

    with pytest.raises(OSError):
        await run_benchmark(
            _config(tmp_path, provider_factory, workspace_root=workspace_root),
            (_task("repository_lookup"),),
        )

    assert called is False
