"""Metadata-driven slash command registration and dispatch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from .context import context_usage, format_context_usage

CommandSource = Literal["builtin", "skill", "extension"]
TranscriptEffect = Literal["preserve", "clear", "reload"]


@dataclass(frozen=True, slots=True)
class CommandAvailability:
    available: bool = True
    reason: str | None = None


@dataclass(slots=True)
class CommandContext:
    runtime: Any = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandLink:
    label: str
    target: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    message: str | None = None
    exit_requested: bool = False
    transcript_effect: TranscriptEffect = "preserve"
    links: tuple[CommandLink, ...] = ()


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
        scored: list[tuple[int, CommandSpec]] = []
        for command in visible:
            if not normalized:
                scored.append((0, command))
                continue
            candidates = [command.name, *command.aliases, command.description]
            scores = [score for value in candidates if (score := self._fuzzy_score(normalized, value)) is not None]
            if scores:
                scored.append((min(scores), command))
        return [
            command
            for _, command in sorted(
                scored,
                key=lambda item: (-self._priority(item[1].source), item[0], item[1].name),
            )
        ]

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

    @staticmethod
    def _fuzzy_score(query: str, value: str) -> int | None:
        target = value.casefold()
        if query in target:
            return target.index(query)
        position = -1
        gaps = 0
        for character in query:
            found = target.find(character, position + 1)
            if found < 0:
                return None
            gaps += found - position - 1
            position = found
        return 100 + gaps


def _persistent_session(context: CommandContext) -> CommandAvailability:
    if getattr(context.runtime.bundle, "persistent_session", True):
        return CommandAvailability()
    return CommandAvailability(False, "--no-session 模式未启用持久化会话，无法使用此命令")


async def _invoke_ui(context: CommandContext, name: str, *args: Any) -> Any:
    callback = context.data.get(name)
    if callback is None:
        raise CommandError(f"当前界面不支持 {name}")
    return await callback(*args)


def create_builtin_registry() -> CommandRegistry:
    """Create the single command catalog shared by completion and execution."""
    registry = CommandRegistry()

    async def help_command(_argument: str, _context: CommandContext) -> CommandResult:
        lines = [f"/{command.name}  {command.description}" for command in registry.suggest()]
        return CommandResult(message="可用命令\n" + "\n".join(lines))

    async def model_command(argument: str, context: CommandContext) -> CommandResult:
        selected = argument or await _invoke_ui(
            context,
            "select_model",
            tuple(getattr(context.runtime.bundle, "model_names", ())),
            context.runtime.bundle.model_name,
        )
        if not selected:
            return CommandResult(message="已取消模型切换")
        await context.runtime.switch_model(str(selected))
        return CommandResult(message=f"已切换模型：{selected}")

    async def settings_command(_argument: str, context: CommandContext) -> CommandResult:
        selected = await _invoke_ui(context, "select_setting", context.runtime.bundle.policy.mode.value)
        if not selected:
            return CommandResult(message="已取消设置修改")
        mode_type = type(context.runtime.bundle.policy.mode)
        context.runtime.bundle.policy.mode = mode_type(str(selected))
        return CommandResult(message=f"权限模式：{selected}")

    async def new_command(_argument: str, context: CommandContext) -> CommandResult:
        await context.runtime.new_session()
        return CommandResult(message="已创建新会话", transcript_effect="clear")

    async def clear_command(_argument: str, _context: CommandContext) -> CommandResult:
        return CommandResult(transcript_effect="clear")

    async def resume_command(argument: str, context: CommandContext) -> CommandResult:
        selected = argument or await _invoke_ui(context, "select_session")
        if not selected:
            return CommandResult(message="已取消恢复会话")
        await context.runtime.resume(str(selected))
        return CommandResult(message=f"已恢复会话：{selected}", transcript_effect="reload")

    async def tree_command(_argument: str, context: CommandContext) -> CommandResult:
        result = await _invoke_ui(context, "show_tree")
        if isinstance(result, CommandResult):
            return result
        effect: TranscriptEffect = "preserve" if str(result).startswith("已取消") else "reload"
        return CommandResult(message=str(result), transcript_effect=effect)

    async def compact_command(argument: str, context: CommandContext) -> CommandResult:
        changed = await context.runtime.compact(argument or None)
        return CommandResult(
            message="上下文压缩完成" if changed else "当前上下文无需压缩",
            transcript_effect="reload" if changed else "preserve",
        )

    async def skills_command(_argument: str, context: CommandContext) -> CommandResult:
        skills = context.runtime.bundle.skills
        if not skills:
            return CommandResult(message="未发现可用 Skill")
        lines = [f"{skill.name}  {skill.description}" for skill in skills.values()]
        return CommandResult(message="可用 Skills\n" + "\n".join(lines))

    async def skill_command(argument: str, context: CommandContext) -> CommandResult:
        name, _, arguments = argument.partition(" ")
        if not name:
            raise CommandError("用法：/skill <name> [args]")
        skill = context.runtime.bundle.skills.get(name)
        if skill is None:
            raise CommandError(f"未知 Skill：{name}")
        prompt = f"使用 Skill `{skill.name}` 完成请求。\n\n{skill.body}"
        if arguments.strip():
            prompt += f"\n\n用户参数：{arguments.strip()}"
        await context.runtime.prompt(prompt)
        return CommandResult()

    async def session_command(_argument: str, context: CommandContext) -> CommandResult:
        bundle = context.runtime.bundle
        entries = await bundle.session.entries()
        persistence = "持久化" if bundle.persistent_session else "仅内存"
        session_file = getattr(bundle, "session_file", None)
        if session_file is None:
            file_line = "文件：仅内存，无 JSONL 文件"
            links: tuple[CommandLink, ...] = ()
        else:
            path = session_file.resolve()
            file_line = f"文件：{path}"
            links = (CommandLink("打开 JSONL 文件", path.as_uri()),)
        return CommandResult(
            message=(
                f"会话：{bundle.session.id}\n模型：{bundle.model_name}\n"
                f"模式：{persistence}\n记录：{len(entries)} entries\n{file_line}"
            ),
            links=links,
        )

    async def status_command(_argument: str, context: CommandContext) -> CommandResult:
        return CommandResult(message=format_context_usage(context_usage(context.runtime.bundle)))

    async def name_command(argument: str, context: CommandContext) -> CommandResult:
        if not argument:
            raise CommandError("用法：/name <会话名称>")
        await context.runtime.bundle.session.append_session_info(argument)
        return CommandResult(message=f"会话已命名：{argument}")

    async def hotkeys_command(_argument: str, _context: CommandContext) -> CommandResult:
        return CommandResult(
            message=(
                "Enter  steering · Alt+Enter  follow-up · Shift+Enter  换行\n"
                "Escape  中止/取消 · Alt+Up  取回队列 · Ctrl+O  展开工具 · Shift+Tab  切换权限"
            )
        )

    async def quit_command(_argument: str, _context: CommandContext) -> CommandResult:
        return CommandResult(exit_requested=True)

    specs = [
        CommandSpec("help", "显示命令帮助", handler=help_command),
        CommandSpec("model", "选择或切换模型", "[name]", handler=model_command),
        CommandSpec("settings", "修改运行设置", handler=settings_command),
        CommandSpec("new", "创建新会话", handler=new_command),
        CommandSpec("clear", "清空当前界面", handler=clear_command),
        CommandSpec("resume", "恢复历史会话", "[session-id]", availability=_persistent_session, handler=resume_command),
        CommandSpec("tree", "浏览并回溯会话树", availability=_persistent_session, handler=tree_command),
        CommandSpec("compact", "压缩当前上下文", "[instructions]", handler=compact_command),
        CommandSpec("skills", "列出可用 Skills", handler=skills_command),
        CommandSpec("skill", "调用指定 Skill", "<name> [args]", handler=skill_command),
        CommandSpec("session", "显示会话与存储信息", handler=session_command),
        CommandSpec("status", "显示上下文占用", handler=status_command),
        CommandSpec("name", "设置会话名称", "<name>", availability=_persistent_session, handler=name_command),
        CommandSpec("hotkeys", "显示快捷键", handler=hotkeys_command),
        CommandSpec("quit", "退出 le-agent", handler=quit_command),
    ]
    for spec in specs:
        registry.register(spec)
    return registry
