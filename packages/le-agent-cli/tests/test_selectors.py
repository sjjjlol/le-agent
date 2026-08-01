import pytest
from le_agent_cli.app import ModelOption
from le_agent_cli.ui.selectors import ModelSelector, SearchableSelector, SelectorItem
from textual.app import App
from textual.widgets import Input, OptionList


@pytest.mark.asyncio
async def test_searchable_selector_filters_and_returns_selected_value() -> None:
    app = App[None]()
    selector = SearchableSelector(
        "选择模型",
        [
            SelectorItem("claude-sonnet", "Claude Sonnet", "Anthropic"),
            SelectorItem("gpt-5", "GPT-5", "OpenAI"),
        ],
    )

    async with app.run_test(size=(80, 20)) as pilot:
        app.push_screen(selector)
        await pilot.pause()
        search = selector.query_one("#selector-search", Input)
        search.focus()
        await pilot.press(*"sonnet")
        options = selector.query_one("#selector-options", OptionList)
        assert options.option_count == 1
        assert options.get_option_at_index(0).id == "claude-sonnet"


@pytest.mark.asyncio
async def test_model_selector_has_no_search_input_and_uses_arrows_and_enter() -> None:
    app = App[None]()
    selected: list[str | None] = []
    selector = ModelSelector(
        [
            ModelOption("terra", "openai", "gpt-5.6-terra"),
            ModelOption("mini", "openai", "gpt-5.4-mini"),
        ],
        current="mini",
    )

    async with app.run_test(size=(80, 20)) as pilot:
        app.push_screen(selector, callback=selected.append)
        await pilot.pause()
        assert len(selector.query(Input)) == 0
        options = selector.query_one("#model-selector-options", OptionList)
        assert options.get_option_at_index(0).id == "mini"
        await pilot.press("down", "enter")
        await pilot.pause()

    assert selected == ["terra"]
