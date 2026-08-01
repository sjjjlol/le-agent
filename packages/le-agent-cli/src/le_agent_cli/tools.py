"""Small, composable coding tools with workspace-root safety checks."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from pathlib import Path
from typing import Any

from le_agent_ai import TextContent
from le_agent_core import AgentTool, ToolResult
from pydantic import BaseModel, Field


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_workspace_path(root: Path, raw_path: str, *, readable_roots: list[Path] | None = None) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    roots = [root.resolve(), *(item.resolve() for item in readable_roots or [])]
    if not any(_inside(resolved, allowed) for allowed in roots):
        raise ValueError(f"path is outside allowed workspace roots: {raw_path}")
    return resolved


class ReadArgs(BaseModel):
    path: str
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=500, ge=1, le=5000)


class ReadTool(AgentTool[ReadArgs]):
    name = "read"
    description = "Read a text file with 1-based line numbers."
    args_model = ReadArgs
    execution_mode = "parallel"

    def __init__(self, workspace: Path, *, skill_roots: list[Path] | None = None) -> None:
        self.workspace = workspace.resolve()
        self.skill_roots = skill_roots or []

    async def execute(self, args: ReadArgs, *, on_update: Any = None) -> ToolResult:
        del on_update
        try:
            path = resolve_workspace_path(self.workspace, args.path, readable_roots=self.skill_roots)
            if not path.is_file():
                return ToolResult.text(f"file not found: {args.path}", is_error=True)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            selected = lines[args.offset - 1 : args.offset - 1 + args.limit]
            body = "\n".join(f"{index}: {line}" for index, line in enumerate(selected, start=args.offset))
            if args.offset - 1 + args.limit < len(lines):
                body += f"\n[truncated; {len(lines)} total lines]"
            return ToolResult.text(body or "[empty file]")
        except ValueError as error:
            return ToolResult.text(str(error), is_error=True)


class WriteArgs(BaseModel):
    path: str
    content: str


class WriteTool(AgentTool[WriteArgs]):
    name = "write"
    description = "Create or replace a text file inside the workspace."
    args_model = WriteArgs
    execution_mode = "sequential"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    async def execute(self, args: WriteArgs, *, on_update: Any = None) -> ToolResult:
        del on_update
        try:
            path = resolve_workspace_path(self.workspace, args.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.content, encoding="utf-8")
            return ToolResult.text(f"wrote {path.relative_to(self.workspace)} ({len(args.content)} characters)")
        except (OSError, ValueError) as error:
            return ToolResult.text(str(error), is_error=True)


class EditArgs(BaseModel):
    path: str
    old_text: str
    new_text: str
    replace_all: bool = False


class EditTool(AgentTool[EditArgs]):
    name = "edit"
    description = "Replace exact text in one workspace file."
    args_model = EditArgs
    execution_mode = "sequential"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    async def execute(self, args: EditArgs, *, on_update: Any = None) -> ToolResult:
        del on_update
        try:
            path = resolve_workspace_path(self.workspace, args.path)
            original = path.read_text(encoding="utf-8")
            occurrences = original.count(args.old_text)
            if occurrences == 0:
                return ToolResult.text("old_text was not found", is_error=True)
            if occurrences > 1 and not args.replace_all:
                return ToolResult.text(
                    "old_text matched multiple locations; set replace_all=true to replace all",
                    is_error=True,
                )
            updated = original.replace(args.old_text, args.new_text, -1 if args.replace_all else 1)
            path.write_text(updated, encoding="utf-8")
            count = occurrences if args.replace_all else 1
            return ToolResult.text(f"edited {path.relative_to(self.workspace)} ({count} replacement)")
        except (OSError, ValueError) as error:
            return ToolResult.text(str(error), is_error=True)


class BashArgs(BaseModel):
    command: str
    timeout: int = Field(default=120, ge=1, le=600)


class BashTool(AgentTool[BashArgs]):
    name = "bash"
    description = "Run a shell command in the workspace and return combined output."
    args_model = BashArgs
    execution_mode = "sequential"

    def __init__(self, workspace: Path, *, output_limit: int = 12_000, log_root: Path | None = None) -> None:
        self.workspace = workspace.resolve()
        self.output_limit = output_limit
        self.log_root = log_root

    async def execute(self, args: BashArgs, *, on_update: Any = None) -> ToolResult:
        log_path: Path | None = None
        chunks: list[str] = []
        try:
            process = await asyncio.create_subprocess_shell(
                args.command,
                cwd=self.workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            if self.log_root is not None:
                self.log_root.mkdir(parents=True, exist_ok=True)
                log_path = self.log_root / f"{uuid.uuid4().hex}.log"
            try:
                async with asyncio.timeout(args.timeout):
                    if process.stdout is None:
                        raise RuntimeError("bash stdout pipe is unavailable")
                    while chunk := await process.stdout.read(4096):
                        text = chunk.decode("utf-8", errors="replace")
                        chunks.append(text)
                        if log_path:
                            with log_path.open("a", encoding="utf-8") as handle:
                                handle.write(text)
                        if on_update:
                            update_result = on_update(text)
                            if inspect.isawaitable(update_result):
                                await update_result
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(
                    content=[TextContent(text=f"command timed out after {args.timeout}s")],
                    is_error=True,
                    details={"log_path": str(log_path)} if log_path else None,
                )
            output = "".join(chunks)
            rendered = output[-self.output_limit :]
            if len(output) > self.output_limit:
                suffix = f"; full log: {log_path}" if log_path else ""
                rendered = f"[output truncated{suffix}]\n{rendered}"
            prefix = f"exit code {process.returncode}"
            return ToolResult(
                content=[TextContent(text=f"{prefix}\n{rendered}")],
                is_error=process.returncode != 0,
                details={"log_path": str(log_path)} if log_path else None,
            )
        except asyncio.CancelledError:
            if "process" in locals() and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except OSError as error:
            return ToolResult.text(str(error), is_error=True)
