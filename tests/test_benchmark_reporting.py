import json
from pathlib import Path

import pytest
from rich.console import Console

from le_agent_coding.benchmark.models import (
    BenchmarkMetrics,
    BenchmarkRun,
    TokenUsage,
    TrialResult,
    TrialStatus,
)
from le_agent_coding.benchmark.reporting import render_benchmark_summary, write_benchmark_run


@pytest.fixture
def sample_run() -> BenchmarkRun:
    """构造序列化和摘要输出均可复用的 benchmark run。"""
    result = TrialResult(
        task_id="repair-file",
        trial=1,
        status=TrialStatus.passed,
        reward=1,
        checks=(),
        duration_seconds=1.25,
        tool_calls=2,
        tool_errors=0,
        usage=TokenUsage(10, 20, 0, 0, None, 30),
        final_text="完成",
        trajectory=(),
    )
    metrics = BenchmarkMetrics(
        trials=1,
        task_count=1,
        passed=1,
        task_failed=0,
        timed_out=0,
        infrastructure_failed=0,
        success_rate=1.0,
        mean_duration_seconds=1.25,
        mean_duration_seconds_by_task={"repair-file": 1.25},
        mean_tool_calls=2.0,
        tool_errors=0,
        tool_error_rate=0.0,
        usage=TokenUsage(10, 20, 0, 0, None, 30),
        pass_at_k={"repair-file": {1: 1.0}},
        macro_pass_at_k={1: 1.0},
    )
    return BenchmarkRun(
        schema_version=1,
        run_id="run-1",
        started_at="2026-08-08T00:00:00Z",
        finished_at="2026-08-08T00:00:01Z",
        provider="fake",
        model="fake-model",
        seed=1,
        task_ids=("repair-file",),
        requested_trials=1,
        git_commit=None,
        results=(result,),
        metrics=metrics,
    )


def test_write_benchmark_run_uses_camel_free_versioned_json(
    tmp_path: Path, sample_run: BenchmarkRun
) -> None:
    path = write_benchmark_run(sample_run, tmp_path / "nested" / "run.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["task_ids"] == ["repair-file"]
    assert payload["results"][0]["status"] == "passed"
    assert payload["metrics"]["task_count"] == 1
    assert payload["metrics"]["tool_errors"] == 0
    assert payload["metrics"]["mean_duration_seconds_by_task"] == {"repair-file": 1.25}
    assert payload["metrics"]["pass_at_k"]["repair-file"]["1"] == 1.0
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert not list(path.parent.glob("*.tmp"))


def test_render_benchmark_summary_explains_fake_mode(sample_run: BenchmarkRun) -> None:
    console = Console(record=True, width=120)
    render_benchmark_summary(sample_run, artifact_path=Path("artifacts/run.json"), console=console)

    text = console.export_text()
    assert "成功率" in text
    assert "task 数" in text
    assert "工具错误总数" in text
    assert "逐 task 平均时长" in text
    assert "repair-file: 1.25s" in text
    assert "repair-file" in text
    assert "tokens" in text
    assert "artifacts/run.json" in text
    assert "fake 模式只验证评测管线，不代表模型能力" in text
