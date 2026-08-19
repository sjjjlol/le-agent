"""Benchmark 运行过程使用的不可变数据模型。"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from le_agent.types import JSONValue


class TrialStatus(StrEnum):
    """表示单次 benchmark trial 的终止状态。"""

    passed = "passed"
    task_failed = "task_failed"
    agent_failure = "agent_failure"
    timeout = "timeout"
    evaluation_failure = "evaluation_failure"


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    """表示可执行的内置 benchmark task。"""

    id: str
    title: str
    prompt: str
    timeout_seconds: float
    fixture_path: Path
    evaluator: str


@dataclass(frozen=True, slots=True)
class EvaluationCheck:
    """表示 evaluator 执行的一项检查。"""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """表示 evaluator 对一次 trial 的结果。"""

    reward: int
    checks: tuple[EvaluationCheck, ...]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """记录 provider 报告的 token 用量。"""

    input: int
    output: int
    cache_read: int
    cache_write: int
    reasoning: int | None
    total_tokens: int


@dataclass(frozen=True, slots=True)
class TrajectoryStep:
    """记录 trial 轨迹中的一个步骤。"""

    sequence: int
    elapsed_ms: int
    kind: str
    text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, JSONValue] | None = None
    result: str | None = None
    is_error: bool | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class TrialResult:
    """表示单次 task trial 的完整结果。"""

    task_id: str
    trial: int
    status: TrialStatus
    reward: int
    checks: tuple[EvaluationCheck, ...]
    duration_seconds: float
    tool_calls: int
    tool_errors: int
    usage: TokenUsage | None
    final_text: str
    trajectory: tuple[TrajectoryStep, ...]
    error: str | None = None
    workspace: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkMetrics:
    """汇总一个 benchmark run 的聚合指标。"""

    trials: int
    task_count: int
    passed: int
    task_failed: int
    timed_out: int
    infrastructure_failed: int
    success_rate: float
    mean_duration_seconds: float
    mean_duration_seconds_by_task: dict[str, float]
    mean_tool_calls: float
    tool_errors: int
    tool_error_rate: float
    usage: TokenUsage | None
    pass_at_k: dict[str, dict[int, float]]
    macro_pass_at_k: dict[int, float]


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    """表示一次可复现的 benchmark 执行记录。"""

    schema_version: int
    run_id: str
    started_at: str
    finished_at: str
    provider: str
    model: str
    seed: int
    task_ids: tuple[str, ...]
    requested_trials: int
    git_commit: str | None
    results: tuple[TrialResult, ...]
    metrics: BenchmarkMetrics
