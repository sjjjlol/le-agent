import pytest
from le_agent_ai import AssistantMessage, FauxProvider, Model, TextContent
from le_agent_ai.models import StreamEvent
from le_agent_cli.app import AppBundle
from le_agent_cli.commands import CommandRegistry, CommandSpec
from le_agent_cli.permissions import PermissionController
from le_agent_cli.ui.app import LeAgentApp
from le_agent_cli.ui.composer import Composer
from le_agent_cli.ui.palette import CommandPalette
from le_agent_cli.ui.transcript import AssistantMessageView, ToolCard, Transcript
from le_agent_core import AgentLoopConfig
from le_agent_core.harness import AgentHarness
from le_agent_core.loop import AgentEvent
from le_agent_core.session import MemorySessionStore, SessionRepository
from textual.app import App, ComposeResult


async def _bundle() -> AppBundle:
    session = await SessionRepository(MemorySessionStore()).create()
    model = Model(provider="faux", id="test", context_window=1000, max_output_tokens=100)
    harness = AgentHarness(session=session, config=AgentLoopConfig(model=model, provider=FauxProvider([])))
    return AppBundle(
        harness=harness,
        policy=PermissionController(),
        skills={},
        session=session,
        model_name="test",
    )


@pytest.mark.asyncio
async def test_typing_slash_opens_filtered_palette_and_tab_completes() -> None:
    registry = CommandRegistry()
    registry.register(CommandSpec(name="tree", description="浏览会话树"))
    registry.register(CommandSpec(name="model", description="选择模型"))
    app = LeAgentApp(await _bundle(), registry=registry)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click("#composer")
        await pilot.press("/")
        palette = app.query_one(CommandPalette)
        assert palette.display
        assert palette.option_count == 2

        await pilot.press("t", "r")
        assert palette.option_count == 1
        await pilot.press("tab")

        assert app.query_one(Composer).text == "/tree "
        assert not palette.display


@pytest.mark.asyncio
async def test_default_tui_registry_exposes_builtin_commands_on_slash() -> None:
    app = LeAgentApp(await _bundle())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.click("#composer")
        await pilot.press("/")

        names = {command.name for command in app.registry.suggest()}
        assert {"help", "model", "resume", "tree", "compact", "skill", "quit"} <= names
        assert app.query_one(CommandPalette).option_count == len(names)


class ComposerTestApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.submissions: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Composer(id="composer")

    def on_composer_submitted(self, event: Composer.Submitted) -> None:
        self.submissions.append((event.text, event.delivery))


@pytest.mark.asyncio
async def test_composer_distinguishes_steer_follow_up_and_newline() -> None:
    app = ComposerTestApp()

    async with app.run_test(size=(80, 12)) as pilot:
        composer = app.query_one(Composer)
        composer.focus()
        await pilot.press("a", "shift+enter", "b")
        assert composer.text == "a\nb"

        await pilot.press("enter")
        composer.text = "later"
        await pilot.press("alt+enter")

        assert app.submissions == [("a\nb", "steer"), ("later", "follow_up")]


@pytest.mark.asyncio
async def test_transcript_coalesces_stream_deltas_and_updates_tool_card_in_place() -> None:
    app = LeAgentApp(await _bundle())

    async with app.run_test(size=(100, 30)) as pilot:
        await app._render_event(AgentEvent(type="message_start"))
        await app._render_event(
            AgentEvent(type="message_update", assistant_event=StreamEvent(type="text_delta", delta="hel"))
        )
        await app._render_event(
            AgentEvent(type="message_update", assistant_event=StreamEvent(type="text_delta", delta="lo"))
        )
        await pilot.pause(0.05)

        assistant = app.query_one(AssistantMessageView)
        assert "hello" in assistant.renderable.plain

        await app._render_event(AgentEvent(type="tool_execution_start", tool_call_id="call-1", tool_name="bash"))
        await app._render_event(
            AgentEvent(
                type="tool_execution_update",
                tool_call_id="call-1",
                tool_name="bash",
                assistant_event="running",
            )
        )
        card = app.query_one(ToolCard)
        assert "running" in card.renderable.plain

        await app._render_event(
            AgentEvent(
                type="message_end",
                message=AssistantMessage(content=[TextContent(text="hello")], provider="faux", model="test"),
            )
        )
        assert len(app.query_one(Transcript).query(AssistantMessageView)) == 1
