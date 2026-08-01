"""Console entry point for Textual, print, and JSONL execution modes."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from le_agent_ai import AssistantMessage
from le_agent_core.loop import AgentEvent

from .app import AppBundle, create_bundle, latest_session_id
from .config import load_config
from .permissions import PermissionMode
from .runtime import RuntimeController, RuntimeRequest
from .tui import LeAgentApp


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="le-agent", description="A pi-inspired local coding agent")
    parser.add_argument("prompt", nargs="?", help="initial prompt for the TUI or non-interactive mode")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--print", dest="print_mode", action="store_true", help="print only the final assistant text")
    mode.add_argument("--json", dest="json_mode", action="store_true", help="emit versioned JSONL lifecycle events")
    parser.add_argument("--model")
    parser.add_argument("--permission", choices=[item.value for item in PermissionMode])
    parser.add_argument("--resume")
    parser.add_argument("--continue", dest="continue_latest", action="store_true")
    parser.add_argument("--new", action="store_true")
    parser.add_argument("--no-session", action="store_true")
    parser.add_argument("--system-prompt", type=Path)
    return parser


def _event_json(event: AgentEvent) -> str:
    payload: dict[str, Any] = {"version": 1, "type": event.type}
    if event.message is not None:
        payload["message"] = event.message.model_dump(mode="json")
    if event.tool_call_id:
        payload["tool_call_id"] = event.tool_call_id
    if event.tool_name:
        payload["tool_name"] = event.tool_name
    if event.tool_result:
        payload["tool_result"] = event.tool_result.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False)


def _system_prompt(path: Path | None) -> str | None:
    if path is None:
        return None
    return path.read_text(encoding="utf-8")


async def _run_noninteractive(args: argparse.Namespace) -> int:
    workspace = Path.cwd()
    resume = args.resume or (latest_session_id(workspace) if args.continue_latest and not args.new else None)
    config = load_config(
        user_path=Path.home() / ".le-agent" / "config.toml",
        project_path=workspace / ".le-agent" / "config.toml",
    )
    bundle = await create_bundle(
        config=config,
        workspace=workspace,
        model_name=args.model,
        permission=PermissionMode(args.permission) if args.permission else None,
        resume=resume,
        no_session=args.no_session,
        system_prompt_override=_system_prompt(args.system_prompt),
    )
    try:
        if not args.prompt:
            raise ValueError("--print and --json require a prompt")
        agent = await bundle.harness.restore()
        if args.json_mode:

            async def render(event: AgentEvent) -> None:
                print(_event_json(event), flush=True)

            agent.subscribe(render)
        await bundle.harness.prompt(args.prompt)
        if args.print_mode:
            messages = await bundle.session.build_context_messages()
            assistant = next(
                (message for message in reversed(messages) if getattr(message, "role", "") == "assistant"),
                None,
            )
            if isinstance(assistant, AssistantMessage):
                print(assistant.text)
        return 0
    finally:
        await bundle.session.close()


def main() -> int:
    args = _parser().parse_args()
    if args.print_mode or args.json_mode:
        try:
            return asyncio.run(_run_noninteractive(args))
        except (ValueError, KeyError) as error:
            print(f"le-agent: {error}")
            return 2
    workspace = Path.cwd()
    resume = args.resume or (latest_session_id(workspace) if args.continue_latest and not args.new else None)
    config = load_config(
        user_path=Path.home() / ".le-agent" / "config.toml",
        project_path=workspace / ".le-agent" / "config.toml",
    )
    try:
        bundle = asyncio.run(
            create_bundle(
                config=config,
                workspace=workspace,
                model_name=args.model,
                permission=PermissionMode(args.permission) if args.permission else None,
                resume=resume,
                no_session=args.no_session,
                system_prompt_override=_system_prompt(args.system_prompt),
            )
        )
    except (ValueError, KeyError) as error:
        print(f"le-agent: {error}")
        return 2
    async def factory(request: RuntimeRequest) -> AppBundle:
        return await create_bundle(
            config=config,
            workspace=workspace,
            model_name=request.model_name,
            permission=PermissionMode(args.permission) if args.permission else None,
            resume=request.resume,
            no_session=args.no_session,
            system_prompt_override=_system_prompt(args.system_prompt),
            session_override=request.session,
        )

    LeAgentApp(RuntimeController(bundle, factory), initial_prompt=args.prompt).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
