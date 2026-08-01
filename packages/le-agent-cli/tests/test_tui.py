from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from le_agent_ai import AssistantMessage, FauxProvider, Model, TextContent, Usage, UserMessage
from le_agent_ai.models import StreamEvent
from le_agent_cli.app import AppBundle
from le_agent_cli.commands import CommandRegistry, CommandSpec
from le_agent_cli.permissions import PermissionController
from le_agent_cli.ui.app import LeAgentApp
from le_agent_cli.ui.composer import Composer
from le_agent_cli.ui.header import BrandHeader
from le_agent_cli.ui.palette import CommandPalette
from le_agent_cli.ui.status import ContextUsage, StatusBar, context_usage
from le_agent_cli.ui.transcript import AssistantMessageView, ToolCard, Transcript, WaitingMessage
from le_agent_core import AgentLoopConfig
from le_agent_core.harness import AgentHarness
from le_agent_core.loop import AgentEvent
from le_agent_core.session import MemorySessionStore, SessionRepository
from rich.cells import cell_len
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


@pytest.mark.asyncio
async def test_palette_fuzzy_enter_completion_and_escape_dismissal() -> None:
    app = LeAgentApp(await _bundle())

    async with app.run_test(size=(100, 30)) as pilot:
        composer = app.query_one(Composer)
        composer.focus()
        await pilot.press(*"/mdl")
        palette = app.query_one(CommandPalette)
        assert palette.option_count == 1

        await pilot.press("enter")
        assert composer.text == "/model "
        await pilot.press("escape")
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


@pytest.mark.asyncio
async def test_composer_submits_slash_command_with_arguments() -> None:
    app = ComposerTestApp()

    async with app.run_test(size=(80, 12)) as pilot:
        composer = app.query_one(Composer)
        composer.focus()
        await pilot.press(*"/name test01", "enter")

        assert app.submissions == [("/name test01", "steer")]
        assert composer.text == ""


@pytest.mark.asyncio
async def test_name_command_from_composer_updates_session_and_header() -> None:
    app = LeAgentApp(await _bundle())

    async with app.run_test(size=(80, 24)) as pilot:
        composer = app.query_one(Composer)
        composer.focus()
        await pilot.press(*"/name test01", "enter")
        await pilot.pause(0.1)

        assert app.runtime.bundle.session_name == "test01"
        assert "test01" in app.query_one(BrandHeader).renderable.plain
        assert any(
            "会话已命名：test01" in str(widget.render())
            for widget in app.query(".system-message")
        )


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
        await pilot.pause(0.1)

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


@pytest.mark.asyncio
async def test_request_waiting_starts_before_provider_output_and_stops_on_first_delta() -> None:
    app = LeAgentApp(await _bundle())

    async with app.run_test(size=(80, 24)) as pilot:
        await app._render_event(AgentEvent(type="assistant_request_start"))
        await pilot.pause(0.05)
        waiting = app.query_one(WaitingMessage)
        assert "正在等待" in waiting.renderable.plain
        assert "test" not in waiting.renderable.plain
        assert "Esc 中止" in waiting.renderable.plain

        await app._render_event(
            AgentEvent(type="message_update", assistant_event=StreamEvent(type="text_delta", delta="hello"))
        )
        await pilot.pause(0.05)
        assert len(app.query(WaitingMessage)) == 0


@pytest.mark.asyncio
async def test_provider_error_is_readable_and_always_stops_waiting() -> None:
    app = LeAgentApp(await _bundle())

    async with app.run_test(size=(80, 24)) as pilot:
        await app._render_event(AgentEvent(type="assistant_request_start"))
        await app._render_event(
            AgentEvent(
                type="message_update",
                assistant_event=StreamEvent(type="error", error_message="缺少 OPENAI_API_KEY"),
            )
        )
        await app._render_event(
            AgentEvent(
                type="message_end",
                message=AssistantMessage(
                    content=[],
                    provider="openai",
                    model="gpt",
                    stop_reason="error",
                    error_message="缺少 OPENAI_API_KEY",
                ),
            )
        )
        await pilot.pause(0.05)

        assert len(app.query(WaitingMessage)) == 0
        assert "缺少 OPENAI_API_KEY" in app.query_one(AssistantMessageView).renderable.plain


@pytest.mark.asyncio
async def test_missing_api_key_and_worker_exception_are_visible() -> None:
    bundle = await _bundle()
    bundle.api_key_env = "OPENAI_API_KEY"
    bundle.api_key_available = False
    app = LeAgentApp(bundle)

    async with app.run_test(size=(80, 24)):
        assert sum(
            "OPENAI_API_KEY" in str(widget.render()) for widget in app.query(".error-message")
        ) == 1

        async def fail(_text: str) -> None:
            raise RuntimeError("provider worker crashed")

        app.runtime.prompt = fail  # type: ignore[method-assign]
        await app._run_prompt("hi")
        assert any(
            "provider worker crashed" in str(widget.render()) for widget in app.query(".error-message")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("模型名称不明确"), KeyError("会话不存在")])
async def test_command_configuration_errors_are_rendered_in_transcript(error: Exception) -> None:
    registry = CommandRegistry()

    async def fail(_argument, _context):
        raise error

    registry.register(CommandSpec("fail", "fail", handler=fail))
    app = LeAgentApp(await _bundle(), registry=registry)

    async with app.run_test(size=(80, 24)):
        await app._execute_command("/fail")
        assert any(str(error) in str(widget.render()) for widget in app.query(".error-message"))


@pytest.mark.asyncio
async def test_command_mutations_refresh_status_bar_immediately() -> None:
    registry = CommandRegistry()

    async def mutate(_argument, context):
        from le_agent_cli.commands import CommandResult

        context.runtime.bundle.model_name = "updated-model"
        modes = list(type(context.runtime.bundle.policy.mode))
        context.runtime.bundle.policy.mode = modes[-1]
        return CommandResult()

    registry.register(CommandSpec("mutate", "mutate runtime", handler=mutate))
    app = LeAgentApp(await _bundle(), registry=registry)

    async with app.run_test(size=(100, 24)):
        status = app.query_one("#status", StatusBar)
        assert "updated-model" not in status.content

        await app._execute_command("/mutate")

        assert "updated-model" in status.content
        assert app.runtime.bundle.policy.mode.value in status.content


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [40, 80, 120])
async def test_compact_logo_header_is_visible_at_supported_widths(width: int) -> None:
    app = LeAgentApp(await _bundle())

    async with app.run_test(size=(width, 24)):
        header = app.query_one(BrandHeader)
        assert "le-agent" in header.renderable.plain
        assert header.size.height == (1 if width == 40 else 2)


@pytest.mark.asyncio
async def test_narrow_header_truncates_long_session_name() -> None:
    bundle = await _bundle()
    bundle.session_name = "这是一个非常非常长的会话名称用于窄终端测试"
    app = LeAgentApp(bundle)

    async with app.run_test(size=(40, 24)):
        header = app.query_one(BrandHeader)
        assert header.size.height == 1
        assert cell_len(header.renderable.plain) <= 40
        assert header.renderable.plain.endswith("…")


@pytest.mark.asyncio
async def test_switching_to_provider_without_key_shows_warning_once() -> None:
    registry = CommandRegistry()

    async def switch(_argument, context):
        from le_agent_cli.commands import CommandResult

        context.runtime.bundle.api_key_env = "ANTHROPIC_API_KEY"
        context.runtime.bundle.api_key_available = False
        return CommandResult()

    registry.register(CommandSpec("switch", "switch", handler=switch))
    app = LeAgentApp(await _bundle(), registry=registry)

    async with app.run_test(size=(80, 24)):
        await app._execute_command("/switch")
        await app._execute_command("/switch")
        assert sum(
            "ANTHROPIC_API_KEY" in str(widget.render()) for widget in app.query(".error-message")
        ) == 1


@pytest.mark.asyncio
async def test_clear_only_removes_transcript_without_changing_session_context() -> None:
    bundle = await _bundle()
    await bundle.session.append_message(UserMessage(content=[TextContent(text="kept context")]))
    app = LeAgentApp(bundle)

    async with app.run_test(size=(80, 24)) as pilot:
        transcript = app.query_one(Transcript)
        assert any("kept context" in str(widget.render()) for widget in transcript.query(".user-message"))
        session_id = app.runtime.bundle.session.id
        leaf_id = app.runtime.bundle.session.leaf_id
        entries = await app.runtime.bundle.session.entries()

        await app._execute_command("/clear")
        await pilot.pause()

        assert len(transcript.query(".message")) == 0
        assert app.runtime.bundle.session.id == session_id
        assert app.runtime.bundle.session.leaf_id == leaf_id
        assert await app.runtime.bundle.session.entries() == entries
        assert [message.role for message in await app.runtime.bundle.session.build_context_messages()] == ["user"]


@pytest.mark.asyncio
async def test_command_transcript_effects_clear_or_reload_projected_context() -> None:
    bundle = await _bundle()
    await bundle.session.append_message(UserMessage(content=[TextContent(text="restored branch")]))
    registry = CommandRegistry()

    async def clear_command(_argument, _context):
        from le_agent_cli.commands import CommandResult

        return CommandResult(transcript_effect="clear")

    async def reload_command(_argument, _context):
        from le_agent_cli.commands import CommandResult

        return CommandResult(transcript_effect="reload")

    registry.register(CommandSpec("clear-test", "clear", handler=clear_command))
    registry.register(CommandSpec("reload-test", "reload", handler=reload_command))
    app = LeAgentApp(bundle, registry=registry)

    async with app.run_test(size=(80, 24)) as pilot:
        transcript = app.query_one(Transcript)
        await transcript.append_message("temporary", "system")
        await app._execute_command("/clear-test")
        assert len(transcript.query(".message")) == 0

        await app._execute_command("/reload-test")
        await pilot.pause()
        assert any("restored branch" in str(widget.render()) for widget in transcript.query(".user-message"))


@pytest.mark.asyncio
async def test_status_context_uses_latest_provider_usage_plus_trailing_messages() -> None:
    bundle = await _bundle()
    agent = await bundle.harness.restore()
    agent.state.messages.extend(
        [
            AssistantMessage(content=[], usage=Usage(input_tokens=600, output_tokens=100)),
            UserMessage(content=[TextContent(text="next")]),
        ]
    )

    usage = context_usage(bundle)

    assert usage == ContextUsage(used_tokens=701, context_window=1000, percent=70)


@pytest.mark.asyncio
async def test_session_command_renders_clickable_file_link(tmp_path) -> None:
    bundle = await _bundle()
    bundle.session_file = tmp_path / "demo.jsonl"
    app = LeAgentApp(bundle)

    async with app.run_test(size=(80, 24)):
        await app._execute_command("/session")
        row = list(app.query(".system-message"))[-1]
        rendered = row.render()
        assert str(bundle.session_file) in rendered.plain
        assert any(span.style.link == bundle.session_file.resolve().as_uri() for span in rendered.spans)


@pytest.mark.asyncio
async def test_tree_command_waits_for_idle_before_opening_navigator(monkeypatch) -> None:
    app = LeAgentApp(await _bundle())
    order: list[str] = []

    @asynccontextmanager
    async def state_change():
        order.append("idle")
        yield SimpleNamespace(reload_context=False)

    async def push_screen(_screen):
        order.append("screen")
        return None

    monkeypatch.setattr(app.runtime, "state_change", state_change)
    monkeypatch.setattr(app, "push_screen_wait", push_screen)

    assert await app._show_tree() == "已取消会话回溯"
    assert order == ["idle", "screen"]
    await app.runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [40, 80, 120])
async def test_main_tui_layout_and_session_command_work_at_required_widths(width: int) -> None:
    app = LeAgentApp(await _bundle())

    async with app.run_test(size=(width, 24)) as pilot:
        assert app.query_one(Transcript).display
        assert app.query_one(Composer).display
        await pilot.click("#composer")
        await pilot.press(*"/session", "enter")
        await pilot.pause()
        assert any("会话：" in str(widget.render()) for widget in app.query(".system-message"))

        previous = app.runtime.bundle.policy.mode
        app.action_cycle_permission()
        assert app.runtime.bundle.policy.mode is not previous
