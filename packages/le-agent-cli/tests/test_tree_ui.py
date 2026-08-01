import pytest
from le_agent_ai import AssistantMessage, TextContent, ToolCallContent, ToolResultMessage, UserMessage
from le_agent_cli.ui.theme import APP_CSS
from le_agent_cli.ui.tree import SessionTreeModel, TreeFilter, TreeNavigator
from le_agent_core.session import MemorySessionStore, Session, SessionRepository
from rich.text import Text
from textual.app import App
from textual.widgets import OptionList, Static


@pytest.mark.asyncio
async def test_tree_model_prioritizes_current_path_and_formats_readable_messages() -> None:
    session = await SessionRepository(MemorySessionStore()).create()
    root = await session.append_message(UserMessage(content=[TextContent(text="请检查项目结构")]))
    old = await session.append_message(UserMessage(content=[TextContent(text="旧分支内容")]))
    await session.move_to(root)
    assistant = await session.append_message(
        AssistantMessage(
            content=[
                TextContent(text="我来读取配置"),
                ToolCallContent(id="read-1", name="read", arguments={"path": "pyproject.toml"}),
            ]
        )
    )
    current = await session.append_message(
        ToolResultMessage(
            tool_call_id="read-1",
            tool_name="read",
            content=[TextContent(text="[project]")],
        )
    )
    await session.append_label(assistant, "检查配置")

    model = await SessionTreeModel.from_session(session)
    rows = model.rows(TreeFilter.DEFAULT)

    assert rows[0].entry_id == root
    assert [row.entry_id for row in rows[1:4]] == [assistant, current, old]
    assert rows[0].summary == "user: 请检查项目结构"
    assert rows[1].summary == "assistant: 我来读取配置"
    assert rows[1].label == "检查配置"
    assert rows[2].summary == "[read: pyproject.toml]"
    assert rows[2].is_current
    assert "◆ current" in rows[2].display


@pytest.mark.asyncio
async def test_tree_model_supports_search_and_five_filters() -> None:
    session = await SessionRepository(MemorySessionStore()).create()
    root = await session.append_message(UserMessage(content=[TextContent(text="需求分析")]))
    tool = await session.append_message(
        ToolResultMessage(
            tool_call_id="bash-1",
            tool_name="bash",
            content=[TextContent(text="ok")],
            details={"command": "pytest"},
        )
    )
    await session.append_model_change("faux", "test")
    await session.append_label(root, "起点")

    model = await SessionTreeModel.from_session(session)

    assert set(TreeFilter) == {
        TreeFilter.DEFAULT,
        TreeFilter.NO_TOOLS,
        TreeFilter.USER_ONLY,
        TreeFilter.LABELED,
        TreeFilter.ALL,
    }
    assert [row.entry_id for row in model.rows(TreeFilter.USER_ONLY)] == [root]
    assert [row.entry_id for row in model.rows(TreeFilter.LABELED)] == [root]
    assert tool not in {row.entry_id for row in model.rows(TreeFilter.NO_TOOLS)}
    assert any(row.entry_id == tool for row in model.rows(TreeFilter.DEFAULT))
    assert len(model.rows(TreeFilter.ALL)) > len(model.rows(TreeFilter.DEFAULT))
    assert [row.entry_id for row in model.rows(TreeFilter.DEFAULT, query="需求")] == [root]


class TreeHostApp(App[None]):
    CSS = APP_CSS

    def __init__(self, session: Session, model: SessionTreeModel) -> None:
        super().__init__()
        self.session = session
        self.model = model

    def on_mount(self) -> None:
        self.push_screen(TreeNavigator(self.session, self.model))


@pytest.mark.asyncio
@pytest.mark.parametrize(("width", "preview_visible"), [(120, True), (80, True), (40, False)])
async def test_tree_navigator_filters_and_adapts_preview_to_terminal_width(
    width: int, preview_visible: bool
) -> None:
    session = await SessionRepository(MemorySessionStore()).create()
    await session.append_message(UserMessage(content=[TextContent(text="可搜索内容")]))
    model = await SessionTreeModel.from_session(session)
    app = TreeHostApp(session, model)

    async with app.run_test(size=(width, 24)) as pilot:
        screen = app.screen
        assert isinstance(screen, TreeNavigator)
        assert screen.query_one("#tree-preview", Static).display is preview_visible
        screen.query_one("#tree-options", OptionList).focus()
        await pilot.press("f")
        assert screen.tree_filter is TreeFilter.NO_TOOLS
        await pilot.click("#tree-search")
        await pilot.press(*"missing")
        assert not screen.query_one("#tree-options", OptionList).option_count


@pytest.mark.asyncio
async def test_tree_navigator_treats_tool_summaries_as_literal_text() -> None:
    session = await SessionRepository(MemorySessionStore()).create()
    command = 'grep -RIn --exclude-dir=.git -E "memory|Memory|memories|persist" .'
    await session.append_message(
        ToolResultMessage(
            tool_call_id="bash-markup",
            tool_name="bash",
            content=[TextContent(text="match")],
            details={"command": command},
        )
    )
    model = await SessionTreeModel.from_session(session)
    app = TreeHostApp(session, model)

    async with app.run_test(size=(100, 24)):
        options = app.screen.query_one("#tree-options", OptionList)
        prompt = options.get_option_at_index(0).prompt
        assert isinstance(prompt, Text)
        assert '[bash: grep -RIn --exclude-dir=.git -E "memory|Memory' in prompt.plain
