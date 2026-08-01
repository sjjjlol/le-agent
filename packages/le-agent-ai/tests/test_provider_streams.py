from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from le_agent_ai import Model, ProviderContext, TextContent, UserMessage
from le_agent_ai.providers import AnthropicProvider, OpenAICompatibleProvider


class AsyncItems:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def __aiter__(self):
        async def iterate():
            for item in self.items:
                yield item

        return iterate()


def _context() -> ProviderContext:
    return ProviderContext(system_prompt="system", messages=[UserMessage(content=[TextContent(text="go")])])


@pytest.mark.asyncio
async def test_openai_stream_normalizes_usage_text_and_fragmented_tool_json(monkeypatch) -> None:
    chunks = [
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3), choices=[]),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content="hello",
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call-1",
                                function=SimpleNamespace(name="read", arguments='{"path":'),
                            )
                        ],
                    ),
                )
            ],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(name=None, arguments='"a.py"}'),
                            )
                        ],
                    ),
                )
            ],
        ),
    ]

    class Completions:
        async def create(self, **kwargs):
            assert kwargs["model"] == "gpt-test"
            assert kwargs["messages"][0]["role"] == "system"
            return AsyncItems(chunks)

    class Client:
        def __init__(self, **kwargs) -> None:
            assert kwargs["base_url"] == "https://example.test"
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr("le_agent_ai.providers.AsyncOpenAI", Client)
    model = Model(
        provider="openai",
        id="gpt-test",
        context_window=1000,
        max_output_tokens=100,
        base_url="https://example.test",
    )
    stream = await OpenAICompatibleProvider().stream(model, _context(), api_key="key")
    events = [event async for event in stream]
    result = await stream.result()

    assert result.text == "hello"
    assert result.stop_reason == "tool_use"
    assert result.usage.total_tokens == 15
    assert result.content[-1].arguments == {"path": "a.py"}  # type: ignore[union-attr]
    assert {event.type for event in events} >= {"start", "usage", "text_delta", "tool_call_delta", "done"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "code"),
    [(TimeoutError("timed out"), "timeout"), (asyncio.CancelledError(), "aborted")],
)
async def test_openai_stream_turns_failures_and_cancellation_into_terminal_messages(monkeypatch, raised, code) -> None:
    class Completions:
        async def create(self, **_kwargs):
            raise raised

    class Client:
        def __init__(self, **_kwargs) -> None:
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr("le_agent_ai.providers.AsyncOpenAI", Client)
    model = Model(provider="openai", id="gpt-test", context_window=1000, max_output_tokens=100)
    stream = await OpenAICompatibleProvider().stream(model, _context())
    events = [event async for event in stream]
    result = await stream.result()

    assert result.error_code == code
    assert result.stop_reason == ("aborted" if code == "aborted" else "error")
    assert events[-1].type == "error"


@pytest.mark.asyncio
async def test_anthropic_stream_normalizes_message_events_and_tool_input(monkeypatch) -> None:
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage=SimpleNamespace(input_tokens=20)),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="call-a", name="bash"),
        ),
        SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(text="working", partial_json=None)),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(text=None, partial_json='{"command":"pytest"}'),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="tool_use"),
            usage=SimpleNamespace(input_tokens=20, output_tokens=4),
        ),
    ]

    class Messages:
        async def create(self, **kwargs):
            assert kwargs["system"] == "system"
            return AsyncItems(events)

    class Client:
        def __init__(self, **_kwargs) -> None:
            self.messages = Messages()

    monkeypatch.setattr("le_agent_ai.providers.AsyncAnthropic", Client)
    model = Model(provider="anthropic", id="claude-test", context_window=1000, max_output_tokens=100)
    stream = await AnthropicProvider(base_url="https://anthropic.test").stream(model, _context())
    normalized = [event async for event in stream]
    result = await stream.result()

    assert result.text == "working"
    assert result.stop_reason == "tool_use"
    assert result.usage.total_tokens == 24
    assert result.content[-1].arguments == {"command": "pytest"}  # type: ignore[union-attr]
    assert any(event.type == "tool_call_delta" for event in normalized)


@pytest.mark.asyncio
async def test_anthropic_stream_normalizes_provider_error(monkeypatch) -> None:
    class Error(Exception):
        status_code = 429

    class Messages:
        async def create(self, **_kwargs):
            raise Error("rate limit")

    class Client:
        def __init__(self, **_kwargs) -> None:
            self.messages = Messages()

    monkeypatch.setattr("le_agent_ai.providers.AsyncAnthropic", Client)
    model = Model(provider="anthropic", id="claude-test", context_window=1000, max_output_tokens=100)
    stream = await AnthropicProvider().stream(model, _context())
    events = [event async for event in stream]
    result = await stream.result()

    assert result.error_code == "rate_limit"
    assert events[-1].error_code == "rate_limit"
