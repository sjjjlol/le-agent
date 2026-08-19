"""内置 benchmark task manifest 的加载与选择。"""

import tomllib
from collections.abc import Iterable, Mapping
from importlib.resources import files
from math import isfinite
from pathlib import Path

from le_agent_coding.benchmark.models import BenchmarkTask

KNOWN_EVALUATORS = frozenset({"repository_lookup", "targeted_edit", "failing_test_fix"})


class BenchmarkTaskError(ValueError):
    """表示内置 benchmark task 定义无效。"""


def load_builtin_tasks(root: Path | None = None) -> tuple[BenchmarkTask, ...]:
    """加载并验证内置 benchmark task。"""
    task_root = root or Path(str(files("le_agent_coding").joinpath("data", "benchmark_tasks")))
    if not task_root.is_dir():
        raise BenchmarkTaskError(f"benchmark task 目录不存在: {task_root}")

    tasks: list[BenchmarkTask] = []
    task_ids: set[str] = set()
    for task_dir in task_root.iterdir():
        if not task_dir.is_dir():
            continue
        task = _load_task(task_dir)
        if task.id in task_ids:
            raise BenchmarkTaskError(f"重复 task id: {task.id}")
        task_ids.add(task.id)
        tasks.append(task)

    return tuple(sorted(tasks, key=lambda task: task.id))


def select_tasks(
    tasks: Iterable[BenchmarkTask], task_ids: Iterable[str] | None
) -> tuple[BenchmarkTask, ...]:
    """按用户请求的顺序选择 task，并去除重复 ID。"""
    all_tasks = tuple(tasks)
    if task_ids is None:
        return all_tasks

    tasks_by_id = {task.id: task for task in all_tasks}
    selected: list[BenchmarkTask] = []
    selected_ids: set[str] = set()
    for task_id in task_ids:
        if task_id in selected_ids:
            continue
        try:
            task = tasks_by_id[task_id]
        except KeyError as error:
            raise BenchmarkTaskError(f"未知 task: {task_id}") from error
        selected_ids.add(task_id)
        selected.append(task)
    return tuple(selected)


def _load_task(task_dir: Path) -> BenchmarkTask:
    manifest_path = task_dir / "task.toml"
    if not manifest_path.is_file():
        raise BenchmarkTaskError(f"缺少 task.toml: {task_dir}")

    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise BenchmarkTaskError(f"task.toml 无效: {manifest_path}") from error

    schema_version = _required_int(manifest, "schema_version", task_dir)
    if schema_version != 1:
        raise BenchmarkTaskError(f"不支持 schema_version: {schema_version}")

    task_id = _required_string(manifest, "id", task_dir)
    title = _required_string(manifest, "title", task_dir)
    prompt = _required_string(manifest, "prompt", task_dir)
    if not prompt.strip():
        raise BenchmarkTaskError("prompt 不能为空")

    timeout_seconds = _required_timeout(manifest, task_dir)
    evaluator = _required_string(manifest, "evaluator", task_dir)
    if evaluator not in KNOWN_EVALUATORS:
        raise BenchmarkTaskError(f"未知 evaluator: {evaluator}")

    fixture_path = task_dir / "workspace"
    if not fixture_path.is_dir():
        raise BenchmarkTaskError(f"缺少 workspace: {task_dir}")

    return BenchmarkTask(
        id=task_id,
        title=title,
        prompt=prompt,
        timeout_seconds=timeout_seconds,
        fixture_path=fixture_path,
        evaluator=evaluator,
    )


def _required_string(manifest: Mapping[str, object], field: str, task_dir: Path) -> str:
    value = manifest.get(field)
    if type(value) is not str:
        raise BenchmarkTaskError(f"{field} 必须是字符串: {task_dir}")
    return value


def _required_int(manifest: Mapping[str, object], field: str, task_dir: Path) -> int:
    value = manifest.get(field)
    if type(value) is not int:
        raise BenchmarkTaskError(f"{field} 必须是整数: {task_dir}")
    return value


def _required_timeout(manifest: Mapping[str, object], task_dir: Path) -> float:
    value = manifest.get("timeout_seconds")
    if type(value) is int:
        timeout_seconds: float = value
    elif type(value) is float:
        timeout_seconds = value
    else:
        raise BenchmarkTaskError(f"timeout_seconds 必须是正数: {task_dir}")
    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise BenchmarkTaskError(f"timeout_seconds 必须是正数: {task_dir}")
    return timeout_seconds
