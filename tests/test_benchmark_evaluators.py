import subprocess
import sys
from pathlib import Path
from shutil import copytree
from subprocess import TimeoutExpired

import pytest

from le_agent_coding.benchmark import evaluators
from le_agent_coding.benchmark.evaluators import BenchmarkEvaluationError, evaluate
from le_agent_coding.benchmark.tasks import load_builtin_tasks


def _task(task_id: str):
    return next(task for task in load_builtin_tasks() if task.id == task_id)


def test_repository_lookup_requires_path_symbol_and_relation(tmp_path: Path) -> None:
    task = _task("repository_lookup")
    workspace = copytree(task.fixture_path, tmp_path / "workspace")

    passed = evaluate(
        task,
        workspace,
        "welcome/service.py 中的 build_welcome 调用了 welcome/render.py 中的 format_salutation。",
    )
    failed = evaluate(task, workspace, "答案在 render 模块。")

    assert passed.reward == 1
    assert failed.reward == 0
    assert [check.name for check in passed.checks] == ["path", "symbol", "relation"]


def test_targeted_edit_checks_threshold_and_member_regressions(tmp_path: Path) -> None:
    task = _task("targeted_edit")
    workspace = copytree(task.fixture_path, tmp_path / "workspace")
    (workspace / "pricing.py").write_text(
        "def shipping_fee(total, *, member=False):\n"
        "    return 0 if member or total >= 75 else 10\n",
        encoding="utf-8",
    )

    result = evaluate(task, workspace, "已完成。")

    assert result.reward == 1
    assert all(check.passed for check in result.checks)


def test_targeted_edit_rejects_a_member_regression(tmp_path: Path) -> None:
    task = _task("targeted_edit")
    workspace = copytree(task.fixture_path, tmp_path / "workspace")
    (workspace / "pricing.py").write_text(
        "def shipping_fee(total, *, member=False):\n    return 0 if total >= 75 else 10\n",
        encoding="utf-8",
    )

    result = evaluate(task, workspace, "已完成。")

    assert result.reward == 0
    assert not result.checks[0].passed


def test_targeted_edit_returns_a_failed_check_when_source_is_missing(tmp_path: Path) -> None:
    task = _task("targeted_edit")
    workspace = copytree(task.fixture_path, tmp_path / "workspace")
    (workspace / "pricing.py").unlink()

    result = evaluate(task, workspace, "已完成。")

    assert result.reward == 0
    assert len(result.checks) == 1
    assert not result.checks[0].passed


def test_targeted_edit_returns_a_failed_check_when_source_has_syntax_error(tmp_path: Path) -> None:
    task = _task("targeted_edit")
    workspace = copytree(task.fixture_path, tmp_path / "workspace")
    (workspace / "pricing.py").write_text("def shipping_fee(:\n", encoding="utf-8")

    result = evaluate(task, workspace, "已完成。")

    assert result.reward == 0
    assert len(result.checks) == 1
    assert not result.checks[0].passed


def test_failing_test_fix_rejects_visible_only_workaround(tmp_path: Path) -> None:
    task = _task("failing_test_fix")
    workspace = copytree(task.fixture_path, tmp_path / "workspace")
    (workspace / "tests" / "test_slugify.py").unlink()

    result = evaluate(task, workspace, "已完成。")

    assert result.reward == 0
    assert {check.name for check in result.checks} == {"visible_tests", "hidden_cases"}


@pytest.mark.parametrize("tamper", ["delete", "empty"])
def test_failing_test_fix_requires_the_named_visible_unittest(tmp_path: Path, tamper: str) -> None:
    task = _task("failing_test_fix")
    workspace = copytree(task.fixture_path, tmp_path / "workspace")
    (workspace / "slugify.py").write_text(
        'def slugify(value: str) -> str:\n    return "-".join(value.strip().lower().split())\n',
        encoding="utf-8",
    )
    visible_test = workspace / "tests" / "test_slugify.py"
    if tamper == "delete":
        visible_test.unlink()
    else:
        visible_test.write_text("", encoding="utf-8")

    result = evaluate(task, workspace, "已完成。")

    checks = {check.name: check for check in result.checks}
    assert result.reward == 0
    assert not checks["visible_tests"].passed
    assert checks["hidden_cases"].passed


def test_failing_test_fix_accepts_fixed_fixture_with_named_visible_unittest(
    tmp_path: Path,
) -> None:
    task = _task("failing_test_fix")
    workspace = copytree(task.fixture_path, tmp_path / "workspace")
    (workspace / "slugify.py").write_text(
        'def slugify(value: str) -> str:\n    return "-".join(value.strip().lower().split())\n',
        encoding="utf-8",
    )

    result = evaluate(task, workspace, "已完成。")

    assert result.reward == 1
    assert all(check.passed for check in result.checks)


def test_failing_test_fixture_is_a_real_isolated_unittest() -> None:
    task = _task("failing_test_fix")

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-t",
            ".",
            "-q",
        ],
        cwd=task.fixture_path,
        capture_output=True,
        text=True,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 1
    assert "Ran 1 test" in output
    assert "FAILED" in output
    assert "ModuleNotFoundError" not in output


def test_failing_test_evaluator_runs_exactly_one_named_isolated_unittest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task("failing_test_fix")
    workspace = copytree(task.fixture_path, tmp_path / "workspace")
    commands: list[list[str]] = []

    def record_command(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(evaluators.subprocess, "run", record_command)

    result = evaluate(task, workspace, "已完成。")

    assert result.reward == 1
    assert commands[0][:3] == [sys.executable, "-I", "-c"]
    assert (
        "tests.test_slugify.SlugifyTests.test_slugify_collapses_repeated_spaces" in commands[0][3]
    )
    assert "testsRun != 1" in commands[0][3]
    assert "discover" not in commands[0][3]


def test_evaluate_raises_for_a_subprocess_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task("targeted_edit")
    workspace = copytree(task.fixture_path, tmp_path / "workspace")

    def _timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutExpired(cmd="python", timeout=10)

    monkeypatch.setattr(evaluators.subprocess, "run", _timeout)

    with pytest.raises(BenchmarkEvaluationError, match="超时"):
        evaluate(task, workspace, "已完成。")
