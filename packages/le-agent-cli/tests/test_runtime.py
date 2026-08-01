import pytest
from le_agent_ai import FauxProvider, Model, ScriptedResponse
from le_agent_cli.app import AppBundle
from le_agent_cli.permissions import PermissionController
from le_agent_cli.runtime import RuntimeController, RuntimeRequest
from le_agent_core import AgentLoopConfig
from le_agent_core.harness import AgentHarness
from le_agent_core.session import MemorySessionStore, SessionRepository


async def _bundle(name: str, response: str) -> AppBundle:
    session = await SessionRepository(MemorySessionStore()).create()
    provider = FauxProvider([ScriptedResponse.text(response)])
    model = Model(provider="faux", id=name, context_window=1000, max_output_tokens=100)
    harness = AgentHarness(session=session, config=AgentLoopConfig(model=model, provider=provider))
    return AppBundle(
        harness=harness,
        policy=PermissionController(),
        skills={},
        session=session,
        model_name=name,
    )


@pytest.mark.asyncio
async def test_runtime_controller_rebinds_subscribers_when_bundle_changes() -> None:
    initial = await _bundle("one", "first")
    requests: list[RuntimeRequest] = []

    async def factory(request: RuntimeRequest) -> AppBundle:
        requests.append(request)
        return await _bundle(request.model_name or "two", "second")

    controller = RuntimeController(initial, factory)
    texts: list[str] = []

    async def listener(event: object) -> None:
        message = getattr(event, "message", None)
        if getattr(event, "type", None) == "message_end" and getattr(message, "role", None) == "assistant":
            texts.append(message.text)

    controller.subscribe(listener)
    await controller.start()
    await controller.prompt("first prompt")
    await controller.switch_model("two")
    await controller.prompt("second prompt")

    assert texts == ["first", "second"]
    assert requests == [RuntimeRequest(model_name="two", resume=initial.session.id)]
    assert controller.bundle.model_name == "two"


@pytest.mark.asyncio
async def test_runtime_controller_new_and_resume_use_current_model() -> None:
    initial = await _bundle("one", "unused")
    requests: list[RuntimeRequest] = []

    async def factory(request: RuntimeRequest) -> AppBundle:
        requests.append(request)
        return await _bundle(request.model_name or "one", "unused")

    controller = RuntimeController(initial, factory)
    await controller.new_session()
    await controller.resume("saved-session")

    assert requests == [
        RuntimeRequest(model_name="one", resume=None),
        RuntimeRequest(model_name="one", resume="saved-session"),
    ]
