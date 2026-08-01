"""Metadata-driven slash command registration and dispatch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

CommandSource = Literal["builtin", "skill", "extension"]


@dataclass(frozen=True, slots=True)
class CommandAvailability:
    available: bool = True
    reason: str | None = None


@dataclass(slots=True)
class CommandContext:
    runtime: Any = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandResult:
    message: str | None = None
    exit_requested: bool = False


CommandHandler = Callable[[str, CommandContext], Awaitable[CommandResult]]
CommandCompleter = Callable[[str, CommandContext], Awaitable[list[str]] | list[str]]
AvailabilityCheck = Callable[[CommandContext], CommandAvailability]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    description: str
    argument_hint: str | None = None
    aliases: tuple[str, ...] = ()
    source: CommandSource = "builtin"
    hidden: bool = False
    availability: AvailabilityCheck | None = None
    completer: CommandCompleter | None = None
    handler: CommandHandler | None = None


class CommandError(ValueError):
    pass


class CommandUnavailableError(CommandError):
    pass


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}
        self._aliases: dict[str, str] = {}

    def register(self, command: CommandSpec) -> None:
        name = command.name.strip().removeprefix("/")
        if not name or any(character.isspace() for character in name):
            raise ValueError("command name must be one non-empty word")
        existing = self._commands.get(name)
        if existing and self._priority(existing.source) >= self._priority(command.source):
            return
        if existing:
            for alias in existing.aliases:
                self._aliases.pop(alias, None)
        normalized = CommandSpec(
            name=name,
            description=command.description,
            argument_hint=command.argument_hint,
            aliases=tuple(alias.strip().removeprefix("/") for alias in command.aliases),
            source=command.source,
            hidden=command.hidden,
            availability=command.availability,
            completer=command.completer,
            handler=command.handler,
        )
        self._commands[name] = normalized
        for alias in normalized.aliases:
            if alias and alias not in self._commands:
                self._aliases[alias] = name

    def resolve(self, name: str) -> CommandSpec:
        normalized = name.strip().removeprefix("/")
        canonical = self._aliases.get(normalized, normalized)
        try:
            return self._commands[canonical]
        except KeyError as error:
            raise CommandError(f"未知命令: /{normalized}") from error

    def suggest(self, query: str = "") -> list[CommandSpec]:
        normalized = query.lower().strip().removeprefix("/")
        visible = [command for command in self._commands.values() if not command.hidden]
        if normalized:
            visible = [
                command
                for command in visible
                if normalized in command.name.lower() or normalized in command.description.lower()
            ]
        return sorted(visible, key=lambda command: (-self._priority(command.source), command.name))

    async def execute(self, text: str, context: CommandContext) -> CommandResult:
        name, _, argument = text.strip().removeprefix("/").partition(" ")
        command = self.resolve(name)
        if command.availability:
            availability = command.availability(context)
            if not availability.available:
                raise CommandUnavailableError(availability.reason or f"命令 /{command.name} 当前不可用")
        if command.handler is None:
            return CommandResult()
        return await command.handler(argument.strip(), context)

    @staticmethod
    def _priority(source: CommandSource) -> int:
        return {"skill": 0, "extension": 1, "builtin": 2}[source]
