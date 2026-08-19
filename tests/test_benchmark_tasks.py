from pathlib import Path

import pytest

from le_agent_coding.benchmark.tasks import BenchmarkTaskError, load_builtin_tasks, select_tasks


def _write_task(
    root: Path,
    task_id: str = "one",
    *,
    evaluator: str = "repository_lookup",
) -> None:
    task_dir = root / task_id
    (task_dir / "workspace").mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                f'id = "{task_id}"',
                'title = "示例任务"',
                'prompt = "找到答案"',
                "timeout_seconds = 30",
                f'evaluator = "{evaluator}"',
            ]
        ),
        encoding="utf-8",
    )


def test_load_builtin_tasks_reads_a_valid_manifest(tmp_path: Path) -> None:
    _write_task(tmp_path)

    tasks = load_builtin_tasks(tmp_path)

    assert [task.id for task in tasks] == ["one"]
    assert tasks[0].fixture_path == tmp_path / "one" / "workspace"
    assert tasks[0].timeout_seconds == 30


def test_failing_test_fix_prompt_requires_stdlib_unittest() -> None:
    task = next(task for task in load_builtin_tasks() if task.id == "failing_test_fix")

    assert "python -m unittest discover -s tests -t . -q" in task.prompt
    assert "pytest" not in task.prompt


def test_load_builtin_tasks_rejects_untrusted_evaluator(tmp_path: Path) -> None:
    _write_task(tmp_path, evaluator="package.module:function")

    with pytest.raises(BenchmarkTaskError, match="未知 evaluator"):
        load_builtin_tasks(tmp_path)


def test_select_tasks_preserves_requested_order(tmp_path: Path) -> None:
    for task_id in ("alpha", "beta"):
        _write_task(tmp_path, task_id)
    tasks = load_builtin_tasks(tmp_path)

    assert [task.id for task in select_tasks(tasks, ("beta", "alpha"))] == ["beta", "alpha"]


def test_select_tasks_rejects_unknown_id(tmp_path: Path) -> None:
    _write_task(tmp_path)

    with pytest.raises(BenchmarkTaskError, match="未知 task: missing"):
        select_tasks(load_builtin_tasks(tmp_path), ("missing",))


def test_load_builtin_tasks_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    _write_task(tmp_path)
    (tmp_path / "one" / "task.toml").write_text(
        (tmp_path / "one" / "task.toml")
        .read_text(encoding="utf-8")
        .replace("schema_version = 1", "schema_version = 2"),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkTaskError, match="不支持 schema_version"):
        load_builtin_tasks(tmp_path)


def test_load_builtin_tasks_rejects_duplicate_ids(tmp_path: Path) -> None:
    _write_task(tmp_path, "first")
    _write_task(tmp_path, "second")
    (tmp_path / "second" / "task.toml").write_text(
        (tmp_path / "second" / "task.toml")
        .read_text(encoding="utf-8")
        .replace('id = "second"', 'id = "first"'),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkTaskError, match="重复 task id: first"):
        load_builtin_tasks(tmp_path)


def test_load_builtin_tasks_rejects_empty_prompt(tmp_path: Path) -> None:
    _write_task(tmp_path)
    (tmp_path / "one" / "task.toml").write_text(
        (tmp_path / "one" / "task.toml")
        .read_text(encoding="utf-8")
        .replace('prompt = "找到答案"', 'prompt = ""'),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkTaskError, match="prompt 不能为空"):
        load_builtin_tasks(tmp_path)


@pytest.mark.parametrize("timeout", ("0", "-1", "true", "nan", "inf", "-inf"))
def test_load_builtin_tasks_rejects_non_positive_boolean_or_non_finite_timeout(
    tmp_path: Path, timeout: str
) -> None:
    _write_task(tmp_path)
    (tmp_path / "one" / "task.toml").write_text(
        (tmp_path / "one" / "task.toml")
        .read_text(encoding="utf-8")
        .replace("timeout_seconds = 30", f"timeout_seconds = {timeout}"),
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkTaskError, match="timeout_seconds 必须是正数"):
        load_builtin_tasks(tmp_path)


def test_load_builtin_tasks_requires_workspace_directory(tmp_path: Path) -> None:
    _write_task(tmp_path)
    (tmp_path / "one" / "workspace").rmdir()

    with pytest.raises(BenchmarkTaskError, match="缺少 workspace"):
        load_builtin_tasks(tmp_path)


def test_select_tasks_deduplicates_requested_ids(tmp_path: Path) -> None:
    for task_id in ("alpha", "beta"):
        _write_task(tmp_path, task_id)

    selected = select_tasks(load_builtin_tasks(tmp_path), ("beta", "alpha", "beta"))

    assert [task.id for task in selected] == ["beta", "alpha"]
