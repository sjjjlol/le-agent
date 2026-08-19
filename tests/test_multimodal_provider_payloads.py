from le_agent import ImageContent, TextContent, ToolResultMessage
from le_agent_ai.mistral import _build_mistral_payload
from le_agent_ai.openai_codex import _build_codex_payload
from le_agent_ai.openai_compatible import _build_chat_payload, _build_responses_payload


def _image_result() -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id="call-1",
        tool_name="read",
        content=[
            TextContent(text="Read image file [image/png]"),
            ImageContent(data="aW1hZ2U=", mime_type="image/png"),
        ],
    )


def test_openai_responses_embeds_image_in_function_output() -> None:
    payload = _build_responses_payload(
        model="gpt",
        system="system",
        messages=[_image_result()],
        tools=[],
        supports_images=True,
    )

    result = payload["input"][0]
    assert result["output"][1] == {
        "type": "input_image",
        "detail": "auto",
        "image_url": "data:image/png;base64,aW1hZ2U=",
    }


def test_codex_embeds_image_in_function_output() -> None:
    payload = _build_codex_payload(
        model="codex",
        system="system",
        messages=[_image_result()],
        tools=[],
        supports_images=True,
    )

    result = payload["input"][0]
    assert result["output"][1]["type"] == "input_image"


def test_openai_chat_attaches_tool_images_in_followup_user_message() -> None:
    payload = _build_chat_payload(
        model="vision",
        system="system",
        messages=[_image_result()],
        tools=[],
        supports_images=True,
    )

    assert [message["role"] for message in payload["messages"]] == ["system", "tool", "user"]
    assert payload["messages"][2]["content"][1]["type"] == "image_url"


def test_mistral_attaches_tool_images_in_followup_user_message() -> None:
    payload = _build_mistral_payload(
        model="pixtral",
        system="system",
        messages=[_image_result()],
        tools=[],
        reasoning_effort=None,
        max_tokens=None,
        supports_images=True,
    )

    assert [message["role"] for message in payload["messages"]] == ["system", "tool", "user"]
    assert payload["messages"][2]["content"][1]["type"] == "image_url"


def test_non_vision_models_receive_placeholder_instead_of_image() -> None:
    payload = _build_responses_payload(
        model="text-only",
        system="system",
        messages=[_image_result()],
        tools=[],
        supports_images=False,
    )

    result = payload["input"][0]
    assert "image contents are unavailable—do not infer or describe them" in result["output"]
    assert "switch to a vision-capable model" in result["output"]
    assert "aW1hZ2U=" not in str(payload)
