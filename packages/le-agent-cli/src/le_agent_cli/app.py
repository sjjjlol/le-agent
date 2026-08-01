"""Application composition: configuration -> provider/session/tools -> harness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from le_agent_ai import Model, ProviderContext, TextContent, UserMessage
from le_agent_ai.models import AgentMessage
from le_agent_ai.provider import Provider
from le_agent_ai.providers import AnthropicProvider, OpenAICompatibleProvider
from le_agent_core.compaction import Summarizer
from le_agent_core.harness import AgentHarness
from le_agent_core.loop import AgentLoopConfig, AgentTool
from le_agent_core.session import JsonlSessionStore, MemorySessionStore, Session, SessionRepository

from .config import AppConfig, api_key_for, registry_from_config
from .permissions import PermissionController, PermissionMode
from .skills import Skill, load_skills, skill_catalog_prompt
from .tools import BashTool, EditTool, ReadTool, WriteTool

BASE_SYSTEM_PROMPT = """You are le-agent, a careful coding agent working in a local repository.
Inspect before changing files. Use tools deliberately, explain important decisions, and run relevant checks after edits.
Respect user approval and never claim success without evidence."""


@dataclass(slots=True)
class AppBundle:
    harness: AgentHarness
    policy: PermissionController
    skills: dict[str, Skill]
    session: Session
    model_name: str


def _session_root(workspace: Path) -> Path:
    encoded = workspace.resolve().as_posix().strip("/").replace("/", "-")
    return Path.home() / ".le-agent" / "sessions" / f"--{encoded}--"


def latest_session_id(workspace: Path) -> str | None:
    root = _session_root(workspace)
    candidates = list(root.glob("*.jsonl")) if root.is_dir() else []
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime).stem


def _provider(config: AppConfig, model: Model) -> Provider:
    provider_config = config.providers.get(model.provider)
    kind = provider_config.kind if provider_config else model.provider
    base_url = provider_config.base_url if provider_config and provider_config.base_url else model.base_url
    if kind == "anthropic":
        return AnthropicProvider(base_url=base_url)
    if kind in {"openai", "openai_compatible"}:
        return OpenAICompatibleProvider(base_url=base_url)
    raise ValueError(f"unsupported provider kind: {kind}")


async def _model_summarizer(provider: Provider, model: Model, api_key: str | None) -> Summarizer:
    async def summarize(messages: list[AgentMessage], previous_summary: str | None) -> str:
        transcript = "\n".join(json.dumps(message.model_dump(mode="json"), ensure_ascii=False) for message in messages)
        prompt = """Summarize this coding-agent session for a future agent. Preserve goal, constraints, completed work,
current work, key decisions, next steps, exact paths, and failures. Be concise and structured."""
        if previous_summary:
            prompt += f"\n\nPrevious summary:\n{previous_summary}"
        prompt += f"\n\nConversation:\n{transcript}"
        stream = await provider.stream(
            model,
            ProviderContext(
                system_prompt="You summarize context; do not continue the task.",
                messages=[UserMessage(content=[TextContent(text=prompt)])],
            ),
            api_key=api_key,
        )
        async for _ in stream:
            pass
        result = await stream.result()
        if result.stop_reason in {"error", "aborted"}:
            raise RuntimeError(result.error_message or "compaction summarization failed")
        return result.text

    return summarize


async def create_bundle(
    *,
    config: AppConfig,
    workspace: Path,
    model_name: str | None = None,
    permission: PermissionMode | None = None,
    resume: str | None = None,
    no_session: bool = False,
    system_prompt_override: str | None = None,
) -> AppBundle:
    registry = registry_from_config(config)
    selected_name = model_name or config.default_model
    if not selected_name:
        raise ValueError("no model selected; set default_model in .le-agent/config.toml or pass --model")
    model = registry.require(selected_name)
    provider = _provider(config, model)
    api_key = api_key_for(config, model.provider)
    project_skills = workspace / ".le-agent" / "skills"
    user_skills = Path.home() / ".le-agent" / "skills"
    skills = load_skills(user_skills, project_skills)
    system_prompt = system_prompt_override or (BASE_SYSTEM_PROMPT + "\n\n" + skill_catalog_prompt(skills))
    policy = PermissionController(permission or PermissionMode(config.permission))
    repository = SessionRepository(MemorySessionStore() if no_session else JsonlSessionStore(_session_root(workspace)))
    session = await repository.open(resume) if resume else await repository.create()
    tools: list[AgentTool[Any]] = [
        ReadTool(workspace, skill_roots=[user_skills, project_skills]),
        WriteTool(workspace),
        EditTool(workspace),
        BashTool(workspace),
    ]
    loop_config = AgentLoopConfig(
        model=model,
        provider=provider,
        api_key=api_key,
        before_tool_call=policy.before_tool_call,
    )
    summarizer = await _model_summarizer(provider, model, api_key)
    harness = AgentHarness(
        session=session,
        config=loop_config,
        tools=tools,
        system_prompt=system_prompt,
        summarizer=summarizer,
    )
    return AppBundle(harness=harness, policy=policy, skills=skills, session=session, model_name=selected_name)
