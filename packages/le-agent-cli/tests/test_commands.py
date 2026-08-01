import pytest
from le_agent_cli.commands import (
    CommandAvailability,
    CommandContext,
    CommandRegistry,
    CommandResult,
    CommandSpec,
    CommandUnavailableError,
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
