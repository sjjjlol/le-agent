import pytest

from le_agent_coding.benchmark.metrics import calculate_metrics, estimate_pass_at_k
from le_agent_coding.benchmark.models import TokenUsage, TrialResult, TrialStatus


def _result(
    task_id: str,
    status: TrialStatus,
    *,
    reward: int,
    tool_calls: int,
    duration_seconds: float = 2.0,
    tool_errors: int = 0,
    usage: TokenUsage | None = None,
) -> TrialResult:
    """构造指标测试所需的最小完整 trial。"""
    return TrialResult(
        task_id=task_id,
        trial=1,
        status=status,
        reward=reward,
        checks=(),
        duration_seconds=duration_seconds,
        tool_calls=tool_calls,
        tool_errors=tool_errors,
        usage=usage,
        final_text="",
        trajectory=(),
    )


def test_estimate_pass_at_k_uses_unbiased_formula() -> None:
    assert estimate_pass_at_k(n=4, c=2, k=1) == pytest.approx(0.5)
    assert estimate_pass_at_k(n=4, c=2, k=2) == pytest.approx(5 / 6)
    assert estimate_pass_at_k(n=4, c=0, k=4) == 0.0


@pytest.mark.parametrize(
    ("n", "c", "k"),
    [
        (0, 0, 1),
        (2, -1, 1),
        (2, 3, 1),
        (2, 1, 0),
        (2, 1, 3),
    ],
)
def test_estimate_pass_at_k_rejects_invalid_values(n: int, c: int, k: int) -> None:
    with pytest.raises(ValueError):
        estimate_pass_at_k(n=n, c=c, k=k)


def test_calculate_metrics_keeps_task_failure_separate_from_infrastructure_failure() -> None:
    results = (
        _result("one", TrialStatus.passed, reward=1, tool_calls=2),
        _result("one", TrialStatus.task_failed, reward=0, tool_calls=1),
        _result("two", TrialStatus.timeout, reward=0, tool_calls=0),
    )

    metrics = calculate_metrics(results)

    assert metrics.task_count == 2
    assert metrics.success_rate == pytest.approx(1 / 3)
    assert metrics.task_failed == 1
    assert metrics.timed_out == 1
    assert metrics.infrastructure_failed == 1
    assert metrics.pass_at_k["one"][1] == pytest.approx(0.5)
    assert metrics.pass_at_k["one"][2] == 1.0
    assert metrics.macro_pass_at_k == {1: pytest.approx(0.25), 2: 1.0}
    assert metrics.mean_duration_seconds == pytest.approx(2.0)
    assert metrics.mean_tool_calls == pytest.approx(1.0)
    assert metrics.tool_error_rate == 0.0


def test_calculate_metrics_aggregates_reported_usage_and_uses_all_trial_denominators() -> None:
    results = (
        _result(
            "one",
            TrialStatus.passed,
            reward=1,
            tool_calls=2,
            tool_errors=1,
            duration_seconds=3.0,
            usage=TokenUsage(2, 3, 5, 7, 11, 28),
        ),
        _result(
            "two",
            TrialStatus.agent_failure,
            reward=0,
            tool_calls=0,
            duration_seconds=1.0,
        ),
        _result(
            "three",
            TrialStatus.evaluation_failure,
            reward=0,
            tool_calls=1,
            duration_seconds=2.0,
            usage=TokenUsage(13, 17, 19, 23, None, 72),
        ),
    )

    metrics = calculate_metrics(results)

    assert metrics.tool_errors == 1
    assert metrics.mean_duration_seconds_by_task == {
        "one": pytest.approx(3.0),
        "two": pytest.approx(1.0),
        "three": pytest.approx(2.0),
    }
    assert metrics.mean_duration_seconds == pytest.approx(2.0)
    assert metrics.mean_tool_calls == pytest.approx(1.0)
    assert metrics.tool_error_rate == pytest.approx(1 / 3)
    assert metrics.usage == TokenUsage(15, 20, 24, 30, 11, 100)


def test_calculate_metrics_returns_zero_rates_without_trials_or_tool_calls() -> None:
    empty_metrics = calculate_metrics(())
    assert empty_metrics.task_count == 0
    assert empty_metrics.tool_errors == 0
    assert empty_metrics.mean_duration_seconds_by_task == {}
    assert empty_metrics.success_rate == 0.0
    assert empty_metrics.usage is None
    assert empty_metrics.macro_pass_at_k == {}

    metrics = calculate_metrics((_result("one", TrialStatus.task_failed, reward=0, tool_calls=0),))
    assert metrics.tool_error_rate == 0.0
