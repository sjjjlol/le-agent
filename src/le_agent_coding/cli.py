"""Command-line entry point for LeAgent.

本模块是 LeAgent 的命令行入口，负责解析参数、选择前端模式（Print/TUI）、
装配 CodingSession，并启动相应的渲染流程。

架构定位：
- 位于 le_agent_coding 层，是应用的 CLI 边界
- 不做业务逻辑，只负责：
  1. 解析命令行参数
  2. 加载配置（Provider、Model、资源路径等）
  3. 选择前端模式（Print mode / TUI mode）
  4. 装配 CodingSession
  5. 启动事件消费循环

两种前端模式：

1. Print Mode（非交互式）：
   - 适合脚本、管道、一次性任务
   - 事件通过 renderer 转换为文本输出
   - 输出格式可选：text / json / transcript
   - 命令：le-agent --print "prompt"

2. TUI Mode（交互式）：
   - 基于 Textual 的全屏终端界面
   - 实时流式渲染、键盘交互、会话管理
   - 命令：le-agent（不带 --print）

配置加载优先级：
1. 命令行参数（最高优先级）
2. 环境变量
3. 配置文件（~/.le-agent/config.json）
4. 默认值（最低优先级）

关键函数：
- main(): CLI 入口，解析参数，分发到对应模式
- run_openai_tui(): TUI 模式入口
- run_print_mode(): Print 模式入口
- create_coding_session_config(): 从 CLI 参数构建 CodingSessionConfig
"""

from __future__ import annotations

import contextlib
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from os import environ
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp
from typing import Annotated

import anyio
import typer
from typer._click.core import Context as ClickContext
from typer.core import TyperGroup

from le_agent.provider import ModelProvider
from le_agent.session import JsonlSessionStorage, SessionEntry, SessionStorage
from le_agent_ai.env import (
    DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
)
from le_agent_coding.benchmark.fake import create_fake_provider
from le_agent_coding.benchmark.models import BenchmarkRun, TrialStatus
from le_agent_coding.benchmark.reporting import render_benchmark_summary, write_benchmark_run
from le_agent_coding.benchmark.runner import BenchmarkRunnerConfig, run_benchmark
from le_agent_coding.benchmark.tasks import load_builtin_tasks, select_tasks
from le_agent_coding.catalog_loader import user_catalog_path
from le_agent_coding.commands import format_reload_summary
from le_agent_coding.credentials import FileCredentialStore
from le_agent_coding.extensions import StderrUiBridge
from le_agent_coding.provider_config import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER_NAME,
    CredentialReader,
    OpenAICompatibleProviderConfig,
    ProviderConfig,
    ProviderSettings,
    load_provider_settings,
    provider_kind,
    resolve_provider_selection,
    resolve_startup_thinking_level,
    save_provider_settings,
    upsert_openai_compatible_provider,
)
from le_agent_coding.provider_runtime import create_model_provider
from le_agent_coding.rendering import PrintOutputMode, create_event_renderer
from le_agent_coding.resources import LeAgentResourcePaths
from le_agent_coding.session import (
    CodingSession,
    CodingSessionConfig,
    TerminalCommandResult,
    jsonl_session_storage,
    parse_terminal_command,
)
from le_agent_coding.session_export import (
    default_session_export_artifact_path,
    export_session_artifact,
    normalize_export_format,
)
from le_agent_coding.session_manager import CodingSessionRecord, SessionManager, validate_session_id
from le_agent_coding.shell_config import load_shell_settings
from le_agent_coding.tui import run_tui_app
from le_agent_coding.update_check import (
    UpdateNotice,
    startup_release_notes_notice,
    startup_update_notice,
)
from le_agent_coding.updater import update_le_agent
from le_agent_coding.version import current_version as _current_version


def _is_utf8_encoding(encoding: str | None) -> bool:
    """Return whether a stream encoding name represents UTF-8."""
    if encoding is None:
        return False
    return encoding.lower().replace("-", "").replace("_", "") == "utf8"


def _force_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 when they are not already UTF-8.

    Windows consoles default these streams to the system codepage (e.g.
    cp1252), which raises UnicodeEncodeError on model output containing
    characters outside that codepage.
    """
    for stream in (sys.stdout, sys.stderr):
        if _is_utf8_encoding(getattr(stream, "encoding", None)):
            continue
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


_force_utf8_streams()


_OPTIONS_WITH_SEPARATE_VALUE = frozenset(
    {
        "--provider",
        "--model",
        "-m",
        "--base-url",
        "--api-key-env",
        "--timeout-seconds",
        "--max-retries",
        "--max-retry-delay-seconds",
        "--cwd",
        "--mode",
        "--output",
        "-o",
        "--session",
        "--resume",
        "--session-id",
        "--system-prompt",
        "--append-system-prompt",
        "--auto-compact-threshold",
        "--extension",
        "-e",
        "-x",
        "--prompt",
    }
)
_BENCHMARK_MODE_CONFLICT = "benchmark cannot be combined with --print/--mode/--export"


def _benchmark_command_index(args: list[str]) -> int | None:
    """返回首个位置参数作为 benchmark 命令时的索引。"""
    skip_value = False
    for index, argument in enumerate(args):
        if skip_value:
            skip_value = False
            continue
        if argument == "--":
            next_index = index + 1
            return (
                next_index if next_index < len(args) and args[next_index] == "benchmark" else None
            )
        if argument in _OPTIONS_WITH_SEPARATE_VALUE:
            skip_value = True
            continue
        if argument.startswith("-"):
            continue
        return index if argument == "benchmark" else None
    return None


class _LeAgentCliGroup(TyperGroup):
    """在 Click 消费尾随选项前保留 benchmark 的顺序语义。"""

    def parse_args(self, ctx: ClickContext, args: list[str]) -> list[str]:
        benchmark_index = _benchmark_command_index(args)
        if benchmark_index is not None:
            trailing = args[benchmark_index + 1 :]
            if any(
                argument in {"--print", "-p", "--mode", "--export"}
                or argument.startswith("--mode=")
                for argument in trailing
            ):
                raise typer.BadParameter(_BENCHMARK_MODE_CONFLICT)
        return super().parse_args(ctx, args)


app = typer.Typer(
    name="le-agent",
    help="LeAgent coding-agent harness.",
    cls=_LeAgentCliGroup,
    add_completion=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)


@dataclass(frozen=True, slots=True)
class BenchmarkCliOptions:
    """保存手工解析后的 benchmark 命令参数。"""

    provider: str
    model: str | None
    task_ids: tuple[str, ...]
    trials: int
    seed: int
    output: Path | None
    keep_workspaces: bool


def providers_command() -> None:
    """List configured model providers."""
    render_provider_settings(load_provider_settings(), credential_reader=FileCredentialStore())


def setup_command(
    *,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    base_url: str = DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    api_key_env: str = "OPENAI_API_KEY",
    model: str = DEFAULT_MODEL,
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    set_default: bool = True,
) -> None:
    """Create or update an OpenAI-compatible provider entry."""
    settings = load_provider_settings()
    provider = OpenAICompatibleProviderConfig(
        name=provider_name,
        base_url=base_url.rstrip("/"),
        api_key_env=api_key_env,
        models=(model,),
        default_model=model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_retry_delay_seconds=max_retry_delay_seconds,
    )
    updated = upsert_openai_compatible_provider(settings, provider, set_default=set_default)
    path = save_provider_settings(updated)
    typer.echo(
        f"Saved provider '{provider.name}' to {user_catalog_path()} and preferences to {path}"
    )
    if provider.api_key_env not in environ:
        typer.echo(
            f"Set {provider.api_key_env} before running LeAgent with this provider.", err=True
        )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    prompt_args: Annotated[
        list[str] | None,
        typer.Argument(help="Initial prompt to run in interactive TUI mode."),
    ] = None,
    print_mode: Annotated[
        bool,
        typer.Option(
            "--print",
            "-p",
            help="Run the positional prompt in non-interactive print mode.",
        ),
    ] = False,
    prompt_option: Annotated[
        str | None,
        typer.Option(
            "--prompt",
            help="Removed; pass the prompt positionally and use --print instead.",
            hidden=True,
        ),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Configured provider name to use."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Model name to request from the provider."),
    ] = None,
    setup_base_url: Annotated[
        str,
        typer.Option("--base-url", help="OpenAI-compatible base URL for `le-agent setup`."),
    ] = DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    setup_api_key_env: Annotated[
        str,
        typer.Option("--api-key-env", help="API key environment variable for `le-agent setup`."),
    ] = "OPENAI_API_KEY",
    setup_timeout_seconds: Annotated[
        float,
        typer.Option(
            "--timeout-seconds",
            help="HTTP timeout in seconds for `le-agent setup` provider requests.",
        ),
    ] = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
    setup_max_retries: Annotated[
        int,
        typer.Option("--max-retries", help="Provider retry count for `le-agent setup`."),
    ] = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    setup_max_retry_delay_seconds: Annotated[
        float,
        typer.Option(
            "--max-retry-delay-seconds",
            help="Provider retry delay in seconds for `le-agent setup`.",
        ),
    ] = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    setup_default: Annotated[
        bool,
        typer.Option("--set-default/--no-set-default", help="Make setup provider the default."),
    ] = True,
    cwd: Annotated[
        Path | None,
        typer.Option("--cwd", help="Working directory for built-in coding tools."),
    ] = None,
    mode: Annotated[
        PrintOutputMode | None,
        typer.Option(
            "--mode",
            help="Run in non-interactive print mode with this output format "
            "(text, json, or transcript).",
        ),
    ] = None,
    output: Annotated[
        str | None,
        typer.Option(
            "--output",
            "-o",
            help="Removed; use --mode instead.",
            hidden=True,
        ),
    ] = None,
    session: Annotated[
        str | None,
        typer.Option("--session", help="Resume a session id in TUI mode."),
    ] = None,
    resume: Annotated[
        str | None,
        typer.Option(
            "--resume",
            help="Removed; use --session <session-id> instead.",
            hidden=True,
        ),
    ] = None,
    new_session: Annotated[
        bool,
        typer.Option("--new-session", help="Create a new session in TUI mode (default)."),
    ] = False,
    session_id: Annotated[
        str | None,
        typer.Option(
            "--session-id",
            help="Set the exact id for the newly created print-mode session.",
        ),
    ] = None,
    system_prompt: Annotated[
        str | None,
        typer.Option(
            "--system-prompt",
            metavar="TEXT_OR_PATH",
            help="Replace the default system-prompt base with literal text or a UTF-8 file.",
        ),
    ] = None,
    append_system_prompt: Annotated[
        list[str] | None,
        typer.Option(
            "--append-system-prompt",
            metavar="TEXT_OR_PATH",
            help="Append literal text or a UTF-8 file to the system prompt (repeatable).",
        ),
    ] = None,
    auto_compact_threshold: Annotated[
        int | None,
        typer.Option(
            "--auto-compact-threshold",
            help="Automatically compact TUI context above this rough token estimate.",
        ),
    ] = None,
    extension: Annotated[
        list[Path] | None,
        typer.Option(
            "--extension",
            "-e",
            help="Load an extension file or directory (repeatable).",
        ),
    ] = None,
    extension_legacy: Annotated[
        list[Path] | None,
        typer.Option(
            "-x",
            help="Removed; use -e/--extension instead.",
            hidden=True,
        ),
    ] = None,
    export: Annotated[
        bool,
        typer.Option(
            "--export",
            help="Export the given session id or JSONL path (mirrors `le-agent export`).",
        ),
    ] = False,
    no_extensions: Annotated[
        bool,
        typer.Option(
            "--no-extensions",
            help="Disable extension directory discovery (explicit -e paths still load).",
        ),
    ] = False,
    project_extensions: Annotated[
        bool,
        typer.Option(
            "--project-extensions",
            help="Also load project .le-agent/extensions (runs project-supplied code at startup).",
        ),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", "-v", help="Show LeAgent's version and exit."),
    ] = False,
) -> None:
    """Run the LeAgent CLI."""
    current_version = _current_version()
    if version:
        typer.echo(f"le-agent {current_version}")
        raise typer.Exit()

    if ctx.invoked_subcommand is not None:
        return

    positional_args = prompt_args or []
    command = positional_args[0] if positional_args else None
    print_requested = print_mode or mode is not None

    if resume is not None:
        raise typer.BadParameter(
            f"--resume was renamed to --session. Use `le-agent --session {resume}` instead."
        )

    if session is not None and new_session:
        raise typer.BadParameter("--session and --new-session cannot be used together")

    if prompt_option is not None:
        raise typer.BadParameter(
            "--prompt was removed. Pass the prompt positionally and use --print, e.g. "
            f'`le-agent --print "{prompt_option}"`.'
        )

    if extension_legacy is not None:
        raise typer.BadParameter("-x was renamed to -e/--extension.")

    if command == "benchmark" and not print_requested and not export:
        _run_benchmark_cli(
            positional_args[1:],
            provider=provider,
            model=model,
            output=Path(output) if output is not None else None,
        )

    if output is not None:
        try:
            legacy_output = PrintOutputMode(output)
        except ValueError as exc:
            raise typer.BadParameter(
                f"Invalid value for --output: {output}", param_hint="--output"
            ) from exc
        raise typer.BadParameter(
            f"--output was renamed to --mode. Use `le-agent --mode {legacy_output.value}` instead."
        )

    effective_output = mode or PrintOutputMode.text

    if session_id is not None:
        if not print_requested:
            raise typer.BadParameter("--session-id is only supported in print mode")
        try:
            validate_session_id(session_id)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    initial_prompt = " ".join(positional_args) if positional_args else None

    if not print_requested and not export and command == "update":
        if len(positional_args) != 1:
            raise typer.BadParameter("Usage: le-agent update")
        update_command()
        raise typer.Exit()

    if not print_requested and not export and command == "sessions" and len(positional_args) == 1:
        render_session_list(SessionManager().list_sessions())
        raise typer.Exit()

    if not print_requested and not export and command == "export":
        _run_export_cli(positional_args[1:])

    if export:
        if print_requested:
            raise typer.BadParameter("--export cannot be combined with --print/--mode.")
        _run_export_cli(positional_args)

    if not print_requested and command == "providers" and len(positional_args) == 1:
        providers_command()
        raise typer.Exit()

    if not print_requested and command == "setup" and len(positional_args) == 1:
        setup_command(
            provider_name=provider or DEFAULT_PROVIDER_NAME,
            base_url=setup_base_url,
            api_key_env=setup_api_key_env,
            model=model or DEFAULT_MODEL,
            timeout_seconds=setup_timeout_seconds,
            max_retries=setup_max_retries,
            max_retry_delay_seconds=setup_max_retry_delay_seconds,
            set_default=setup_default,
        )
        raise typer.Exit()

    extension_paths = tuple(extension or ())
    custom_system_prompt = (
        _resolve_prompt_input(system_prompt, option="--system-prompt")
        if system_prompt is not None
        else None
    )
    resolved_append_system_prompt = _resolve_append_system_prompts(append_system_prompt or ())

    # print 与 TUI 共享同一种 CodingSession 和事件流；这里只选择不同的事件消费者。
    if not print_requested:
        notice = _startup_update_notice()
        try:
            resumable_session_id = anyio.run(
                run_openai_tui,
                model,
                cwd or Path.cwd(),
                session,
                new_session,
                provider,
                auto_compact_threshold,
                initial_prompt,
                notice,
                extension_paths,
                not no_extensions,
                project_extensions,
                custom_system_prompt,
                resolved_append_system_prompt,
            )
        except (RuntimeError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        if resumable_session_id is not None:
            typer.echo(f"To resume this session: le-agent --session {resumable_session_id}")
        raise typer.Exit()

    prompt = _merge_stdin_prompt(initial_prompt or "")
    if not prompt:
        raise typer.BadParameter(
            'Usage: le-agent --print "<prompt>" (or --mode text|json|transcript "<prompt>"); '
            "a prompt can also be piped in via stdin"
        )

    notice = _startup_update_notice()
    if notice is not None and effective_output is PrintOutputMode.text:
        typer.echo(notice.message, err=True)

    try:
        ok = anyio.run(
            run_openai_print_mode,
            prompt,
            model,
            cwd or Path.cwd(),
            effective_output,
            provider,
            None,
            extension_paths,
            not no_extensions,
            project_extensions,
            session_id,
            custom_system_prompt,
            resolved_append_system_prompt,
        )
    except (RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not ok:
        raise typer.Exit(1)


async def run_openai_tui(
    model: str | None,
    cwd: Path,
    session_id: str | None = None,
    new_session: bool = False,
    provider_name: str | None = None,
    auto_compact_token_threshold: int | None = None,
    initial_prompt: str | None = None,
    update_notice: UpdateNotice | None = None,
    extension_paths: tuple[Path, ...] = (),
    extensions_enabled: bool = True,
    project_extensions_enabled: bool = False,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
) -> str | None:
    """Run the Textual TUI and return its resumable session id, if any.

    设计说明：
    - 这是一个适配器函数（Adapter），将 CLI 层的高层级参数转换为 TUI 层的具体配置
    - 职责分离：CLI 处理用户输入/输出通知，TUI 处理具体交互逻辑
    - 支持会话恢复（session_id）或创建新会话（new_session）
    """
    # 构建启动通知：版本更新日志 + 传入的更新通知
    release_notes_notice = startup_release_notes_notice(_current_version())
    startup_notices = (release_notes_notice.message,) if release_notes_notice is not None else ()

    # 透传所有参数给 TUI 应用，添加启动通知
    return await run_tui_app(
        model=model,
        cwd=cwd,
        session_id=session_id,
        new_session=new_session,
        provider_name=provider_name,
        auto_compact_token_threshold=auto_compact_token_threshold,
        initial_prompt=initial_prompt,
        startup_update_notice=update_notice.message if update_notice is not None else None,
        startup_notices=startup_notices,
        extension_paths=extension_paths,
        extensions_enabled=extensions_enabled,
        project_extensions_enabled=project_extensions_enabled,
        custom_system_prompt=custom_system_prompt,
        append_system_prompt=append_system_prompt,
    )


def _startup_update_notice() -> UpdateNotice | None:
    return startup_update_notice(_current_version())


def update_command() -> None:
    """Upgrade LeAgent using the installer that manages the current environment."""
    result = update_le_agent()
    if not result.succeeded:
        typer.echo("Could not safely update LeAgent:", err=True)
        for failure in result.failures:
            typer.echo(f"- {failure}", err=True)
        raise typer.Exit(1)
    if result.stdout:
        typer.echo(result.stdout)
    if result.stderr:
        typer.echo(result.stderr, err=True)
    if result.deferred:
        typer.echo(f"LeAgent update handed off with: {' '.join(result.command or ())}")
    else:
        typer.echo(f"LeAgent update completed with: {' '.join(result.command or ())}")


async def run_benchmark_command(options: BenchmarkCliOptions) -> tuple[BenchmarkRun, Path]:
    """运行一次 benchmark，写入 artifact 并渲染摘要。"""
    tasks = select_tasks(load_builtin_tasks(), options.task_ids or None)
    result_directory = _prepare_benchmark_output(
        options.output,
        keep_workspaces=options.keep_workspaces,
    )
    provider_close_error: Exception | None = None
    provider_close_diagnostic: str | None = None
    with _temporary_benchmark_root() as workspace_root:
        if options.provider == "fake":
            provider_name = "fake"
            model = "benchmark-fake"
            typer.echo(
                f"Running benchmark: provider={provider_name} model={model} "
                f"tasks={len(tasks)} trials={options.trials}"
            )
            run = await run_benchmark(
                BenchmarkRunnerConfig(
                    provider_name=provider_name,
                    model=model,
                    trials=options.trials,
                    seed=options.seed,
                    keep_workspaces=options.keep_workspaces,
                    workspace_root=workspace_root,
                    provider_factory=lambda task, trial: create_fake_provider(task.id),
                ),
                tasks,
            )
        else:
            settings = load_provider_settings()
            selection = resolve_provider_selection(
                settings,
                provider_name=options.provider,
                model=options.model,
            )
            shell_settings = load_shell_settings()
            provider_name = selection.provider.name
            model = selection.model
            typer.echo(
                f"Running benchmark: provider={provider_name} model={model} "
                f"tasks={len(tasks)} trials={options.trials}"
            )
            typer.echo(
                "Warning: real benchmark mode calls an external model and may incur costs.",
                err=True,
            )
            runtime_provider = create_model_provider(
                selection.provider,
                model=model,
                thinking_level=resolve_startup_thinking_level(selection.provider, model),
            )
            runtime_error: BaseException | None = None
            try:
                run = await run_benchmark(
                    BenchmarkRunnerConfig(
                        provider_name=provider_name,
                        model=model,
                        trials=options.trials,
                        seed=options.seed,
                        keep_workspaces=options.keep_workspaces,
                        workspace_root=workspace_root,
                        provider_factory=lambda task, trial: runtime_provider,
                        shell_command_prefix=shell_settings.shell_command_prefix,
                    ),
                    tasks,
                )
            except BaseException as exc:
                runtime_error = exc
                raise
            finally:
                # 外部取消不能中断 provider 的资源释放；关闭失败也不能覆盖主异常。
                with anyio.CancelScope(shield=True):
                    try:
                        await runtime_provider.aclose()
                    except Exception as exc:
                        provider_close_error = exc
                        provider_close_diagnostic = (
                            f"关闭 runtime provider 失败: {type(exc).__name__}: {exc}"
                        )

                if runtime_error is not None and provider_close_diagnostic is not None:
                    runtime_error.add_note(provider_close_diagnostic)

                if runtime_error is None:
                    # shield 期间到达的取消必须在继续归档或写 artifact 前重新生效。
                    try:
                        await anyio.lowlevel.checkpoint_if_cancelled()
                    except anyio.get_cancelled_exc_class() as exc:
                        if provider_close_diagnostic is not None:
                            exc.add_note(provider_close_diagnostic)
                        raise

        if options.keep_workspaces:
            try:
                run = _archive_benchmark_workspaces(
                    run,
                    active_root=workspace_root,
                    result_directory=result_directory,
                )
            except BaseException as exc:
                if provider_close_diagnostic is not None:
                    exc.add_note(provider_close_diagnostic)
                raise

    artifact_path = options.output
    if artifact_path is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        artifact_path = result_directory / f"{timestamp}-{run.run_id}.json"
    try:
        written_path = write_benchmark_run(run, artifact_path)
        render_benchmark_summary(run, artifact_path=written_path)
    except BaseException as exc:
        if provider_close_diagnostic is not None:
            exc.add_note(provider_close_diagnostic)
        raise
    if provider_close_error is not None:
        provider_close_error.add_note(f"benchmark artifact 已写入: {written_path.resolve()}")
        retained_workspaces = tuple(
            result.workspace for result in run.results if result.workspace is not None
        )
        if retained_workspaces:
            for workspace in retained_workspaces:
                provider_close_error.add_note(f"benchmark workspace 已保留: {workspace}")
        else:
            provider_close_error.add_note("benchmark workspace 未请求保留，临时目录已清理")
        raise provider_close_error
    return run, written_path


def _archive_benchmark_workspaces(
    run: BenchmarkRun,
    *,
    active_root: Path,
    result_directory: Path,
) -> BenchmarkRun:
    """将保留的活跃 workspace 搬到结果目录并更新冻结结果路径。"""
    retained = tuple(result for result in run.results if result.workspace is not None)
    if not retained:
        return run

    archive_root = (result_directory / "workspaces" / run.run_id).resolve()
    if archive_root.exists():
        raise FileExistsError(f"Benchmark workspace archive already exists: {archive_root}")

    active_root = active_root.resolve()
    moves: dict[Path, Path] = {}
    for result in retained:
        source = Path(result.workspace or "").resolve()
        if not source.is_relative_to(active_root) or source.parent != active_root:
            raise ValueError(f"Benchmark workspace escaped active root: {source}")
        if not source.is_dir():
            raise FileNotFoundError(f"Benchmark workspace is missing: {source}")
        destination = archive_root / source.name
        if destination in moves.values():
            raise FileExistsError(f"Duplicate benchmark workspace archive: {destination}")
        moves[source] = destination

    archive_root.mkdir(parents=True, exist_ok=False)
    for source, destination in moves.items():
        shutil.move(str(source), str(destination))

    archived_results = tuple(
        replace(
            result,
            workspace=str(moves[Path(result.workspace).resolve()]),
        )
        if result.workspace is not None
        else result
        for result in run.results
    )
    return replace(run, results=archived_results)


@contextlib.contextmanager
def _temporary_benchmark_root() -> Iterator[Path]:
    """创建系统临时活跃根，且不让清理错误覆盖已有 primary 异常。"""
    root = Path(mkdtemp(prefix="le-agent-benchmark-"))
    primary_error: BaseException | None = None
    try:
        yield root
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if root.exists():
            try:
                shutil.rmtree(root)
            except OSError as exc:
                diagnostic = f"清理 benchmark 临时根失败: {type(exc).__name__}: {exc}"
                if primary_error is None:
                    raise
                primary_error.add_note(diagnostic)


def _prepare_benchmark_output(
    output: Path | None,
    *,
    keep_workspaces: bool,
) -> Path:
    """在模型调用前确认 artifact 目录支持同目录原子写入。"""
    if output is not None and output.is_dir():
        raise IsADirectoryError(f"Benchmark output path is a directory: {output}")

    result_directory = output.parent if output is not None else Path.cwd() / "benchmark-results"
    result_directory.mkdir(parents=True, exist_ok=True)
    if keep_workspaces:
        archive_parent = result_directory / "workspaces"
        if archive_parent.exists() and not archive_parent.is_dir():
            raise NotADirectoryError(
                f"Benchmark workspace archive path is not a directory: {archive_parent}"
            )
        archive_parent.mkdir(exist_ok=True)
        archive_probe: Path | None = None
        try:
            # 随机子目录验证真实 mkdir 权限；绝不复用或覆盖用户目录。
            archive_probe = Path(
                mkdtemp(
                    prefix=".le-agent-benchmark-archive-preflight-",
                    dir=archive_parent,
                )
            )
        finally:
            if archive_probe is not None:
                archive_probe.rmdir()
    source_path: Path | None = None
    destination_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=result_directory,
            prefix=".le-agent-benchmark-preflight-",
            delete=False,
        ) as source:
            source_path = Path(source.name)
            source.write(b"probe")
            source.flush()
        with NamedTemporaryFile(
            mode="wb",
            dir=result_directory,
            prefix=".le-agent-benchmark-preflight-",
            delete=False,
        ) as destination:
            destination_path = Path(destination.name)
        source_path.replace(destination_path)
    finally:
        if source_path is not None:
            source_path.unlink(missing_ok=True)
        if destination_path is not None:
            destination_path.unlink(missing_ok=True)
    return result_directory


def _run_benchmark_cli(
    args: list[str],
    *,
    provider: str | None,
    model: str | None,
    output: Path | None,
) -> None:
    """解析并运行 `le-agent benchmark`，随后按 trial 状态退出。"""
    try:
        args, provider, model, output = _extract_benchmark_shared_options(
            args,
            provider=provider,
            model=model,
            output=output,
        )
        options = _parse_benchmark_cli_args(
            args,
            provider=provider,
            model=model,
            output=output,
        )
        run, _ = anyio.run(run_benchmark_command, options)
    except (OSError, RuntimeError, ValueError) as exc:
        diagnostics = tuple(getattr(exc, "__notes__", ()))
        if any(note.startswith("benchmark artifact 已写入:") for note in diagnostics):
            typer.echo(
                f"Benchmark completed, but provider cleanup failed: {exc}",
                err=True,
            )
            for diagnostic in diagnostics:
                typer.echo(diagnostic, err=True)
            raise typer.Exit(1) from exc
        raise typer.BadParameter(str(exc)) from exc

    infrastructure_statuses = {
        TrialStatus.agent_failure,
        TrialStatus.timeout,
        TrialStatus.evaluation_failure,
    }
    exit_code = 1 if any(result.status in infrastructure_statuses for result in run.results) else 0
    raise typer.Exit(exit_code)


def _extract_benchmark_shared_options(
    args: list[str],
    *,
    provider: str | None,
    model: str | None,
    output: Path | None,
) -> tuple[list[str], str | None, str | None, Path | None]:
    """提取 variadic 参数吞掉的 callback 级 benchmark 选项。"""
    remaining: list[str] = []
    index = 0
    while index < len(args):
        argument = args[index]
        option: str | None = None
        value: str | None = None
        if argument in {"--provider", "--model", "-m", "--output", "-o"}:
            option = argument
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                raise RuntimeError(f"{option} requires a value")
            index += 1
            value = args[index]
        elif argument.startswith(("--provider=", "--model=", "--output=")):
            option, _, value = argument.partition("=")
        elif argument.startswith("-m") and not argument.startswith("--"):
            option = "-m"
            value = argument[2:]
        elif argument.startswith("-o") and not argument.startswith("--"):
            option = "-o"
            value = argument[2:]

        if option is None:
            remaining.append(argument)
        elif not value:
            raise RuntimeError(f"{option} requires a value")
        elif option == "--provider":
            provider = value
        elif option in {"--model", "-m"}:
            model = value
        else:
            output = Path(value)
        index += 1
    return remaining, provider, model, output


def render_session_list(records: list[CodingSessionRecord]) -> None:
    """Render indexed sessions for the CLI."""
    if not records:
        typer.echo("No sessions found.")
        return

    for record in records:
        title = record.title or "Untitled"
        typer.echo(f"{record.id}\t{title}\t{record.model}\t{record.cwd}")


async def export_session_command(
    session_ref: str,
    output_path: Path | None = None,
    export_format: str | None = None,
    session_manager: SessionManager | None = None,
) -> Path:
    """Export an indexed session id or JSONL file path."""
    session_path, title = _resolve_export_source(session_ref, session_manager)
    entries = await JsonlSessionStorage(session_path).read_all()
    normalized_format = normalize_export_format(
        export_format or (output_path.suffix.removeprefix(".") if output_path else "html")
    )
    destination = _resolve_export_destination(
        output_path,
        session_path=session_path,
        format=normalized_format,
    )
    return export_session_artifact(
        entries,
        destination,
        title=title,
        source=str(session_path),
        format=normalized_format,
    )


def _run_export_cli(args: list[str]) -> None:
    """Run `le-agent export`/`le-agent --export` and exit."""
    try:
        session_ref, output_path, export_format = _parse_export_cli_args(args)
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        exported_path = anyio.run(
            export_session_command,
            session_ref,
            output_path,
            export_format,
        )
    except (RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Exported session to {exported_path}")
    raise typer.Exit()


def _resolve_prompt_input(value: str, *, option: str) -> str:
    """Resolve an existing UTF-8 file, otherwise preserve literal prompt text."""
    try:
        path = Path(value).expanduser()
    except RuntimeError:
        return value
    try:
        exists = path.exists()
    except OSError as exc:
        raise typer.BadParameter(
            f"Could not inspect {option} path {path}: {exc}",
            param_hint=option,
        ) from exc
    if not exists:
        return value
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise typer.BadParameter(
            f"Could not read {option} file {path}: {exc}",
            param_hint=option,
        ) from exc


def _resolve_append_system_prompts(values: tuple[str, ...] | list[str]) -> str | None:
    """Resolve repeated append inputs in order and separate them by one blank line."""
    if not values:
        return None
    return "\n\n".join(
        _resolve_prompt_input(value, option="--append-system-prompt") for value in values
    )


def _merge_stdin_prompt(prompt: str) -> str:
    """Merge piped stdin content into a print-mode prompt, mirroring Pi.

    When stdin is not a terminal (e.g. `cat file | le-agent -p "..."`), its
    contents are prepended to the prompt text.
    """
    stdin = sys.stdin
    if stdin is None:
        return prompt
    try:
        if stdin.isatty():
            return prompt
    except (AttributeError, ValueError):
        return prompt
    try:
        piped = stdin.read()
    except (OSError, ValueError):
        return prompt
    if not piped:
        return prompt
    if not prompt:
        return piped
    return f"{piped}\n\n{prompt}"


def _parse_export_cli_args(args: list[str]) -> tuple[str, Path | None, str | None]:
    if not args:
        raise RuntimeError(
            "Usage: le-agent export <session-id-or-jsonl> [--format html|jsonl] [output]"
        )
    session_ref = args[0]
    output_path: Path | None = None
    export_format: str | None = None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg == "--format":
            index += 1
            if index >= len(args):
                raise RuntimeError(
                    "Usage: le-agent export <session-id-or-jsonl> [--format html|jsonl] [output]"
                )
            export_format = args[index]
        elif arg.startswith("--format="):
            export_format = arg.partition("=")[2]
        elif arg.startswith("-"):
            raise RuntimeError(f"Unknown export option: {arg}")
        elif output_path is None:
            output_path = Path(arg).expanduser()
        else:
            raise RuntimeError(
                "Usage: le-agent export <session-id-or-jsonl> [--format html|jsonl] [output]"
            )
        index += 1
    return session_ref, output_path, export_format


def _parse_benchmark_cli_args(
    args: list[str],
    *,
    provider: str | None,
    model: str | None,
    output: Path | None,
) -> BenchmarkCliOptions:
    """解析 variadic prompt 参数中的 benchmark 专属选项。"""
    task_ids: list[str] = []
    trials = 1
    seed = 300
    keep_workspaces = False
    index = 0

    def value_after(option: str) -> str:
        nonlocal index
        if index + 1 >= len(args) or args[index + 1].startswith("--"):
            raise RuntimeError(f"{option} requires a value")
        index += 1
        return args[index]

    def equals_value(argument: str, option: str) -> str:
        value = argument.partition("=")[2]
        if not value:
            raise RuntimeError(f"{option} requires a value")
        return value

    def integer_value(value: str, option: str) -> int:
        try:
            return int(value)
        except ValueError as exc:
            raise RuntimeError(f"{option} must be an integer") from exc

    while index < len(args):
        argument = args[index]
        if argument == "--task":
            task_ids.append(value_after("--task"))
        elif argument.startswith("--task="):
            task_ids.append(equals_value(argument, "--task"))
        elif argument == "--trials":
            trials = integer_value(value_after("--trials"), "--trials")
        elif argument.startswith("--trials="):
            trials = integer_value(equals_value(argument, "--trials"), "--trials")
        elif argument == "--seed":
            seed = integer_value(value_after("--seed"), "--seed")
        elif argument.startswith("--seed="):
            seed = integer_value(equals_value(argument, "--seed"), "--seed")
        elif argument == "--keep-workspaces":
            keep_workspaces = True
        else:
            raise RuntimeError(f"Unknown benchmark argument: {argument}")
        index += 1

    if trials < 1:
        raise RuntimeError("--trials must be at least 1")
    return BenchmarkCliOptions(
        provider=provider or "fake",
        model=model,
        task_ids=tuple(task_ids),
        trials=trials,
        seed=seed,
        output=output,
        keep_workspaces=keep_workspaces,
    )


def _resolve_export_destination(
    output_path: Path | None,
    *,
    session_path: Path,
    format: str,
) -> Path:
    if output_path is None:
        return default_session_export_artifact_path(
            session_path,
            destination_dir=Path.cwd(),
            format=format,
        )
    if output_path.suffix:
        return output_path
    return default_session_export_artifact_path(
        session_path,
        destination_dir=output_path,
        format=format,
    )


def _resolve_export_source(
    session_ref: str,
    session_manager: SessionManager | None = None,
) -> tuple[Path, str]:
    candidate_path = Path(session_ref).expanduser()
    if candidate_path.exists():
        if candidate_path.is_dir():
            raise RuntimeError(f"Session export source is a directory: {candidate_path}")
        return candidate_path, f"LeAgent session {candidate_path.stem}"

    manager = session_manager or SessionManager()
    record = manager.get_session(session_ref)
    if record is None:
        raise RuntimeError(f"Unknown session or file: {session_ref}")

    title = record.title or f"LeAgent session {record.id}"
    return record.path, title


def render_provider_settings(
    settings: ProviderSettings,
    *,
    credential_reader: CredentialReader | None = None,
) -> None:
    """Render configured providers for the CLI."""
    for provider in settings.providers:
        marker = "*" if provider.name == settings.default_provider else " "
        models = ",".join(provider.models)
        typer.echo(
            f"{marker}\t{provider.name}\t{provider_kind(provider)}\t"
            f"{provider.default_model}\t{models}\t{provider.api_key_env}\t"
            f"{_provider_credential_status(provider, credential_reader=credential_reader)}\t"
            f"{provider.base_url}\t{provider.timeout_seconds:g}s\t"
            f"retries={provider.max_retries}\t"
            f"retry_delay={provider.max_retry_delay_seconds:g}s"
        )


def _provider_credential_status(
    provider: ProviderConfig,
    *,
    credential_reader: CredentialReader | None,
) -> str:
    if provider.credential_name and credential_reader is not None:
        if provider_kind(provider) == "openai-codex":
            get_oauth = getattr(credential_reader, "get_oauth", None)
            if get_oauth is not None and get_oauth(provider.credential_name) is not None:
                return f"stored:{provider.credential_name}"
        elif credential_reader.get(provider.credential_name):
            return f"stored:{provider.credential_name}"
    if environ.get(provider.api_key_env):
        return f"env:{provider.api_key_env}"
    return "missing"


async def run_openai_print_mode(
    prompt: str,
    model: str | None,
    cwd: Path,
    output: PrintOutputMode = PrintOutputMode.text,
    provider_name: str | None = None,
    session_manager: SessionManager | None = None,
    extension_paths: tuple[Path, ...] = (),
    extensions_enabled: bool = True,
    project_extensions_enabled: bool = False,
    session_id: str | None = None,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
) -> bool:
    """Run print mode with the OpenAI-compatible provider configured from the environment."""
    settings = load_provider_settings()
    shell_settings = load_shell_settings()
    selection = resolve_provider_selection(settings, provider_name=provider_name, model=model)
    provider = create_model_provider(
        selection.provider,
        model=selection.model,
        thinking_level=resolve_startup_thinking_level(selection.provider, selection.model),
    )
    try:
        manager = session_manager or SessionManager()
        record = _create_print_session(
            manager,
            cwd=cwd,
            model=selection.model,
            session_id=session_id,
        )
        return await run_print_mode(
            prompt=prompt,
            model=selection.model,
            cwd=record.cwd,
            provider=provider,
            output=output,
            storage=jsonl_session_storage(record.path),
            session_id=record.id,
            session_manager=manager,
            provider_name=selection.provider.name,
            provider_settings=settings,
            runtime_provider_config=selection.provider,
            shell_command_prefix=shell_settings.shell_command_prefix,
            extension_paths=extension_paths,
            extensions_enabled=extensions_enabled,
            project_extensions_enabled=project_extensions_enabled,
            custom_system_prompt=custom_system_prompt,
            append_system_prompt=append_system_prompt,
        )
    finally:
        await provider.aclose()


def _create_print_session(
    manager: SessionManager,
    *,
    cwd: Path,
    model: str,
    session_id: str | None,
) -> CodingSessionRecord:
    """Create an isolated print-mode session, refusing transcript collisions."""
    return manager.create_session_exclusive(cwd=cwd, model=model, session_id=session_id)


async def run_print_mode(
    *,
    prompt: str,
    model: str,
    cwd: Path,
    provider: ModelProvider,
    output: PrintOutputMode = PrintOutputMode.text,
    resource_paths: LeAgentResourcePaths | None = None,
    storage: SessionStorage | None = None,
    session_id: str | None = None,
    session_manager: SessionManager | None = None,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    provider_settings: ProviderSettings | None = None,
    runtime_provider_config: ProviderConfig | None = None,
    shell_command_prefix: str | None = None,
    extension_paths: tuple[Path, ...] = (),
    extensions_enabled: bool = True,
    project_extensions_enabled: bool = False,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
) -> bool:
    """Run one non-interactive prompt and print streamed events.

    Returns False when the agent emits a non-recoverable error so CLI callers
    can fail non-interactive runs while still rendering the error message.
    """
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=provider,
            model=model,
            cwd=cwd,
            storage=storage or _MemorySessionStorage(),
            resource_paths=resource_paths,
            session_id=session_id,
            session_manager=session_manager,
            provider_name=provider_name,
            provider_settings=provider_settings,
            runtime_provider_config=runtime_provider_config,
            shell_command_prefix=shell_command_prefix,
            extension_paths=extension_paths,
            extensions_enabled=extensions_enabled,
            project_extensions_enabled=project_extensions_enabled,
            custom_system_prompt=custom_system_prompt,
            append_system_prompt=append_system_prompt,
        )
    )
    session.extension_runtime.set_ui_bridge(StderrUiBridge())
    await session.emit_pending_session_start()
    renderer = create_event_renderer(
        output,
        custom_message_renderer=session.extension_runtime.render_custom_message,
    )
    try:
        terminal_command = parse_terminal_command(prompt)
        if terminal_command is not None:
            result = await session.run_terminal_command(
                terminal_command.command,
                add_to_context=terminal_command.add_to_context,
            )
            typer.echo(_format_terminal_command_result(result))
            return result.ok
        command = session.handle_command(prompt)
        if command.handled:
            message = command.message
            if command.reload_requested:
                try:
                    summary = await session.reload()
                except ValueError as exc:
                    message = f"Could not reload: {exc}"
                else:
                    message = format_reload_summary(summary)
            if message:
                typer.echo(message)
            return True
        async for event in session.prompt(prompt):
            renderer.render(event)
        return renderer.finish()
    finally:
        await session.aclose()


class _MemorySessionStorage:
    """Append-only in-memory storage for direct print-mode tests."""

    def __init__(self) -> None:
        self.entries: list[SessionEntry] = []

    async def append(self, entry: SessionEntry) -> None:
        self.entries.append(entry)

    async def read_all(self) -> list[SessionEntry]:
        return list(self.entries)


def _format_terminal_command_result(result: TerminalCommandResult) -> str:
    context_status = "added to context" if result.added_to_context else "not added to context"
    return f"$ {result.command}\n[{context_status}]\n{result.output}"
