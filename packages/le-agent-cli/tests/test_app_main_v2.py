from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from le_agent_ai import FauxProvider, Model, ScriptedResponse, TextContent, UserMessage
from le_agent_cli import app as app_module
from le_agent_cli import main as main_module
from le_agent_cli.app import AppBundle
from le_agent_cli.config import AppConfig, ProviderConfig
from le_agent_cli.permissions import PermissionController
from le_agent_core import AgentLoopConfig
from le_agent_core.compaction import SummaryRequest
from le_agent_core.harness import AgentHarness
from le_agent_core.loop import AgentEvent
from le_agent_core.session import MemorySessionStore, SessionRepository


async def _bundle(response: str = "done") -> AppBundle:
    session = await SessionRepository(MemorySessionStore()).create()
    model = Model(provider="faux", id="test", context_window=1000, max_output_tokens=100)
    harness = AgentHarness(
        session=session,
        config=AgentLoopConfig(model=model, provider=FauxProvider([ScriptedResponse.text(response)])),
    )
    return AppBundle(harness, PermissionController(), {}, session, "test", ("test",), persistent_session=False)


def test_session_discovery_reads_latest_name_and_ignores_corrupt_lines(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_session_root", lambda _workspace: tmp_path)
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(
        '\n'.join([
            '{"type":"session","version":2,"id":"first","created_at":1}',
            '{"type":"session_info","id":"n","parent_id":null,"timestamp":1,"name":"Demo"}',
        ]),
        encoding="utf-8",
    )
    second.write_text("broken", encoding="utf-8")
    second.touch()

    sessions = app_module.list_sessions(tmp_path)

    assert {item.identifier for item in sessions} == {"first", "second"}
    assert next(item for item in sessions if item.identifier == "first").name == "Demo"
    assert app_module.latest_session_id(tmp_path) == "second"


def test_provider_factory_supports_both_configured_kinds() -> None:
    model = Model(provider="api", id="model", context_window=1000, max_output_tokens=100)
    anthropic = AppConfig(providers={"api": ProviderConfig("anthropic")})
    openai = AppConfig(providers={"api": ProviderConfig("openai_compatible")})

    assert type(app_module._provider(anthropic, model)).__name__ == "AnthropicProvider"
    assert type(app_module._provider(openai, model)).__name__ == "OpenAICompatibleProvider"
    with pytest.raises(ValueError, match="unsupported provider"):
        app_module._provider(AppConfig(providers={"api": ProviderConfig("unknown")}), model)


@pytest.mark.asyncio
async def test_create_bundle_assembles_skills_tools_and_model_summarizer(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / ".le-agent" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code\n---\nInspect carefully.",
        encoding="utf-8",
    )
    provider = FauxProvider([ScriptedResponse.text("answer")])
    monkeypatch.setattr(app_module, "_provider", lambda _config, _model: provider)
    model = Model(provider="faux", id="test", context_window=1000, max_output_tokens=100)
    config = AppConfig(default_model="test", models={"test": model})

    bundle = await app_module.create_bundle(config=config, workspace=tmp_path, no_session=True)

    assert bundle.model_names == ("test",)
    assert set(bundle.skills) == {"review"}
    assert [tool.name for tool in bundle.harness.tools] == ["read", "write", "edit", "bash"]
    assert "review" in bundle.harness.system_prompt
    await bundle.harness.prompt("hello")
    assert (await bundle.session.build_context_messages())[-1].text == "answer"  # type: ignore[union-attr]

    summary_provider = FauxProvider([ScriptedResponse.text("summary")])
    summarizer = await app_module._model_summarizer(summary_provider, model, None)
    result = await summarizer(
        SummaryRequest(
            kind="branch",
            messages=[UserMessage(content=[TextContent(text="history")])],
            custom_instructions="focus",
        )
    )
    assert result == "summary"
    assert "User focus instructions" in summary_provider.requests[0].messages[0].content[0].text


def test_main_helpers_parse_modes_serialize_events_and_read_prompt(tmp_path) -> None:
    args = main_module._parser().parse_args(["--json", "--model", "test", "hello"])
    assert args.json_mode and args.prompt == "hello"
    path = tmp_path / "system.txt"
    path.write_text("system", encoding="utf-8")
    assert main_module._system_prompt(path) == "system"
    assert main_module._system_prompt(None) is None
    payload = json.loads(main_module._event_json(AgentEvent(type="agent_start")))
    assert payload == {"version": 1, "type": "agent_start"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["print", "json"])
async def test_noninteractive_modes_share_bundle_and_release_session(mode, monkeypatch, capsys) -> None:
    bundle = await _bundle("final")

    async def create(**_kwargs):
        return bundle

    monkeypatch.setattr(main_module, "create_bundle", create)
    monkeypatch.setattr(main_module, "load_config", lambda **_kwargs: AppConfig())
    args = SimpleNamespace(
        resume=None,
        continue_latest=False,
        new=False,
        model=None,
        permission=None,
        no_session=True,
        system_prompt=None,
        prompt="hello",
        json_mode=mode == "json",
        print_mode=mode == "print",
    )

    assert await main_module._run_noninteractive(args) == 0
    output = capsys.readouterr().out
    assert "final" in output
    with pytest.raises(RuntimeError, match="closed"):
        await bundle.session.append_session_info("closed")


def test_main_reports_noninteractive_configuration_errors(monkeypatch, capsys) -> None:
    class Parser:
        def parse_args(self):
            return SimpleNamespace(print_mode=True, json_mode=False)

    async def fail(_args):
        raise ValueError("bad config")

    monkeypatch.setattr(main_module, "_parser", lambda: Parser())
    monkeypatch.setattr(main_module, "_run_noninteractive", fail)

    assert main_module.main() == 2
    assert "bad config" in capsys.readouterr().out
