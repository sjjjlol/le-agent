from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from tempfile import gettempdir
from types import SimpleNamespace

import anyio
import pytest
from typer.testing import CliRunner

from le_agent_coding import cli
from le_agent_coding.benchmark.metrics import calculate_metrics
from le_agent_coding.benchmark.models import BenchmarkRun, TrialResult, TrialStatus
from le_agent_coding.cli import app


def _sample_run(
    *,
    provider: str = "fake",
    status: TrialStatus = TrialStatus.passed,
) -> BenchmarkRun:
    result = TrialResult(
        task_id="repository_lookup",
        trial=1,
        status=status,
        reward=1 if status is TrialStatus.passed else 0,
        checks=(),
        duration_seconds=0.1,
        tool_calls=1,
        tool_errors=0,
        usage=None,
        final_text="done",
        trajectory=(),
    )
    return BenchmarkRun(
        schema_version=1,
        run_id="run-1",
        started_at="2026-08-08T00:00:00Z",
        finished_at="2026-08-08T00:00:01Z",
        provider=provider,
        model="benchmark-fake" if provider == "fake" else "real-model",
        seed=300,
        task_ids=("repository_lookup",),
        requested_trials=1,
        git_commit=None,
        results=(result,),
        metrics=calculate_metrics((result,)),
    )


def test_parse_benchmark_cli_args_supports_repeated_tasks_and_equals_forms() -> None:
    options = cli._parse_benchmark_cli_args(
        [
            "--task",
            "repository_lookup",
            "--task=failing_test_fix",
            "--trials=3",
            "--seed",
            "7",
            "--keep-workspaces",
        ],
        provider="fake",
        model=None,
        output=Path("run.json"),
    )

    assert options.provider == "fake"
    assert options.task_ids == ("repository_lookup", "failing_test_fix")
    assert options.trials == 3
    assert options.seed == 7
    assert options.output == Path("run.json")
    assert options.keep_workspaces is True


def test_parse_benchmark_cli_args_defaults_to_safe_fake_mode() -> None:
    options = cli._parse_benchmark_cli_args([], provider=None, model="ignored", output=None)

    assert options.provider == "fake"
    assert options.model == "ignored"
    assert options.task_ids == ()
    assert options.trials == 1
    assert options.seed == 300
    assert options.output is None
    assert options.keep_workspaces is False


@pytest.mark.parametrize("argument", ["--wat", "task-without-option"])
def test_parse_benchmark_cli_args_rejects_unknown_arguments(argument: str) -> None:
    with pytest.raises(RuntimeError, match="Unknown benchmark argument"):
        cli._parse_benchmark_cli_args([argument], provider="fake", model=None, output=None)


@pytest.mark.parametrize("option", ["--task", "--trials", "--seed"])
def test_parse_benchmark_cli_args_rejects_missing_values(option: str) -> None:
    with pytest.raises(RuntimeError, match=f"{option} requires a value"):
        cli._parse_benchmark_cli_args([option], provider="fake", model=None, output=None)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--trials", "0"], "--trials must be at least 1"),
        (["--trials", "many"], "--trials must be an integer"),
        (["--seed", "random"], "--seed must be an integer"),
    ],
)
def test_parse_benchmark_cli_args_rejects_invalid_numbers(args: list[str], message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        cli._parse_benchmark_cli_args(args, provider="fake", model=None, output=None)


def test_benchmark_cli_routes_without_starting_tui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[cli.BenchmarkCliOptions] = []

    async def fake_run(options: cli.BenchmarkCliOptions) -> tuple[BenchmarkRun, Path]:
        calls.append(options)
        return _sample_run(provider="fake"), tmp_path / "run.json"

    def unexpected_tui(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("benchmark must not start the TUI")

    monkeypatch.setattr(cli, "run_benchmark_command", fake_run)
    monkeypatch.setattr(cli, "run_openai_tui", unexpected_tui)

    result = CliRunner().invoke(
        app,
        [
            "benchmark",
            "--provider",
            "fake",
            "--task",
            "repository_lookup",
            "--output",
            str(tmp_path / "run.json"),
        ],
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0].provider == "fake"
    assert calls[0].task_ids == ("repository_lookup",)
    assert calls[0].output == tmp_path / "run.json"


@pytest.mark.parametrize(
    "args",
    [
        ["--print", "benchmark"],
        ["--mode", "text", "benchmark"],
    ],
)
def test_benchmark_word_remains_a_prompt_when_print_mode_was_requested_first(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    prompts: list[str] = []

    async def fake_print(prompt: str, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        prompts.append(prompt)
        return True

    async def unexpected_benchmark(
        options: cli.BenchmarkCliOptions,
    ) -> tuple[BenchmarkRun, Path]:
        del options
        raise AssertionError("显式 print mode 不得路由到 benchmark")

    monkeypatch.setattr(cli, "run_openai_print_mode", fake_print)
    monkeypatch.setattr(cli, "run_benchmark_command", unexpected_benchmark)
    monkeypatch.setattr(cli, "_startup_update_notice", lambda: None)

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.output
    assert prompts == ["benchmark"]


@pytest.mark.parametrize(
    "args",
    [
        ["benchmark", "--print"],
        ["benchmark", "--mode", "text"],
        ["benchmark", "--export"],
    ],
)
def test_benchmark_rejects_print_mode_or_export_options_after_command(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    async def unexpected_benchmark(
        options: cli.BenchmarkCliOptions,
    ) -> tuple[BenchmarkRun, Path]:
        del options
        raise AssertionError("冲突参数不得启动 benchmark")

    async def unexpected_print(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        raise AssertionError("冲突参数不得启动普通 prompt")

    monkeypatch.setattr(cli, "run_benchmark_command", unexpected_benchmark)
    monkeypatch.setattr(cli, "run_openai_print_mode", unexpected_print)

    result = CliRunner().invoke(app, args)

    assert result.exit_code != 0
    assert "benchmark cannot be combined with --print/--mode/--export" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["-mgpt-5", "benchmark"],
        ["benchmark", "-mgpt-5"],
    ],
)
def test_benchmark_cli_supports_attached_model_short_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str]
) -> None:
    calls: list[cli.BenchmarkCliOptions] = []

    async def fake_run(options: cli.BenchmarkCliOptions) -> tuple[BenchmarkRun, Path]:
        calls.append(options)
        return _sample_run(), tmp_path / "run.json"

    monkeypatch.setattr(cli, "run_benchmark_command", fake_run)

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0
    assert calls[0].model == "gpt-5"


@pytest.mark.parametrize(
    "args",
    [
        ["-orun.json", "benchmark"],
        ["benchmark", "-orun.json"],
    ],
)
def test_benchmark_cli_supports_attached_output_short_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str]
) -> None:
    calls: list[cli.BenchmarkCliOptions] = []

    async def fake_run(options: cli.BenchmarkCliOptions) -> tuple[BenchmarkRun, Path]:
        calls.append(options)
        return _sample_run(), tmp_path / "run.json"

    monkeypatch.setattr(cli, "run_benchmark_command", fake_run)

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0
    assert calls[0].output == Path("run.json")


def test_benchmark_cli_does_not_treat_unknown_long_option_as_attached_short_value() -> None:
    result = CliRunner().invoke(app, ["benchmark", "--modelish=value"])

    assert result.exit_code != 0
    assert "Unknown benchmark argument: --modelish=value" in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--resume", "old", "benchmark"], "--resume was renamed to --session"),
        (["--prompt", "hello", "benchmark"], "--prompt was removed"),
        (["-x", "old.py", "benchmark"], "-x was renamed to -e/--extension"),
        (
            ["--session", "a", "--new-session", "benchmark"],
            "--session and --new-session cannot be used together",
        ),
    ],
)
def test_benchmark_cli_preserves_existing_compatibility_validation(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    message: str,
) -> None:
    async def unexpected_run(options: cli.BenchmarkCliOptions) -> tuple[BenchmarkRun, Path]:
        del options
        raise AssertionError("invalid shared options must be rejected before benchmark routing")

    monkeypatch.setattr(cli, "run_benchmark_command", unexpected_run)

    result = CliRunner().invoke(app, args)

    assert result.exit_code != 0
    assert message in result.output


def test_benchmark_cli_rejects_unknown_task_before_running_trials(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["benchmark", "--task", "missing", "--output", str(tmp_path / "run.json")],
    )

    assert result.exit_code != 0
    assert "未知 task: missing" in result.output


def test_benchmark_cli_artifact_reports_selected_task_and_aggregate_counts(tmp_path: Path) -> None:
    output = tmp_path / "run.json"

    result = CliRunner().invoke(
        app,
        [
            "benchmark",
            "--provider",
            "fake",
            "--task",
            "targeted_edit",
            "--task",
            "repository_lookup",
            "--trials",
            "1",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["task_ids"] == ["targeted_edit", "repository_lookup"]
    assert payload["requested_trials"] == 1
    assert payload["metrics"]["task_count"] == 2
    assert payload["metrics"]["trials"] == 2
    assert payload["metrics"]["tool_errors"] == 0
    assert set(payload["metrics"]["mean_duration_seconds_by_task"]) == {
        "targeted_edit",
        "repository_lookup",
    }


def test_benchmark_cli_returns_zero_for_normal_task_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run(options: cli.BenchmarkCliOptions) -> tuple[BenchmarkRun, Path]:
        del options
        return _sample_run(status=TrialStatus.task_failed), tmp_path / "run.json"

    monkeypatch.setattr(cli, "run_benchmark_command", fake_run)

    result = CliRunner().invoke(app, ["benchmark"])

    assert result.exit_code == 0


@pytest.mark.parametrize(
    "status",
    [TrialStatus.agent_failure, TrialStatus.timeout, TrialStatus.evaluation_failure],
)
def test_benchmark_cli_returns_one_for_infrastructure_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: TrialStatus,
) -> None:
    async def fake_run(options: cli.BenchmarkCliOptions) -> tuple[BenchmarkRun, Path]:
        del options
        return _sample_run(status=status), tmp_path / "run.json"

    monkeypatch.setattr(cli, "run_benchmark_command", fake_run)

    result = CliRunner().invoke(app, ["benchmark"])

    assert result.exit_code == 1


def test_benchmark_cli_returns_one_for_provider_close_failure_after_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "run.json"
    close_error = RuntimeError("provider close exploded")
    close_error.add_note(f"benchmark artifact 已写入: {output}")
    close_error.add_note(f"benchmark workspace 已保留: {tmp_path / 'workspace'}")

    def fail_after_artifact(function: object, options: object) -> object:
        del function, options
        raise close_error

    monkeypatch.setattr(cli.anyio, "run", fail_after_artifact)

    result = CliRunner().invoke(
        app,
        ["benchmark", "--provider", "configured", "--output", str(output)],
    )

    assert result.exit_code == 1
    assert "provider close exploded" in result.output
    assert f"benchmark artifact 已写入: {output}" in result.output
    assert f"benchmark workspace 已保留: {tmp_path / 'workspace'}" in result.output


def test_legacy_output_flag_still_errors_with_migration_hint() -> None:
    result = CliRunner().invoke(app, ["--output", "text", "--print", "hello"])

    assert result.exit_code != 0
    assert "--output was renamed to --mode" in result.output


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_output_kind", ["directory", "parent-file"])
async def test_run_benchmark_command_rejects_invalid_output_before_model_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_output_kind: str,
) -> None:
    calls: list[str] = []
    if invalid_output_kind == "directory":
        output = tmp_path
    else:
        parent = tmp_path / "parent-file"
        parent.write_text("not a directory", encoding="utf-8")
        output = parent / "run.json"

    class ClosableProvider:
        async def aclose(self) -> None:
            calls.append("close")

    def create_provider(*args: object, **kwargs: object) -> ClosableProvider:
        del args, kwargs
        calls.append("provider")
        return ClosableProvider()

    async def fake_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del config, tasks
        calls.append("runner")
        return _sample_run(provider="configured")

    selection = SimpleNamespace(provider=SimpleNamespace(name="configured"), model="model")
    monkeypatch.setattr(cli, "load_provider_settings", lambda: object())
    monkeypatch.setattr(
        cli, "load_shell_settings", lambda: SimpleNamespace(shell_command_prefix=None)
    )
    monkeypatch.setattr(cli, "resolve_provider_selection", lambda *args, **kwargs: selection)
    monkeypatch.setattr(cli, "resolve_startup_thinking_level", lambda *args: None)
    monkeypatch.setattr(cli, "create_model_provider", create_provider)
    monkeypatch.setattr(cli, "run_benchmark", fake_benchmark)

    with pytest.raises(OSError):
        await cli.run_benchmark_command(
            cli.BenchmarkCliOptions(
                provider="configured",
                model=None,
                task_ids=("repository_lookup",),
                trials=1,
                seed=300,
                output=output,
                keep_workspaces=False,
            )
        )

    assert calls == []


@pytest.mark.anyio
async def test_run_benchmark_command_rejects_unavailable_default_output_before_model_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    (tmp_path / "benchmark-results").write_text("not a directory", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "create_model_provider",
        lambda *args, **kwargs: calls.append("provider"),
    )

    async def fake_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del config, tasks
        calls.append("runner")
        return _sample_run()

    monkeypatch.setattr(cli, "run_benchmark", fake_benchmark)

    with pytest.raises(OSError):
        await cli.run_benchmark_command(
            cli.BenchmarkCliOptions(
                provider="fake",
                model=None,
                task_ids=("repository_lookup",),
                trials=1,
                seed=300,
                output=None,
                keep_workspaces=False,
            )
        )

    assert calls == []


@pytest.mark.anyio
async def test_keep_workspace_archive_parent_is_preflighted_before_provider_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "results" / "run.json"
    output.parent.mkdir()
    (output.parent / "workspaces").write_text("occupied", encoding="utf-8")
    provider_calls = 0

    def unexpected_provider_settings() -> object:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("archive 预检失败时不得读取 provider 配置")

    monkeypatch.setattr(cli, "load_provider_settings", unexpected_provider_settings)

    with pytest.raises(NotADirectoryError, match="workspace archive"):
        await cli.run_benchmark_command(
            cli.BenchmarkCliOptions(
                provider="configured",
                model=None,
                task_ids=("repository_lookup",),
                trials=1,
                seed=300,
                output=output,
                keep_workspaces=True,
            )
        )

    assert provider_calls == 0


@pytest.mark.anyio
async def test_keep_workspace_archive_probe_failure_precedes_provider_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "results" / "run.json"
    archive_parent = output.parent / "workspaces"
    provider_calls = 0
    original_mkdtemp = cli.mkdtemp

    def deny_archive_probe(*, prefix: str, dir: str | Path | None = None) -> str:
        if dir is not None and Path(dir) == archive_parent:
            raise PermissionError("archive probe denied")
        return original_mkdtemp(prefix=prefix, dir=dir)

    def unexpected_provider_settings() -> object:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("archive probe 失败时不得读取 provider 配置")

    monkeypatch.setattr(cli, "mkdtemp", deny_archive_probe)
    monkeypatch.setattr(cli, "load_provider_settings", unexpected_provider_settings)

    with pytest.raises(PermissionError, match="archive probe denied"):
        await cli.run_benchmark_command(
            cli.BenchmarkCliOptions(
                provider="configured",
                model=None,
                task_ids=("repository_lookup",),
                trials=1,
                seed=300,
                output=output,
                keep_workspaces=True,
            )
        )

    assert provider_calls == 0


@pytest.mark.anyio
async def test_keep_workspace_archive_preflight_cleans_its_temporary_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "results" / "run.json"
    archive_parent = output.parent / "workspaces"
    archive_probes: list[Path] = []
    original_mkdtemp = cli.mkdtemp

    def record_mkdtemp(*, prefix: str, dir: str | Path | None = None) -> str:
        created = Path(original_mkdtemp(prefix=prefix, dir=dir))
        if dir is not None and Path(dir) == archive_parent:
            archive_probes.append(created)
            assert created.parent == archive_parent
            assert created.is_dir()
        return str(created)

    async def fake_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del config, tasks
        assert len(archive_probes) == 1
        assert not archive_probes[0].exists()
        assert list(archive_parent.iterdir()) == []
        return _sample_run()

    monkeypatch.setattr(cli, "mkdtemp", record_mkdtemp)
    monkeypatch.setattr(cli, "run_benchmark", fake_benchmark)
    monkeypatch.setattr(cli, "write_benchmark_run", lambda run, path: path)
    monkeypatch.setattr(cli, "render_benchmark_summary", lambda run, artifact_path: None)

    await cli.run_benchmark_command(
        cli.BenchmarkCliOptions(
            provider="fake",
            model=None,
            task_ids=("repository_lookup",),
            trials=1,
            seed=300,
            output=output,
            keep_workspaces=True,
        )
    )

    assert len(archive_probes) == 1
    assert not archive_probes[0].exists()


@pytest.mark.anyio
async def test_run_benchmark_command_cleans_output_preflight_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "new-results" / "run.json"

    async def fake_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del config, tasks
        return _sample_run()

    monkeypatch.setattr(cli, "run_benchmark", fake_benchmark)
    monkeypatch.setattr(cli, "write_benchmark_run", lambda run, path: path)
    monkeypatch.setattr(cli, "render_benchmark_summary", lambda run, artifact_path: None)

    await cli.run_benchmark_command(
        cli.BenchmarkCliOptions(
            provider="fake",
            model=None,
            task_ids=("repository_lookup",),
            trials=1,
            seed=300,
            output=output,
            keep_workspaces=False,
        )
    )

    assert output.parent.is_dir()
    assert list(output.parent.iterdir()) == []


@pytest.mark.anyio
async def test_run_benchmark_command_configures_fake_trials_and_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[object] = []
    sample = _sample_run()

    async def fake_benchmark(config: object, tasks: object) -> BenchmarkRun:
        task_list = tuple(tasks)  # type: ignore[arg-type]
        events.append((config, task_list))
        providers = [config.provider_factory(task_list[0], trial) for trial in (1, 2)]  # type: ignore[attr-defined]
        assert providers[0] is not providers[1]
        return replace(sample, requested_trials=2)

    def fake_write(run: BenchmarkRun, path: Path) -> Path:
        events.append(("write", run, path))
        return path

    def fake_render(run: BenchmarkRun, artifact_path: Path) -> None:
        events.append(("render", run, artifact_path))

    monkeypatch.setattr(cli, "run_benchmark", fake_benchmark)
    monkeypatch.setattr(cli, "write_benchmark_run", fake_write)
    monkeypatch.setattr(cli, "render_benchmark_summary", fake_render)

    options = cli.BenchmarkCliOptions(
        provider="fake",
        model="unused-model",
        task_ids=("repository_lookup",),
        trials=2,
        seed=7,
        output=tmp_path / "run.json",
        keep_workspaces=True,
    )
    run, path = await cli.run_benchmark_command(options)

    config, tasks = events[0]  # type: ignore[misc]
    assert config.provider_name == "fake"
    assert config.model == "benchmark-fake"
    assert config.trials == 2
    assert config.seed == 7
    assert config.keep_workspaces is True
    workspace_root = config.workspace_root
    assert workspace_root.parent.resolve() == Path(gettempdir()).resolve()
    assert not workspace_root.is_relative_to(tmp_path)
    assert not workspace_root.exists()
    assert [task.id for task in tasks] == ["repository_lookup"]
    assert run.requested_trials == 2
    assert path == tmp_path / "run.json"
    assert [event[0] for event in events[1:]] == ["write", "render"]  # type: ignore[index]


@pytest.mark.anyio
async def test_keep_workspaces_moves_frozen_result_paths_before_artifact_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "run.json"
    written_runs: list[BenchmarkRun] = []

    async def fake_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del tasks
        workspace = config.workspace_root / "trial-workspace"  # type: ignore[attr-defined]
        workspace.mkdir(parents=True)
        (workspace / "diagnostic.txt").write_text("kept", encoding="utf-8")
        sample = _sample_run()
        result = replace(sample.results[0], workspace=str(workspace))
        return replace(sample, results=(result,))

    def fail_artifact_write(run: BenchmarkRun, path: Path) -> Path:
        del path
        written_runs.append(run)
        raise OSError("artifact write failed")

    monkeypatch.setattr(cli, "run_benchmark", fake_benchmark)
    monkeypatch.setattr(cli, "write_benchmark_run", fail_artifact_write)

    with pytest.raises(OSError, match="artifact write failed"):
        await cli.run_benchmark_command(
            cli.BenchmarkCliOptions(
                provider="fake",
                model=None,
                task_ids=("repository_lookup",),
                trials=1,
                seed=300,
                output=output,
                keep_workspaces=True,
            )
        )

    archived = Path(written_runs[0].results[0].workspace or "")
    assert archived == tmp_path / "workspaces" / "run-1" / "trial-workspace"
    assert (archived / "diagnostic.txt").read_text(encoding="utf-8") == "kept"


@pytest.mark.anyio
async def test_keep_workspaces_never_overwrites_an_existing_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "run.json"
    occupied = tmp_path / "workspaces" / "run-1" / "trial-workspace"
    occupied.mkdir(parents=True)
    marker = occupied / "owner.txt"
    marker.write_text("user data", encoding="utf-8")

    async def fake_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del tasks
        workspace = config.workspace_root / "trial-workspace"  # type: ignore[attr-defined]
        workspace.mkdir(parents=True)
        sample = _sample_run()
        result = replace(sample.results[0], workspace=str(workspace))
        return replace(sample, results=(result,))

    monkeypatch.setattr(cli, "run_benchmark", fake_benchmark)

    with pytest.raises(FileExistsError):
        await cli.run_benchmark_command(
            cli.BenchmarkCliOptions(
                provider="fake",
                model=None,
                task_ids=("repository_lookup",),
                trials=1,
                seed=300,
                output=output,
                keep_workspaces=True,
            )
        )

    assert marker.read_text(encoding="utf-8") == "user data"


@pytest.mark.anyio
async def test_relative_output_still_records_an_absolute_kept_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    written_runs: list[BenchmarkRun] = []

    async def fake_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del tasks
        workspace = config.workspace_root / "trial-workspace"  # type: ignore[attr-defined]
        workspace.mkdir(parents=True)
        sample = _sample_run()
        return replace(sample, results=(replace(sample.results[0], workspace=str(workspace)),))

    def record_write(run: BenchmarkRun, path: Path) -> Path:
        written_runs.append(run)
        return path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_benchmark", fake_benchmark)
    monkeypatch.setattr(cli, "write_benchmark_run", record_write)
    monkeypatch.setattr(cli, "render_benchmark_summary", lambda run, artifact_path: None)

    await cli.run_benchmark_command(
        cli.BenchmarkCliOptions(
            provider="fake",
            model=None,
            task_ids=("repository_lookup",),
            trials=1,
            seed=300,
            output=Path("run.json"),
            keep_workspaces=True,
        )
    )

    kept_workspace = Path(written_runs[0].results[0].workspace or "")
    assert kept_workspace.is_absolute()
    assert kept_workspace == tmp_path / "workspaces" / "run-1" / "trial-workspace"
    assert kept_workspace.is_dir()


@pytest.mark.anyio
async def test_temporary_root_cleanup_failure_does_not_replace_runner_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    active_roots: list[Path] = []
    original_rmtree = cli.shutil.rmtree

    async def fail_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del tasks
        active_roots.append(config.workspace_root)  # type: ignore[attr-defined]
        raise RuntimeError("runner exploded")

    def fail_active_root_cleanup(path: Path | str, *args: object, **kwargs: object) -> None:
        if active_roots and Path(path) == active_roots[0]:
            raise OSError("temporary root cleanup exploded")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(cli, "run_benchmark", fail_benchmark)
    monkeypatch.setattr(cli.shutil, "rmtree", fail_active_root_cleanup)

    try:
        with pytest.raises(RuntimeError, match="runner exploded") as caught:
            await cli.run_benchmark_command(
                cli.BenchmarkCliOptions(
                    provider="fake",
                    model=None,
                    task_ids=("repository_lookup",),
                    trials=1,
                    seed=300,
                    output=tmp_path / "run.json",
                    keep_workspaces=False,
                )
            )
        assert any(
            "清理 benchmark 临时根失败: OSError: temporary root cleanup exploded" in note
            for note in getattr(caught.value, "__notes__", ())
        )
    finally:
        if active_roots and active_roots[0].exists():
            original_rmtree(active_roots[0])


def test_default_fake_cli_isolated_from_parent_pytest_and_git_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    caller_repo = tmp_path / "caller-repo"
    (caller_repo / ".git").mkdir(parents=True)
    parent_tests = caller_repo / "parent-tests"
    parent_tests.mkdir()
    (caller_repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["parent-tests"]\n',
        encoding="utf-8",
    )
    (parent_tests / "test_parent.py").write_text(
        "def test_parent_must_not_run():\n    assert False\n",
        encoding="utf-8",
    )
    active_roots: list[Path] = []
    original_run_benchmark = cli.run_benchmark

    async def record_active_root(config: object, tasks: object) -> BenchmarkRun:
        active_roots.append(config.workspace_root.resolve())  # type: ignore[attr-defined]
        return await original_run_benchmark(config, tasks)  # type: ignore[arg-type]

    monkeypatch.chdir(caller_repo)
    monkeypatch.setattr(cli, "run_benchmark", record_active_root)

    result = CliRunner().invoke(app, ["benchmark", "--provider", "fake"])

    assert result.exit_code == 0, result.output
    assert len(active_roots) == 1
    assert active_roots[0].parent == Path(gettempdir()).resolve()
    assert not active_roots[0].is_relative_to(caller_repo)
    artifacts = list((caller_repo / "benchmark-results").glob("*.json"))
    assert len(artifacts) == 1
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert [trial["status"] for trial in payload["results"]] == ["passed"] * 3
    failing_trial = next(
        trial for trial in payload["results"] if trial["task_id"] == "failing_test_fix"
    )
    bash_commands = [
        step["arguments"]["command"]
        for step in failing_trial["trajectory"]
        if step["kind"] == "tool_execution_start" and step["tool_name"] == "bash"
    ]
    assert bash_commands == [
        "python -m unittest discover -s tests -t . -q",
        "python -m unittest discover -s tests -t . -q",
    ]


@pytest.mark.anyio
async def test_run_benchmark_command_reuses_and_closes_real_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    closed = False
    provider_config = SimpleNamespace(name="configured")
    selection = SimpleNamespace(provider=provider_config, model="resolved-model")
    shell_settings = SimpleNamespace(shell_command_prefix="source aliases.sh")
    captured_config: object | None = None

    class ClosableProvider:
        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    runtime_provider = ClosableProvider()

    monkeypatch.setattr(cli, "load_provider_settings", lambda: object())
    monkeypatch.setattr(cli, "load_shell_settings", lambda: shell_settings)
    monkeypatch.setattr(cli, "resolve_provider_selection", lambda *args, **kwargs: selection)
    monkeypatch.setattr(cli, "resolve_startup_thinking_level", lambda *args: None)
    monkeypatch.setattr(cli, "create_model_provider", lambda *args, **kwargs: runtime_provider)

    async def fake_benchmark(config: object, tasks: object) -> BenchmarkRun:
        nonlocal captured_config
        captured_config = config
        task = tuple(tasks)[0]  # type: ignore[arg-type]
        assert config.provider_factory(task, 1) is runtime_provider  # type: ignore[attr-defined]
        assert config.provider_factory(task, 2) is runtime_provider  # type: ignore[attr-defined]
        return _sample_run(provider="configured")

    monkeypatch.setattr(cli, "run_benchmark", fake_benchmark)
    monkeypatch.setattr(cli, "write_benchmark_run", lambda run, path: path)
    monkeypatch.setattr(cli, "render_benchmark_summary", lambda run, artifact_path: None)

    await cli.run_benchmark_command(
        cli.BenchmarkCliOptions(
            provider="configured",
            model=None,
            task_ids=("repository_lookup",),
            trials=1,
            seed=300,
            output=tmp_path / "run.json",
            keep_workspaces=False,
        )
    )

    assert captured_config is not None
    assert captured_config.provider_name == "configured"  # type: ignore[attr-defined]
    assert captured_config.model == "resolved-model"  # type: ignore[attr-defined]
    assert captured_config.shell_command_prefix == "source aliases.sh"  # type: ignore[attr-defined]
    assert closed is True


@pytest.mark.anyio
async def test_run_benchmark_command_closes_real_provider_when_runner_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    closed = False
    selection = SimpleNamespace(provider=SimpleNamespace(name="configured"), model="model")

    class ClosableProvider:
        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(cli, "load_provider_settings", lambda: object())
    monkeypatch.setattr(
        cli,
        "load_shell_settings",
        lambda: SimpleNamespace(shell_command_prefix=None),
    )
    monkeypatch.setattr(cli, "resolve_provider_selection", lambda *args, **kwargs: selection)
    monkeypatch.setattr(cli, "resolve_startup_thinking_level", lambda *args: None)
    monkeypatch.setattr(cli, "create_model_provider", lambda *args, **kwargs: ClosableProvider())

    async def fail_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del config, tasks
        raise RuntimeError("runner failed")

    monkeypatch.setattr(cli, "run_benchmark", fail_benchmark)

    with pytest.raises(RuntimeError, match="runner failed"):
        await cli.run_benchmark_command(
            cli.BenchmarkCliOptions(
                provider="configured",
                model=None,
                task_ids=("repository_lookup",),
                trials=1,
                seed=300,
                output=tmp_path / "run.json",
                keep_workspaces=False,
            )
        )

    assert closed is True


@pytest.mark.anyio
async def test_real_provider_close_failure_preserves_artifact_and_kept_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "results" / "run.json"
    close_error = RuntimeError("provider close exploded")
    selection = SimpleNamespace(provider=SimpleNamespace(name="configured"), model="model")
    rendered: list[Path] = []

    class FailingCloseProvider:
        async def aclose(self) -> None:
            raise close_error

    async def successful_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del tasks
        workspace = config.workspace_root / "trial-workspace"  # type: ignore[attr-defined]
        workspace.mkdir(parents=True)
        (workspace / "result.txt").write_text("paid result", encoding="utf-8")
        sample = _sample_run(provider="configured")
        result = replace(sample.results[0], workspace=str(workspace))
        return replace(sample, results=(result,))

    monkeypatch.setattr(cli, "load_provider_settings", lambda: object())
    monkeypatch.setattr(
        cli,
        "load_shell_settings",
        lambda: SimpleNamespace(shell_command_prefix=None),
    )
    monkeypatch.setattr(cli, "resolve_provider_selection", lambda *args, **kwargs: selection)
    monkeypatch.setattr(cli, "resolve_startup_thinking_level", lambda *args: None)
    monkeypatch.setattr(
        cli,
        "create_model_provider",
        lambda *args, **kwargs: FailingCloseProvider(),
    )
    monkeypatch.setattr(cli, "run_benchmark", successful_benchmark)
    monkeypatch.setattr(
        cli,
        "render_benchmark_summary",
        lambda run, artifact_path: rendered.append(artifact_path),
    )

    with pytest.raises(RuntimeError, match="provider close exploded") as caught:
        await cli.run_benchmark_command(
            cli.BenchmarkCliOptions(
                provider="configured",
                model=None,
                task_ids=("repository_lookup",),
                trials=1,
                seed=300,
                output=output,
                keep_workspaces=True,
            )
        )

    assert caught.value is close_error
    artifact = json.loads(output.read_text(encoding="utf-8"))
    kept_workspace = Path(artifact["results"][0]["workspace"])
    assert output.is_file()
    assert kept_workspace.is_dir()
    assert (kept_workspace / "result.txt").read_text(encoding="utf-8") == "paid result"
    assert rendered == [output]
    notes = getattr(caught.value, "__notes__", ())
    assert any(f"benchmark artifact 已写入: {output.resolve()}" in note for note in notes)
    assert any(f"benchmark workspace 已保留: {kept_workspace}" in note for note in notes)


@pytest.mark.anyio
@pytest.mark.parametrize("failure_stage", ["archive", "write"])
async def test_archive_or_write_failure_stays_primary_over_provider_close_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_stage: str,
) -> None:
    output = tmp_path / "results" / "run.json"
    close_error = RuntimeError("provider close exploded")
    primary_error = OSError(f"{failure_stage} exploded")
    selection = SimpleNamespace(provider=SimpleNamespace(name="configured"), model="model")

    class FailingCloseProvider:
        async def aclose(self) -> None:
            raise close_error

    async def successful_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del tasks
        workspace = config.workspace_root / "trial-workspace"  # type: ignore[attr-defined]
        workspace.mkdir(parents=True)
        sample = _sample_run(provider="configured")
        result = replace(sample.results[0], workspace=str(workspace))
        return replace(sample, results=(result,))

    def fail_postprocessing(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise primary_error

    monkeypatch.setattr(cli, "load_provider_settings", lambda: object())
    monkeypatch.setattr(
        cli,
        "load_shell_settings",
        lambda: SimpleNamespace(shell_command_prefix=None),
    )
    monkeypatch.setattr(cli, "resolve_provider_selection", lambda *args, **kwargs: selection)
    monkeypatch.setattr(cli, "resolve_startup_thinking_level", lambda *args: None)
    monkeypatch.setattr(
        cli,
        "create_model_provider",
        lambda *args, **kwargs: FailingCloseProvider(),
    )
    monkeypatch.setattr(cli, "run_benchmark", successful_benchmark)
    if failure_stage == "archive":
        monkeypatch.setattr(cli, "_archive_benchmark_workspaces", fail_postprocessing)
    else:
        monkeypatch.setattr(cli, "write_benchmark_run", fail_postprocessing)
    monkeypatch.setattr(
        cli,
        "render_benchmark_summary",
        lambda *args, **kwargs: pytest.fail("失败后不得渲染摘要"),
    )

    with pytest.raises(OSError, match=f"{failure_stage} exploded") as caught:
        await cli.run_benchmark_command(
            cli.BenchmarkCliOptions(
                provider="configured",
                model=None,
                task_ids=("repository_lookup",),
                trials=1,
                seed=300,
                output=output,
                keep_workspaces=True,
            )
        )

    assert caught.value is primary_error
    assert any(
        "关闭 runtime provider 失败: RuntimeError: provider close exploded" in note
        for note in getattr(caught.value, "__notes__", ())
    )


@pytest.mark.anyio
@pytest.mark.parametrize("close_fails", [False, True], ids=("close-ok", "close-fails"))
async def test_real_provider_close_is_shielded_without_replacing_external_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    close_fails: bool,
) -> None:
    runner_started = anyio.Event()
    events: list[str] = []
    caught: list[BaseException] = []
    returned_runs: list[object] = []
    active_roots: list[Path] = []
    selection = SimpleNamespace(provider=SimpleNamespace(name="configured"), model="model")

    class ClosableProvider:
        async def aclose(self) -> None:
            events.append("provider-close-start")
            await anyio.sleep(0)
            events.append("provider-close-finish")
            if close_fails:
                raise RuntimeError("provider close exploded")

    async def hanging_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del tasks
        active_roots.append(config.workspace_root)  # type: ignore[attr-defined]
        runner_started.set()
        await anyio.sleep_forever()
        raise AssertionError("unreachable")

    monkeypatch.setattr(cli, "load_provider_settings", lambda: object())
    monkeypatch.setattr(
        cli,
        "load_shell_settings",
        lambda: SimpleNamespace(shell_command_prefix=None),
    )
    monkeypatch.setattr(cli, "resolve_provider_selection", lambda *args, **kwargs: selection)
    monkeypatch.setattr(cli, "resolve_startup_thinking_level", lambda *args: None)
    monkeypatch.setattr(cli, "create_model_provider", lambda *args, **kwargs: ClosableProvider())
    monkeypatch.setattr(cli, "run_benchmark", hanging_benchmark)

    async def invoke() -> None:
        try:
            returned_runs.append(
                await cli.run_benchmark_command(
                    cli.BenchmarkCliOptions(
                        provider="configured",
                        model=None,
                        task_ids=("repository_lookup",),
                        trials=1,
                        seed=300,
                        output=tmp_path / "run.json",
                        keep_workspaces=False,
                    )
                )
            )
        except BaseException as exc:
            caught.append(exc)
            raise

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        await runner_started.wait()
        task_group.cancel_scope.cancel()

    assert returned_runs == []
    assert len(caught) == 1
    assert isinstance(caught[0], anyio.get_cancelled_exc_class())
    assert events == ["provider-close-start", "provider-close-finish"]
    assert len(active_roots) == 1
    assert not active_roots[0].exists()
    if close_fails:
        assert any(
            "关闭 runtime provider 失败: RuntimeError: provider close exploded" in note
            for note in getattr(caught[0], "__notes__", ())
        )


@pytest.mark.anyio
async def test_run_benchmark_command_builds_default_timestamped_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sample = _sample_run()
    written_paths: list[Path] = []

    async def fake_benchmark(config: object, tasks: object) -> BenchmarkRun:
        del config, tasks
        return sample

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_benchmark", fake_benchmark)
    monkeypatch.setattr(
        cli,
        "write_benchmark_run",
        lambda run, path: written_paths.append(path) or path,
    )
    monkeypatch.setattr(cli, "render_benchmark_summary", lambda run, artifact_path: None)

    _, path = await cli.run_benchmark_command(
        cli.BenchmarkCliOptions(
            provider="fake",
            model=None,
            task_ids=("repository_lookup",),
            trials=1,
            seed=300,
            output=None,
            keep_workspaces=False,
        )
    )

    assert path.parent == tmp_path / "benchmark-results"
    assert path.name.endswith("-run-1.json")
    assert written_paths == [path]
