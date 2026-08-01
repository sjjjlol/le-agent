"""Claude Code-inspired, event-driven Textual interface."""

from __future__ import annotations

from le_agent_ai.models import ToolCallContent
from le_agent_core.loop import AgentEvent
from pydantic import BaseModel
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from .app import AppBundle


class ApprovalScreen(ModalScreen[str]):
    def __init__(self, tool_name: str, detail: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.detail = detail

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(f"Allow {self.tool_name}?\n{self.detail}", id="approval-message"),
            Button("Allow once", id="allow_once", variant="success"),
            Button("Allow session edits", id="allow_session_edit"),
            Button("Deny", id="deny", variant="error"),
            id="approval-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "deny")


class LeAgentApp(App[None]):
    CSS = """
    #transcript { height: 1fr; border: solid $primary; padding: 1 2; }
    #composer { dock: bottom; }
    #status { dock: bottom; height: 1; color: $text-muted; }
    #approval-dialog { width: 68; height: auto; border: thick $warning; padding: 1 2; background: $surface; }
    """
    BINDINGS = [
        ("ctrl+o", "toggle_tools", "Toggle tool details"),
        ("ctrl+t", "show_tree", "Session tree"),
        ("shift+tab", "cycle_permission", "Permission mode"),
        ("escape", "abort", "Abort"),
    ]

    def __init__(self, bundle: AppBundle, initial_prompt: str | None = None) -> None:
        super().__init__()
        self.bundle = bundle
        self.initial_prompt = initial_prompt
        self.show_tool_details = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="transcript", markup=True, wrap=True)
        yield Static(self._status(), id="status")
        yield Input(placeholder="输入消息，或输入 / 查看命令…", id="composer")
        yield Footer()

    async def on_mount(self) -> None:
        agent = await self.bundle.harness.restore()
        agent.subscribe(self._render_event)
        self.bundle.policy.approval = self._approve
        self.query_one(Input).focus()
        if self.initial_prompt:
            self.run_worker(self._send(self.initial_prompt), exclusive=False)

    def _status(self) -> str:
        return f"{self.bundle.model_name} · {self.bundle.policy.mode} · session {self.bundle.session.id[:8]}"

    async def _approve(self, call: ToolCallContent, args: BaseModel) -> str:
        return await self.push_screen_wait(ApprovalScreen(call.name, str(args.model_dump())))

    async def _render_event(self, event: AgentEvent) -> None:
        log = self.query_one(RichLog)
        if event.type == "message_end" and event.message is not None:
            role = event.message.role.upper()
            text = getattr(event.message, "text", getattr(event.message, "content", ""))
            log.write(f"[bold cyan]{role}[/] {text}")
        elif event.type == "tool_execution_start":
            log.write(f"[yellow]● {event.tool_name}[/] running")
        elif event.type == "tool_execution_end" and event.tool_result:
            output = event.tool_result.content[0].text
            log.write(f"[green]✓ {event.tool_name}[/] {output if self.show_tool_details else output[:160]}")
        self.query_one("#status", Static).update(self._status())

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        if text.startswith("/"):
            await self._command(text)
        else:
            self.run_worker(self._send(text), exclusive=False)

    async def _send(self, text: str) -> None:
        try:
            await self.bundle.harness.prompt(text)
        except Exception as error:
            self.query_one(RichLog).write(f"[bold red]Error:[/] {error}")

    async def _command(self, command: str) -> None:
        log = self.query_one(RichLog)
        name, _, argument = command.partition(" ")
        if name == "/quit":
            self.exit()
        elif name == "/help":
            log.write("/help /model /new /resume /tree /compact /skills /status /clear /quit")
        elif name == "/skills":
            text = "\n".join(f"{skill.name}: {skill.description}" for skill in self.bundle.skills.values())
            log.write(text or "No skills found")
        elif name == "/status":
            log.write(self._status())
        elif name == "/tree":
            entries = await self.bundle.session.entries()
            text = "\n".join(f"{entry.id} {entry.type} parent={entry.parent_id}" for entry in entries)
            log.write(text or "Empty session")
        elif name == "/compact":
            try:
                changed = await self.bundle.harness.compact()
                log.write("Compaction completed" if changed else "Nothing to compact")
            except Exception as error:
                log.write(f"[red]Compaction failed:[/] {error}")
        elif name == "/model":
            log.write(f"Current model: {self.bundle.model_name}. Restart with --model to switch.")
        elif name in {"/new", "/clear", "/resume"}:
            log.write(
                f"{name} is available on startup through --new/--resume; "
                "session switching in-place is intentionally deferred."
            )
        else:
            log.write(f"Unknown command: {name}")

    def action_abort(self) -> None:
        if self.bundle.harness.agent:
            self.bundle.harness.agent.abort()

    def action_toggle_tools(self) -> None:
        self.show_tool_details = not self.show_tool_details

    def action_show_tree(self) -> None:
        self.run_worker(self._command("/tree"), exclusive=False)

    def action_cycle_permission(self) -> None:
        modes = list(type(self.bundle.policy.mode))
        next_index = (modes.index(self.bundle.policy.mode) + 1) % len(modes)
        self.bundle.policy.mode = modes[next_index]
        self.query_one("#status", Static).update(self._status())
