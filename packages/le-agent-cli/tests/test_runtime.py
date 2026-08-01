import asyncio

import pytest
from le_agent_ai import FauxProvider, Model, ScriptedResponse
from le_agent_cli.app import AppBundle
from le_agent_cli.permissions import PermissionController
from le_agent_cli.runtime import RuntimeController, RuntimeRequest
from le_agent_core import AgentLoopConfig
from le_agent_core.harness import AgentHarness
from le_agent_core.session import MemorySessionStore, SessionRepository


async def _bundle(name: str, response: str, session=None) -> AppBundle:
    session = session or await SessionRepository(MemorySessionStore()).create()
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
    initial.policy.mode = type(initial.policy.mode).TRUST

    async def approve(_call, _args):
        return "allow_once"

    initial.policy.approval = approve
    requests: list[RuntimeRequest] = []

    async def factory(request: RuntimeRequest) -> AppBundle:
        requests.append(request)
        return await _bundle(request.model_name or "two", "second", request.session)

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
    assert requests == [RuntimeRequest(model_name="two", session=initial.session)]
    assert controller.bundle.model_name == "two"
    assert controller.bundle.session is initial.session
    assert controller.bundle.policy.mode is type(controller.bundle.policy.mode).TRUST
    assert controller.bundle.policy.approval is approve
    assert "model_change" in {entry.type for entry in await controller.bundle.session.entries()}


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


@pytest.mark.asyncio
async def test_runtime_replacement_and_close_release_owned_sessions() -> None:
    initial = await _bundle("one", "unused")
    replacement = await _bundle("one", "unused")

    async def factory(_request: RuntimeRequest) -> AppBundle:
        return replacement

    controller = RuntimeController(initial, factory)
    await controller.new_session()

    with pytest.raises(RuntimeError, match="session is closed"):
        await initial.session.append_session_info("closed")

    await controller.close()
    with pytest.raises(RuntimeError, match="session is closed"):
        await replacement.session.append_session_info("closed")


@pytest.mark.asyncio
async def test_resuming_current_session_is_an_idle_noop() -> None:
    initial = await _bundle("one", "unused")
    requests: list[RuntimeRequest] = []

    async def factory(request: RuntimeRequest) -> AppBundle:
        requests.append(request)
        return await _bundle("one", "unused")

    controller = RuntimeController(initial, factory)
    await controller.resume(initial.session.id)

    assert requests == []
    assert controller.bundle is initial
    await controller.close()


@pytest.mark.asyncio
async def test_name_session_updates_persistence_and_header_state() -> None:
    initial = await _bundle("one", "unused")

    async def factory(_request: RuntimeRequest) -> AppBundle:
        return initial

    controller = RuntimeController(initial, factory)
    await controller.name_session("Demo")

    assert controller.bundle.session_name == "Demo"
    entries = await controller.bundle.session.entries()
    assert entries[-1].type == "session_info"
    assert entries[-1].payload["name"] == "Demo"
    await controller.close()


@pytest.mark.asyncio
async def test_state_change_lock_cannot_overlap_a_new_prompt() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    acquired = asyncio.Event()
    session = await SessionRepository(MemorySessionStore()).create()

    class Agent:
        def subscribe(self, _listener):
            return lambda: None

        async def wait_for_idle(self) -> None:
            return None

    class Harness:
        agent = None

        async def restore(self):
            self.agent = Agent()
            return self.agent

        async def prompt(self, _text: str) -> None:
            started.set()
            await release.wait()

    bundle = AppBundle(Harness(), PermissionController(), {}, session, "test")  # type: ignore[arg-type]

    async def factory(_request: RuntimeRequest) -> AppBundle:
        return bundle

    controller = RuntimeController(bundle, factory)
    prompt_task = asyncio.create_task(controller.prompt("running"))
    await started.wait()

    async def mutate() -> None:
        async with controller.state_change():
            acquired.set()

    mutation_task = asyncio.create_task(mutate())
    await asyncio.sleep(0)
    assert not acquired.is_set()
    release.set()
    await prompt_task
    await mutation_task
    assert acquired.is_set()
    await controller.close()
