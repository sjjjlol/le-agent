"""Compact Claude-inspired Textual application backed by RuntimeController."""

from __future__ import annotations

from le_agent_ai.models import ToolCallContent
from le_agent_core.loop import AgentEvent
from pydantic import BaseModel
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from ..app import AppBundle
from ..commands import CommandContext, CommandError, CommandRegistry
from ..runtime import RuntimeController, RuntimeRequest
from .composer import Composer
from .palette import CommandPalette
from .status import StatusBar
from .theme import APP_CSS
from .transcript import Transcript


class ApprovalScreen(ModalScreen[str]):
    def __init__(self, tool_name: str, detail: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.detail = detail

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(f"允许执行 {self.tool_name}？\n{self.detail}"),
            Button("仅允许一次", id="allow_once", variant="success"),
            Button("本会话允许编辑", id="allow_session_edit"),
            Button("拒绝", id="deny", variant="error"),
            id="approval-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "deny")


class LeAgentApp(App[None]):
    CSS = APP_CSS
    BINDINGS = [
        ("ctrl+o", "toggle_tools", "展开工具"),
        ("ctrl+t", "show_tree", "会话树"),
        ("shift+tab", "cycle_permission", "权限模式"),
        ("escape", "abort", "中止"),
    ]

    def __init__(
        self,
        runtime: RuntimeController | AppBundle,
        *,
        registry: CommandRegistry | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        super().__init__()
        if isinstance(runtime, AppBundle):

            async def static_factory(_request: RuntimeRequest) -> AppBundle:
                raise RuntimeError("当前启动方式不支持切换运行时")

            runtime = RuntimeController(runtime, static_factory)
        self.runtime = runtime
        self.registry = registry or CommandRegistry()
        self.initial_prompt = initial_prompt
        self.show_tool_details = False
        self._queue: list[str] = []

    def compose(self) -> ComposeResult:
        yield Transcript(id="transcript")
        palette = CommandPalette(id="command-palette")
        palette.display = False
        yield palette
        yield StatusBar(id="status")
        yield Composer(id="composer")
        yield Static("/ 命令 · Enter steer · Alt+Enter follow-up · Shift+Enter 换行", id="hints")

    async def on_mount(self) -> None:
        self.runtime.subscribe(self._render_event)
        await self.runtime.start()
        self.runtime.bundle.policy.approval = self._approve
        self.query_one(Composer).focus()
        self._refresh_status()
        if self.initial_prompt:
            self.run_worker(self.runtime.prompt(self.initial_prompt), exclusive=False)

    async def _approve(self, call: ToolCallContent, args: BaseModel) -> str:
        return await self.push_screen_wait(ApprovalScreen(call.name, str(args.model_dump())))

    async def _render_event(self, event: AgentEvent) -> None:
        if event.type != "message_end" or event.message is None:
            return
        transcript = self.query_one(Transcript)
        if event.message.role == "user":
            text = "".join(block.text for block in event.message.content)
            await transcript.append_message(f"❯ {text}", "user")
        elif event.message.role == "assistant":
            await transcript.append_message(event.message.text, "assistant")
        self._refresh_status()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if not isinstance(event.text_area, Composer):
            return
        value = event.text_area.text.lstrip()
        palette = self.query_one(CommandPalette)
        if value.startswith("/") and " " not in value:
            palette.set_commands(self.registry.suggest(value[1:]))
        else:
            palette.display = False

    def on_composer_completion_moved(self, event: Composer.CompletionMoved) -> None:
        self.query_one(CommandPalette).move(event.direction)

    def on_composer_completion_requested(self, _event: Composer.CompletionRequested) -> None:
        palette = self.query_one(CommandPalette)
        selected = palette.selected_name()
        if selected:
            self.query_one(Composer).text = f"/{selected} "
        palette.display = False

    async def on_composer_submitted(self, event: Composer.Submitted) -> None:
        self.query_one(CommandPalette).display = False
        if event.text.startswith("/"):
            self.run_worker(self._execute_command(event.text), exclusive=False)
            return
        agent = self.runtime.bundle.harness.agent
        if agent and agent.state.is_streaming:
            if event.delivery == "follow_up":
                self.runtime.follow_up(event.text)
            else:
                self.runtime.steer(event.text)
            self._queue.append(event.text)
            self._refresh_status()
            return
        self.run_worker(self.runtime.prompt(event.text), exclusive=False)

    def on_composer_recall_requested(self, _event: Composer.RecallRequested) -> None:
        if self._queue:
            self.query_one(Composer).text = self._queue.pop()
            self._refresh_status()

    async def _execute_command(self, text: str) -> None:
        transcript = self.query_one(Transcript)
        try:
            result = await self.registry.execute(text, CommandContext(runtime=self.runtime))
            if result.message:
                await transcript.append_message(result.message, "system")
            if result.exit_requested:
                self.exit()
        except (CommandError, RuntimeError) as error:
            await transcript.append_message(str(error), "error")

    def _refresh_status(self) -> None:
        self.query_one(StatusBar).refresh_bundle(self.runtime.bundle, queue_size=len(self._queue))

    def action_abort(self) -> None:
        self.runtime.abort()

    def action_toggle_tools(self) -> None:
        self.show_tool_details = not self.show_tool_details

    def action_show_tree(self) -> None:
        self.run_worker(self._execute_command("/tree"), exclusive=False)

    def action_cycle_permission(self) -> None:
        modes = list(type(self.runtime.bundle.policy.mode))
        current = modes.index(self.runtime.bundle.policy.mode)
        self.runtime.bundle.policy.mode = modes[(current + 1) % len(modes)]
        self._refresh_status()
