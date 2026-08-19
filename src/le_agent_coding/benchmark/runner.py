"""串行运行隔离的 LeAgent 原生 agent benchmark。"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

import anyio

from le_agent.events import MessageEndEvent
from le_agent.messages import AssistantMessage
from le_agent.provider import ModelProvider
from le_agent.session import SessionStorage
from le_agent.session.entries import SessionEntry
from le_agent_coding.benchmark.collector import TrajectoryCollector
from le_agent_coding.benchmark.evaluators import evaluate
from le_agent_coding.benchmark.metrics import calculate_metrics
from le_agent_coding.benchmark.models import (
    BenchmarkRun,
    BenchmarkTask,
    EvaluationCheck,
    TrialResult,
    TrialStatus,
)
from le_agent_coding.session import CodingSession, CodingSessionConfig
from le_agent_coding.tools import create_coding_tools

BENCHMARK_SYSTEM_PROMPT = """\
你正在一个一次性隔离 workspace 中完成编码任务。请围绕用户给出的目标工作，可以使用提供的
读取、写入、编辑和命令工具。必须实际检查相关代码，并在完成修改后运行适当的验证。最后简洁
说明完成结果和验证情况。
"""

_MAX_ERROR_CHARS = 2_000
_MAX_CLEANUP_DIAGNOSTIC_CHARS = 500
_TRUNCATION_SUFFIX = "…[已截断]"
_GIT_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class BenchmarkRunnerConfig:
    """配置一次串行 benchmark run。"""

    provider_name: str
    model: str
    trials: int
    seed: int
    keep_workspaces: bool
    workspace_root: Path
    provider_factory: Callable[[BenchmarkTask, int], ModelProvider]
    shell_command_prefix: str | None = None


class _MemorySessionStorage(SessionStorage):
    """为单个 trial 保存最小的内存 session 历史。"""

    def __init__(self) -> None:
        self._entries: list[SessionEntry] = []

    async def append(self, entry: SessionEntry) -> None:
        self._entries.append(entry)

    async def read_all(self) -> list[SessionEntry]:
        return list(self._entries)


async def run_benchmark(
    config: BenchmarkRunnerConfig,
    tasks: Iterable[BenchmarkTask],
) -> BenchmarkRun:
    """按 task 和 trial 顺序运行 benchmark，并返回完整聚合结果。"""
    selected_tasks = tuple(tasks)
    if config.trials < 1:
        raise ValueError("trials 必须至少为 1")
    if not selected_tasks:
        raise ValueError("benchmark tasks 不能为空")

    # 在调用 provider factory 前验证根目录，避免配置错误产生部分 trial。
    config.workspace_root.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex
    started_at = _utc_now()
    results: list[TrialResult] = []
    for task_index, task in enumerate(selected_tasks, start=1):
        for trial in range(1, config.trials + 1):
            workspace = config.workspace_root / f"{run_id}-{task_index}-{trial}"
            results.append(
                await _run_trial(
                    config=config,
                    task=task,
                    trial=trial,
                    workspace=workspace,
                )
            )

    finished_at = _utc_now()
    frozen_results = tuple(results)
    return BenchmarkRun(
        schema_version=1,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        provider=config.provider_name,
        model=config.model,
        seed=config.seed,
        task_ids=tuple(task.id for task in selected_tasks),
        requested_trials=config.trials,
        git_commit=_git_commit(),
        results=frozen_results,
        metrics=calculate_metrics(frozen_results),
    )


async def _run_trial(
    *,
    config: BenchmarkRunnerConfig,
    task: BenchmarkTask,
    trial: int,
    workspace: Path,
) -> TrialResult:
    started_at = monotonic()
    collector = TrajectoryCollector()
    session: CodingSession | None = None
    status = TrialStatus.agent_failure
    reward = 0
    checks: tuple[EvaluationCheck, ...] = ()
    error: str | None = None
    agent_failure: str | None = None
    cleanup_diagnostics: list[str] = []
    external_cancellation: BaseException | None = None
    session_cancelled = False

    def cancel_session() -> None:
        """只发送一次 session cancellation，并保留通知失败诊断。"""
        nonlocal session_cancelled
        if session is None or session_cancelled:
            return
        session_cancelled = True
        try:
            session.cancel()
        except Exception as exc:
            cleanup_diagnostics.append(_cleanup_error_summary("取消 session 失败", exc))

    try:
        shutil.copytree(task.fixture_path, workspace)
        provider = config.provider_factory(task, trial)
        session = await CodingSession.load(
            CodingSessionConfig(
                provider=provider,
                model=config.model,
                storage=_MemorySessionStorage(),
                cwd=workspace,
                system=BENCHMARK_SYSTEM_PROMPT,
                tools=create_coding_tools(
                    cwd=workspace,
                    shell_command_prefix=config.shell_command_prefix,
                ),
                provider_name=config.provider_name,
                shell_command_prefix=config.shell_command_prefix,
                auto_compact_enabled=False,
                skills_enabled=False,
                extensions_enabled=False,
                project_extensions_enabled=False,
            )
        )
        prompt_complete = anyio.Event()
        prompt_error: Exception | None = None
        timed_out = False
        prompt_cancel_scope = anyio.CancelScope(shield=True)

        async def consume_prompt() -> None:
            nonlocal agent_failure, prompt_error
            with prompt_cancel_scope:
                try:
                    # 每个 prompt event 都先进入 collector；runner 只额外识别终止状态。
                    async for event in session.prompt(task.prompt):
                        collector.record(event)
                        if (
                            isinstance(event, MessageEndEvent)
                            and isinstance(event.message, AssistantMessage)
                            and event.message.stop_reason in {"error", "aborted"}
                        ):
                            agent_failure = (
                                event.message.error_message
                                or collector.agent_error
                                or (f"agent stopped with {event.message.stop_reason}")
                            )
                except Exception as exc:
                    prompt_error = exc
                finally:
                    prompt_complete.set()

        try:
            async with anyio.create_task_group() as prompt_group:
                prompt_group.start_soon(consume_prompt)
                try:
                    with anyio.fail_after(task.timeout_seconds):
                        await prompt_complete.wait()
                except TimeoutError:
                    # 先通知依赖 LeAgent token 的 provider/tool，再取消并等待 prompt task。
                    cancel_session()
                    prompt_cancel_scope.cancel()
                    timed_out = True
                except anyio.get_cancelled_exc_class():
                    # 外部 cancellation 必须先到达 LeAgent token，随后再取消 prompt task。
                    cancel_session()
                    prompt_cancel_scope.cancel()
                    with anyio.CancelScope(shield=True):
                        await prompt_complete.wait()
                    raise
            if not timed_out and prompt_error is not None:
                raise prompt_error
        except Exception as exc:
            status = TrialStatus.agent_failure
            error = _error_summary(exc)
        else:
            if timed_out:
                status = TrialStatus.timeout
                error = _bounded_text(f"TimeoutError: trial 超过 {task.timeout_seconds:g} 秒")
            elif agent_failure is not None:
                status = TrialStatus.agent_failure
                error = _bounded_text(f"AgentError: {agent_failure}")
            else:
                try:
                    evaluation = evaluate(task, workspace, collector.final_text)
                except Exception as exc:
                    status = TrialStatus.evaluation_failure
                    error = _error_summary(exc)
                else:
                    reward = evaluation.reward
                    checks = evaluation.checks
                    status = (
                        TrialStatus.passed if evaluation.reward == 1 else TrialStatus.task_failed
                    )
    except anyio.get_cancelled_exc_class() as exc:
        external_cancellation = exc
        cancel_session()
        raise
    except Exception as exc:
        status = TrialStatus.agent_failure
        error = _error_summary(exc)
    finally:
        # 已取消 scope 中的 await 会立刻再次取消；shield 保证 close 与删除都获得机会。
        with anyio.CancelScope(shield=True):
            if session is not None:
                try:
                    await session.aclose()
                except Exception as exc:
                    cleanup_diagnostics.append(_cleanup_error_summary("清理 session 失败", exc))
            if not config.keep_workspaces and workspace.exists():
                try:
                    shutil.rmtree(workspace)
                except OSError as exc:
                    cleanup_diagnostics.append(_cleanup_error_summary("清理 workspace 失败", exc))

        if external_cancellation is not None:
            for diagnostic in cleanup_diagnostics:
                external_cancellation.add_note(diagnostic)
        elif cleanup_diagnostics:
            cleanup_detail = "\n".join(cleanup_diagnostics)
            if status in {TrialStatus.passed, TrialStatus.task_failed}:
                status = TrialStatus.agent_failure
                reward = 0
                checks = ()
                error = _bounded_text(f"CleanupError: {cleanup_detail}")
            else:
                error = _append_cleanup_diagnostics(error, cleanup_diagnostics)

        if external_cancellation is None:
            try:
                await anyio.lowlevel.checkpoint_if_cancelled()
            except anyio.get_cancelled_exc_class() as exc:
                cancel_session()
                for diagnostic in cleanup_diagnostics:
                    exc.add_note(diagnostic)
                raise

    return TrialResult(
        task_id=task.id,
        trial=trial,
        status=status,
        reward=reward,
        checks=checks,
        duration_seconds=monotonic() - started_at,
        tool_calls=collector.tool_calls,
        tool_errors=collector.tool_errors,
        usage=collector.usage,
        final_text=collector.final_text,
        trajectory=collector.steps,
        error=error,
        workspace=str(workspace) if config.keep_workspaces and workspace.exists() else None,
    )


def _utc_now() -> str:
    """返回带 UTC offset 的 ISO 8601 时间。"""
    return datetime.now(UTC).isoformat()


def _git_commit() -> str | None:
    """尽力读取当前 git commit；任何失败都不影响 benchmark。"""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and commit else None


def _error_summary(error: Exception) -> str:
    return _bounded_text(f"{type(error).__name__}: {error}")


def _cleanup_error_summary(operation: str, error: Exception) -> str:
    return _bounded_text(
        f"{operation}: {type(error).__name__}: {error}",
        max_chars=_MAX_CLEANUP_DIAGNOSTIC_CHARS,
    )


def _append_cleanup_diagnostics(primary: str | None, diagnostics: list[str]) -> str:
    cleanup_detail = "\n".join(diagnostics)
    if primary is None:
        return _bounded_text(cleanup_detail)
    primary_limit = _MAX_ERROR_CHARS - len(cleanup_detail) - 1
    if primary_limit <= len(_TRUNCATION_SUFFIX):  # pragma: no cover - 最多只有三条有界诊断
        return _bounded_text(cleanup_detail)
    return f"{_bounded_text(primary, max_chars=primary_limit)}\n{cleanup_detail}"


def _bounded_text(text: str, *, max_chars: int = _MAX_ERROR_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX
