import pytest
from le_agent_ai import FauxProvider, Model
from le_agent_cli.app import AppBundle
from le_agent_cli.commands import CommandRegistry, CommandSpec
from le_agent_cli.permissions import PermissionController
from le_agent_cli.ui.app import LeAgentApp
from le_agent_cli.ui.composer import Composer
from le_agent_cli.ui.palette import CommandPalette
from le_agent_core import AgentLoopConfig
from le_agent_core.harness import AgentHarness
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
