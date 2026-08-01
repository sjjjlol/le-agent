import pytest
from le_agent_ai.faux import FauxProvider, ScriptedResponse
from le_agent_ai.models import Model, ProviderContext, TextContent, UserMessage


@pytest.mark.asyncio
async def test_faux_provider_streams_text_and_returns_canonical_message() -> None:
    provider = FauxProvider([ScriptedResponse.text("hello")])
    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    context = ProviderContext(system_prompt="be helpful", messages=[UserMessage(content=[TextContent(text="hi")])])

    stream = await provider.stream(model, context)
    events = [event async for event in stream]
    message = await stream.result()

    assert [event.type for event in events] == ["start", "text_delta", "done"]
    assert message.text == "hello"
    assert message.stop_reason == "stop"


@pytest.mark.asyncio
async def test_faux_provider_preserves_standardized_error_code() -> None:
    provider = FauxProvider(
        [ScriptedResponse(error_message="maximum context length exceeded", error_code="context_overflow")]
    )
    model = Model(provider="faux", id="scripted", context_window=1000, max_output_tokens=100)
    context = ProviderContext(system_prompt="", messages=[UserMessage(content=[TextContent(text="hi")])])

    stream = await provider.stream(model, context)
    events = [event async for event in stream]
    message = await stream.result()

    assert message.error_code == "context_overflow"
    assert next(event for event in events if event.type == "error").error_code == "context_overflow"
