"""Benchmark trial 的可复现指标聚合。"""

from collections import defaultdict
from math import comb

from le_agent_coding.benchmark.models import BenchmarkMetrics, TokenUsage, TrialResult, TrialStatus


def estimate_pass_at_k(n: int, c: int, k: int) -> float:
    """用无偏估计计算 ``n`` 次 trial 中 ``c`` 次成功的 pass@k。"""
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (n, c, k)):
        raise ValueError("n, c, and k must be integers")
    if n < 1:
        raise ValueError("n must be at least 1")
    if not 0 <= c <= n:
        raise ValueError("c must be between 0 and n")
    if not 1 <= k <= n:
        raise ValueError("k must be between 1 and n")

    return 1.0 - (comb(n - c, k) / comb(n, k) if n - c >= k else 0.0)


def calculate_metrics(results: tuple[TrialResult, ...]) -> BenchmarkMetrics:
    """汇总所有 trial，并保留每个 task 的 pass@k 与可选用量。"""
    trials = len(results)
    passed = sum(result.status is TrialStatus.passed for result in results)
    task_failed = sum(result.status is TrialStatus.task_failed for result in results)
    timed_out = sum(result.status is TrialStatus.timeout for result in results)
    infrastructure_failed = trials - passed - task_failed
    total_duration = sum(result.duration_seconds for result in results)
    total_tool_calls = sum(result.tool_calls for result in results)
    total_tool_errors = sum(result.tool_errors for result in results)

    per_task: dict[str, list[TrialResult]] = defaultdict(list)
    for result in results:
        per_task[result.task_id].append(result)

    mean_duration_seconds_by_task = {
        task_id: sum(result.duration_seconds for result in task_results) / len(task_results)
        for task_id, task_results in per_task.items()
    }
    pass_at_k: dict[str, dict[int, float]] = {}
    macro_values: dict[int, list[float]] = defaultdict(list)
    for task_id, task_results in per_task.items():
        task_trials = len(task_results)
        task_passed = sum(result.status is TrialStatus.passed for result in task_results)
        task_pass_at_k = {
            k: estimate_pass_at_k(n=task_trials, c=task_passed, k=k)
            for k in range(1, task_trials + 1)
        }
        pass_at_k[task_id] = task_pass_at_k
        for k, value in task_pass_at_k.items():
            macro_values[k].append(value)

    return BenchmarkMetrics(
        trials=trials,
        task_count=len(per_task),
        passed=passed,
        task_failed=task_failed,
        timed_out=timed_out,
        infrastructure_failed=infrastructure_failed,
        success_rate=passed / trials if trials else 0.0,
        mean_duration_seconds=total_duration / trials if trials else 0.0,
        mean_duration_seconds_by_task=mean_duration_seconds_by_task,
        mean_tool_calls=total_tool_calls / trials if trials else 0.0,
        tool_errors=total_tool_errors,
        tool_error_rate=total_tool_errors / total_tool_calls if total_tool_calls else 0.0,
        usage=_aggregate_usage(results),
        pass_at_k=pass_at_k,
        macro_pass_at_k={
            k: sum(values) / len(values) for k, values in sorted(macro_values.items())
        },
    )


def _aggregate_usage(results: tuple[TrialResult, ...]) -> TokenUsage | None:
    """只在至少一个 provider 上报用量时返回总和。"""
    usages = [result.usage for result in results if result.usage is not None]
    if not usages:
        return None

    reasoning_values = [usage.reasoning for usage in usages if usage.reasoning is not None]
    return TokenUsage(
        input=sum(usage.input for usage in usages),
        output=sum(usage.output for usage in usages),
        cache_read=sum(usage.cache_read for usage in usages),
        cache_write=sum(usage.cache_write for usage in usages),
        reasoning=sum(reasoning_values) if reasoning_values else None,
        total_tokens=sum(usage.total_tokens for usage in usages),
    )
