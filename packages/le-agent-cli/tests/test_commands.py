from pathlib import Path
from types import SimpleNamespace

import pytest
from le_agent_cli.commands import (
    CommandAvailability,
    CommandContext,
    CommandError,
    CommandRegistry,
    CommandResult,
    CommandSpec,
    CommandUnavailableError,
    create_builtin_registry,
)


@pytest.mark.asyncio
async def test_command_registry_uses_metadata_for_suggestions_aliases_and_execution() -> None:
    registry = CommandRegistry()

    async def execute(argument: str, context: CommandContext) -> CommandResult:
        del context
        return CommandResult(message=f"model={argument}")

    registry.register(
        CommandSpec(
            name="model",
            description="选择模型",
            argument_hint="<name>",
            aliases=("m",),
            handler=execute,
        )
    )

    assert [item.name for item in registry.suggest("mod")] == ["model"]
    assert [item.name for item in registry.suggest("mdl")] == ["model"]
    assert registry.resolve("m").name == "model"
    assert await registry.execute("/m claude", CommandContext()) == CommandResult(message="model=claude")


def test_builtin_command_wins_skill_name_conflict() -> None:
    registry = CommandRegistry()
    registry.register(CommandSpec(name="tree", description="skill", source="skill"))
    registry.register(CommandSpec(name="tree", description="builtin", source="builtin"))

    assert registry.resolve("tree").description == "builtin"


@pytest.mark.asyncio
async def test_unavailable_command_reports_its_reason() -> None:
    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="tree",
            description="会话树",
            availability=lambda _context: CommandAvailability(False, "无持久化会话"),
        )
    )

    with pytest.raises(CommandUnavailableError, match="无持久化会话"):
        await registry.execute("/tree", CommandContext())


def test_builtin_registry_contains_clear_as_an_independent_visible_command() -> None:
    registry = create_builtin_registry()

    assert {command.name for command in registry.suggest()} == {
        "compact",
        "clear",
        "help",
        "hotkeys",
        "model",
        "name",
        "new",
        "quit",
        "resume",
        "session",
        "status",
        "settings",
        "skill",
        "skills",
        "tree",
    }
    assert registry.resolve("clear").name == "clear"
    assert registry.resolve("status").name == "status"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/resume abc", "/tree", "/name demo"])
async def test_persistence_commands_explain_why_they_are_disabled_without_session(command: str) -> None:
    bundle = SimpleNamespace(persistent_session=False)
    runtime = SimpleNamespace(bundle=bundle)

    with pytest.raises(CommandUnavailableError, match="--no-session"):
        await create_builtin_registry().execute(command, CommandContext(runtime=runtime))


@pytest.mark.asyncio
async def test_model_and_compact_commands_use_runtime_and_custom_instructions() -> None:
    calls: list[tuple[str, str | None]] = []

    class Harness:
        pass

    class Runtime:
        bundle = SimpleNamespace(
            harness=Harness(),
            persistent_session=True,
            model_name="old",
            model_names=("old", "new"),
        )

        async def switch_model(self, name: str) -> None:
            calls.append(("model", name))

        async def compact(self, instructions: str | None = None) -> bool:
            calls.append(("compact", instructions))
            return True

    registry = create_builtin_registry()
    context = CommandContext(runtime=Runtime())

    assert (await registry.execute("/model new", context)).message == "已切换模型：new"
    assert (await registry.execute("/compact 重点保留接口决策", context)).message == "上下文压缩完成"
    assert calls == [
        ("model", "new"),
        ("compact", "重点保留接口决策"),
    ]


def test_registry_rejects_invalid_names_and_reports_unknown_commands() -> None:
    registry = CommandRegistry()
    with pytest.raises(ValueError, match="one non-empty word"):
        registry.register(CommandSpec("two words", "bad"))
    with pytest.raises(CommandError, match="未知命令"):
        registry.resolve("missing")


@pytest.mark.asyncio
async def test_remaining_builtin_commands_execute_through_one_context() -> None:
    calls: list[tuple[str, str | None]] = []

    class Session:
        id = "session-123"

        async def entries(self):
            return [1, 2]

        async def append_session_info(self, name: str) -> None:
            calls.append(("name", name))

    skill = SimpleNamespace(
        name="review",
        description="Review code",
        path=Path("/skills/review/SKILL.md"),
        body="Inspect carefully",
    )

    class Runtime:
        bundle = SimpleNamespace(
            harness=SimpleNamespace(),
            persistent_session=True,
            model_name="test",
            model_names=("test",),
            policy=SimpleNamespace(mode=SimpleNamespace(value="confirm")),
            skills={"review": skill},
            session=Session(),
        )

        async def new_session(self) -> None:
            calls.append(("new", None))

        async def resume(self, identifier: str) -> None:
            calls.append(("resume", identifier))

        async def prompt(self, prompt: str) -> None:
            calls.append(("prompt", prompt))

        async def name_session(self, name: str) -> None:
            calls.append(("name", name))

        def abort(self) -> None:
            calls.append(("abort", None))

    async def select_setting(_current: str) -> str | None:
        return None

    async def show_tree() -> str:
        return "已回溯"

    context = CommandContext(
        runtime=Runtime(),
        data={"select_setting": select_setting, "show_tree": show_tree},
    )
    registry = create_builtin_registry()

    assert "可用命令" in (await registry.execute("/help", context)).message
    assert (await registry.execute("/settings", context)).message == "已取消设置修改"
    new_result = await registry.execute("/new", context)
    assert new_result.message == "已创建新会话"
    assert new_result.transcript_effect == "clear"
    clear_result = await registry.execute("/clear", context)
    assert clear_result.message is None
    assert clear_result.transcript_effect == "clear"
    resume_result = await registry.execute("/resume saved", context)
    assert resume_result.message == "已恢复会话：saved"
    assert resume_result.transcript_effect == "reload"
    tree_result = await registry.execute("/tree", context)
    assert tree_result.message == "已回溯"
    assert tree_result.transcript_effect == "reload"
    assert "Review code" in (await registry.execute("/skills", context)).message
    assert (await registry.execute("/skill review focus", context)).message is None
    assert "session-123" in (await registry.execute("/session", context)).message
    assert (await registry.execute("/name Demo", context)).message == "会话已命名：Demo"
    assert "Alt+Enter" in (await registry.execute("/hotkeys", context)).message
    assert (await registry.execute("/quit", context)).exit_requested
    assert calls[0:2] == [("new", None), ("resume", "saved")]
    assert ("name", "Demo") in calls
    assert calls[-1] == ("abort", None)
    assert "/skills/review/SKILL.md" in calls[2][1]
    assert "Inspect carefully" not in calls[2][1]

    with pytest.raises(CommandError, match="用法"):
        await registry.execute("/skill", context)
    with pytest.raises(CommandError, match="未知 Skill"):
        await registry.execute("/skill missing", context)
    with pytest.raises(CommandError, match="用法"):
        await registry.execute("/name", context)


@pytest.mark.asyncio
async def test_ui_backed_commands_report_cancel_and_missing_ui() -> None:
    runtime = SimpleNamespace(
        bundle=SimpleNamespace(
            persistent_session=True,
            model_name="test",
            model_names=("test",),
        )
    )

    async def cancel(*_args):
        return None

    registry = create_builtin_registry()
    model_result = await registry.execute("/model", CommandContext(runtime, {"select_model": cancel}))
    resume_result = await registry.execute("/resume", CommandContext(runtime, {"select_session": cancel}))
    assert model_result.message == "已取消模型切换"
    assert resume_result.message == "已取消恢复会话"
    with pytest.raises(CommandError, match="当前界面不支持"):
        await registry.execute("/tree", CommandContext(runtime))


@pytest.mark.asyncio
async def test_session_reports_jsonl_link_and_status_only_reports_context_usage(tmp_path: Path) -> None:
    session_file = tmp_path / "session.jsonl"

    class Session:
        id = "session-123"

        async def entries(self):
            return [1, 2, 3]

    model = SimpleNamespace(context_window=1000)
    bundle = SimpleNamespace(
        session=Session(),
        session_file=session_file,
        persistent_session=True,
        model_name="test-model",
        harness=SimpleNamespace(agent=None, config=SimpleNamespace(model=model)),
    )
    registry = create_builtin_registry()
    context = CommandContext(runtime=SimpleNamespace(bundle=bundle))

    session_result = await registry.execute("/session", context)
    status_result = await registry.execute("/status", context)

    assert session_result.message is not None and "session-123" in session_result.message
    assert str(session_file) in session_result.message
    assert session_result.links[0].target == session_file.resolve().as_uri()
    assert status_result.message == "上下文：0% · 0 / 1,000 tokens"
    assert "会话" not in status_result.message


@pytest.mark.asyncio
async def test_session_explains_no_jsonl_in_memory_mode() -> None:
    class Session:
        id = "memory"

        async def entries(self):
            return []

    bundle = SimpleNamespace(
        session=Session(),
        session_file=None,
        persistent_session=False,
        model_name="test",
    )
    result = await create_builtin_registry().execute(
        "/session", CommandContext(runtime=SimpleNamespace(bundle=bundle))
    )

    assert "仅内存，无 JSONL 文件" in (result.message or "")
    assert result.links == ()
