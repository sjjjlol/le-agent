from types import SimpleNamespace

import pytest
from le_agent_cli.commands import (
    CommandAvailability,
    CommandContext,
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


def test_builtin_registry_contains_v2_command_set_and_hidden_compatibility_aliases() -> None:
    registry = create_builtin_registry()

    assert {command.name for command in registry.suggest()} == {
        "compact",
        "help",
        "hotkeys",
        "model",
        "name",
        "new",
        "quit",
        "resume",
        "session",
        "settings",
        "skill",
        "skills",
        "tree",
    }
    assert registry.resolve("status").name == "session"
    assert registry.resolve("clear").name == "new"
    assert "clear" not in {command.name for command in registry.suggest()}


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
        async def compact(self, *, instructions: str | None = None) -> bool:
            calls.append(("compact", instructions))
            return True

    class Runtime:
        bundle = SimpleNamespace(
            harness=Harness(),
            persistent_session=True,
            model_name="old",
            model_names=("old", "new"),
        )

        async def switch_model(self, name: str) -> None:
            calls.append(("model", name))

        async def reload_context(self) -> None:
            calls.append(("reload", None))

    registry = create_builtin_registry()
    context = CommandContext(runtime=Runtime())

    assert (await registry.execute("/model new", context)).message == "已切换模型：new"
    assert (await registry.execute("/compact 重点保留接口决策", context)).message == "上下文压缩完成"
    assert calls == [("model", "new"), ("compact", "重点保留接口决策"), ("reload", None)]
