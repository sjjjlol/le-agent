"""内置 benchmark task 的可信确定性评分器。"""

import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from textwrap import dedent

from le_agent_coding.benchmark.models import BenchmarkTask, EvaluationCheck, EvaluationResult

type Evaluator = Callable[[Path, str], EvaluationResult]

EVALUATOR_TIMEOUT_SECONDS = 10.0
_MAX_DETAIL_CHARACTERS = 2_000
_VISIBLE_SLUGIFY_TEST = dedent(
    """\
    import sys
    import unittest

    sys.path.insert(0, ".")
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "tests.test_slugify.SlugifyTests.test_slugify_collapses_repeated_spaces"
    )
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    if not result.wasSuccessful() or result.testsRun != 1:
        raise SystemExit(1)
    """
)


class BenchmarkEvaluationError(RuntimeError):
    """表示 evaluator 自身无法完成评分。"""


def evaluate(task: BenchmarkTask, workspace: Path, final_text: str) -> EvaluationResult:
    """使用 task 声明的内置评分器评估一次 trial。"""
    evaluator = EVALUATORS[task.evaluator]
    return evaluator(workspace, final_text)


def _repository_lookup(_workspace: Path, final_text: str) -> EvaluationResult:
    checks = (
        EvaluationCheck(
            name="path",
            passed="welcome/render.py" in final_text,
            detail="最终回答必须包含 welcome/render.py。",
        ),
        EvaluationCheck(
            name="symbol",
            passed="format_salutation" in final_text,
            detail="最终回答必须包含 format_salutation。",
        ),
        EvaluationCheck(
            name="relation",
            passed="build_welcome" in final_text
            and ("调用" in final_text or "calls" in final_text.lower()),
            detail="最终回答必须说明 build_welcome 调用了目标函数。",
        ),
    )
    return _result(checks)


def _targeted_edit(workspace: Path, _final_text: str) -> EvaluationResult:
    source = dedent(
        """\
        import runpy

        shipping_fee = runpy.run_path("pricing.py")["shipping_fee"]
        assert shipping_fee(74.99) == 10
        assert shipping_fee(75) == 0
        assert shipping_fee(10, member=True) == 0
        assert shipping_fee(10) == 10
        """
    )
    return _result((_python_check(workspace, "behavior", source),))


def _failing_test_fix(workspace: Path, _final_text: str) -> EvaluationResult:
    visible_tests = _command_check(
        workspace,
        "visible_tests",
        [
            sys.executable,
            "-I",
            "-c",
            _VISIBLE_SLUGIFY_TEST,
        ],
    )
    hidden_cases = _python_check(
        workspace,
        "hidden_cases",
        dedent(
            """\
            import runpy

            slugify = runpy.run_path("slugify.py")["slugify"]
            assert slugify("Le Agent   Agent") == "le-agent-agent"
            assert slugify("Le Agent\\tAgent") == "le-agent-agent"
            assert slugify("Le Agent\\nAgent") == "le-agent-agent"
            assert slugify("  Le Agent Agent  ") == "le-agent-agent"
            assert slugify("Le Agent") == "le-agent"
            """
        ),
    )
    return _result((visible_tests, hidden_cases))


def _python_check(workspace: Path, name: str, source: str) -> EvaluationCheck:
    """在隔离 Python 子进程中执行隐藏断言。"""
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", source],
            cwd=workspace,
            timeout=EVALUATOR_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise BenchmarkEvaluationError(f"evaluator 子进程超时: {name}") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise BenchmarkEvaluationError(f"evaluator 子进程无法运行: {name}") from error
    return _completed_check(name, completed)


def _command_check(workspace: Path, name: str, command: list[str]) -> EvaluationCheck:
    """执行需要完整测试运行环境的可见测试命令。"""
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            timeout=EVALUATOR_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise BenchmarkEvaluationError(f"evaluator 子进程超时: {name}") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise BenchmarkEvaluationError(f"evaluator 子进程无法运行: {name}") from error
    return _completed_check(name, completed)


def _completed_check(name: str, completed: subprocess.CompletedProcess[str]) -> EvaluationCheck:
    return EvaluationCheck(
        name=name,
        passed=completed.returncode == 0,
        detail=_detail(completed.stdout, completed.stderr),
    )


def _detail(stdout: str, stderr: str) -> str:
    output = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    if not output:
        return "通过"
    return output[-_MAX_DETAIL_CHARACTERS:]


def _result(checks: tuple[EvaluationCheck, ...]) -> EvaluationResult:
    return EvaluationResult(
        reward=int(all(check.passed for check in checks)),
        checks=checks,
    )


EVALUATORS: Mapping[str, Evaluator] = {
    "repository_lookup": _repository_lookup,
    "targeted_edit": _targeted_edit,
    "failing_test_fix": _failing_test_fix,
}
