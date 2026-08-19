"""Benchmark artifact 的版本化 JSON 落盘与 Rich 摘要输出。"""

import json
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile

from rich.console import Console
from rich.table import Table

from le_agent_coding.benchmark.models import BenchmarkRun, TrialResult


def write_benchmark_run(run: BenchmarkRun, path: Path) -> Path:
    """将 benchmark run 原子写入版本化、稳定排序的 JSON artifact。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(
                _json_compatible(asdict(run)),
                temporary_file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            temporary_file.write("\n")
            temporary_file.flush()
        temporary_path.replace(path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return path


def render_benchmark_summary(
    run: BenchmarkRun,
    artifact_path: Path | None = None,
    console: Console | None = None,
) -> None:
    """向 Rich console 输出逐 trial 与总体指标的简明摘要。"""
    output = console or Console()
    if run.provider == "fake":
        output.print("[yellow]fake 模式只验证评测管线，不代表模型能力[/yellow]")

    trials_table = Table(title=f"Benchmark 结果：{run.run_id}")
    trials_table.add_column("task")
    trials_table.add_column("trial", justify="right")
    trials_table.add_column("status")
    trials_table.add_column("reward", justify="right")
    trials_table.add_column("duration", justify="right")
    trials_table.add_column("tool calls", justify="right")
    trials_table.add_column("errors", justify="right")
    trials_table.add_column("tokens", justify="right")
    for result in run.results:
        trials_table.add_row(*_trial_row(result))
    output.print(trials_table)

    metrics = run.metrics
    summary_table = Table(title="总体指标")
    summary_table.add_column("指标")
    summary_table.add_column("值", justify="right")
    summary_table.add_row("task 数", str(metrics.task_count))
    summary_table.add_row("trial 数", str(metrics.trials))
    summary_table.add_row("成功率", f"{metrics.success_rate:.1%}")
    summary_table.add_row("通过", f"{metrics.passed}/{metrics.trials}")
    summary_table.add_row("任务失败", str(metrics.task_failed))
    summary_table.add_row("基础设施失败", str(metrics.infrastructure_failed))
    summary_table.add_row("超时", str(metrics.timed_out))
    summary_table.add_row("平均时长", f"{metrics.mean_duration_seconds:.2f}s")
    per_task_duration = ", ".join(
        f"{task_id}: {duration:.2f}s"
        for task_id, duration in metrics.mean_duration_seconds_by_task.items()
    )
    summary_table.add_row("逐 task 平均时长", per_task_duration or "-")
    summary_table.add_row("平均工具调用", f"{metrics.mean_tool_calls:.2f}")
    summary_table.add_row("工具错误总数", str(metrics.tool_errors))
    summary_table.add_row("工具错误率", f"{metrics.tool_error_rate:.1%}")
    for k, value in sorted(metrics.macro_pass_at_k.items()):
        summary_table.add_row(f"macro pass@{k}", f"{value:.1%}")
    if artifact_path is not None:
        summary_table.add_row("artifact", str(artifact_path))
    output.print(summary_table)


def _trial_row(result: TrialResult) -> tuple[str, ...]:
    """把单个 trial 格式化成 Rich 表格的一行。"""
    tokens = "-" if result.usage is None else str(result.usage.total_tokens)
    return (
        result.task_id,
        str(result.trial),
        result.status.value,
        str(result.reward),
        f"{result.duration_seconds:.2f}s",
        str(result.tool_calls),
        str(result.tool_errors),
        tokens,
    )


def _json_compatible(value: object) -> object:
    """递归转换 dataclass 展开结果中的 StrEnum 和 tuple。"""
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    return value
