import asyncio
from pathlib import Path

import pytest
from le_agent_cli.tools import BashArgs, BashTool, EditArgs, EditTool, ReadArgs, ReadTool, WriteArgs, WriteTool


@pytest.mark.asyncio
async def test_read_returns_numbered_lines_inside_workspace(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("one\ntwo\n", encoding="utf-8")
    result = await ReadTool(tmp_path).execute(ReadArgs(path="note.txt"))

    assert "1: one" in result.content[0].text
    assert "2: two" in result.content[0].text


@pytest.mark.asyncio
async def test_write_refuses_path_outside_workspace(tmp_path: Path) -> None:
    result = await WriteTool(tmp_path).execute(WriteArgs(path="../escape.txt", content="no"))

    assert result.is_error
    assert "outside" in result.content[0].text


@pytest.mark.asyncio
async def test_edit_requires_exactly_one_match_by_default(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("same\nsame\n", encoding="utf-8")
    result = await EditTool(tmp_path).execute(EditArgs(path="note.txt", old_text="same", new_text="new"))

    assert result.is_error
    assert target.read_text(encoding="utf-8") == "same\nsame\n"


@pytest.mark.asyncio
async def test_bash_streams_chunks_and_saves_complete_output(tmp_path: Path) -> None:
    updates: list[str] = []
    logs = tmp_path / "logs"
    tool = BashTool(tmp_path, output_limit=8, log_root=logs)

    result = await tool.execute(
        BashArgs(command="printf 'first\\n'; /bin/sleep 0.05; printf 'second\\n'"),
        on_update=updates.append,
    )

    assert len(updates) >= 2
    assert "second" in result.content[0].text
    assert "truncated" in result.content[0].text
    assert result.details is not None
    log_path = Path(result.details["log_path"])
    assert log_path.parent == logs
    assert await asyncio.to_thread(log_path.read_text, encoding="utf-8") == "first\nsecond\n"
