"""Append-only session trees and JSONL persistence modeled after pi sessions."""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO

from le_agent_ai.models import (
    AgentMessage,
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    ToolResultMessage,
    UserMessage,
)

ENTRY_TYPES = {
    "message",
    "model_change",
    "thinking_level_change",
    "active_tools_change",
    "compaction",
    "branch_summary",
    "leaf",
    "label",
    "session_info",
    "custom",
}


@dataclass(frozen=True, slots=True)
class SessionEntry:
    id: str
    parent_id: str | None
    timestamp: float
    type: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "timestamp": self.timestamp,
            "type": self.type,
            **self.payload,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SessionEntry":
        entry_type = value.get("type")
        if entry_type not in ENTRY_TYPES:
            raise ValueError(f"invalid session entry type: {entry_type!r}")
        identifier = value.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("session entry is missing id")
        parent_id = value.get("parent_id")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError("session entry has invalid parent_id")
        timestamp = value.get("timestamp")
        if not isinstance(timestamp, (int, float)):
            raise ValueError("session entry has invalid timestamp")
        payload = {key: item for key, item in value.items() if key not in {"id", "parent_id", "timestamp", "type"}}
        return cls(identifier, parent_id, float(timestamp), entry_type, payload)


@dataclass(frozen=True, slots=True)
class SessionTreeNode:
    entry: SessionEntry
    children: tuple["SessionTreeNode", ...]
    label: str | None = None


@dataclass(frozen=True, slots=True)
class BranchDivergence:
    common_ancestor_id: str | None
    abandoned_entries: tuple[SessionEntry, ...]


class SessionLockedError(RuntimeError):
    pass


def _message_to_dict(message: AgentMessage) -> dict[str, Any]:
    return message.model_dump(mode="json")


def _message_from_dict(value: dict[str, Any]) -> AgentMessage:
    role = value.get("role")
    if role == "user":
        return UserMessage.model_validate(value)
    if role == "assistant":
        return AssistantMessage.model_validate(value)
    if role == "tool_result":
        return ToolResultMessage.model_validate(value)
    if role == "custom":
        return CustomMessage.model_validate(value)
    if role == "compaction_summary":
        return CompactionSummaryMessage.model_validate(value)
    if role == "branch_summary":
        return BranchSummaryMessage.model_validate(value)
    raise ValueError(f"unknown message role in session: {role!r}")


class SessionStore(Protocol):
    async def create(self, identifier: str, created_at: float) -> list[SessionEntry]: ...

    async def load(self, identifier: str) -> list[SessionEntry]: ...

    async def append(self, identifier: str, entry: SessionEntry) -> None: ...

    async def close(self, identifier: str) -> None: ...


class MemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, list[SessionEntry]] = {}

    async def create(self, identifier: str, created_at: float) -> list[SessionEntry]:
        del created_at
        if identifier in self._sessions:
            raise ValueError(f"session already exists: {identifier}")
        self._sessions[identifier] = []
        return []

    async def load(self, identifier: str) -> list[SessionEntry]:
        if identifier not in self._sessions:
            raise KeyError(f"session not found: {identifier}")
        return list(self._sessions[identifier])

    async def append(self, identifier: str, entry: SessionEntry) -> None:
        entries = self._sessions.get(identifier)
        if entries is None:
            raise KeyError(f"session not found: {identifier}")
        if any(existing.id == entry.id for existing in entries):
            raise ValueError(f"duplicate session entry id: {entry.id}")
        entries.append(entry)

    async def close(self, identifier: str) -> None:
        del identifier


class JsonlSessionStore:
    """One JSON object per line; header is durable metadata, remainder is an append-only entry log."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock_handles: dict[str, TextIO] = {}

    def _path(self, identifier: str) -> Path:
        return self.root / f"{identifier}.jsonl"

    async def create(self, identifier: str, created_at: float) -> list[SessionEntry]:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(identifier)
        if path.exists():
            raise ValueError(f"session already exists: {identifier}")
        header = {"type": "session", "version": 2, "id": identifier, "created_at": created_at}
        handle = path.open("x+", encoding="utf-8")
        try:
            self._acquire(identifier, handle)
            handle.write(json.dumps(header, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            handle.close()
            self._lock_handles.pop(identifier, None)
            raise
        return []

    async def load(self, identifier: str) -> list[SessionEntry]:
        path = self._path(identifier)
        if not path.exists():
            raise KeyError(f"session not found: {identifier}")
        handle = path.open("r+", encoding="utf-8")
        try:
            self._acquire(identifier, handle)
            handle.seek(0)
            lines = handle.read().splitlines()
            return self._parse_lines(identifier, lines)
        except Exception:
            handle.close()
            self._lock_handles.pop(identifier, None)
            raise

    @staticmethod
    def _parse_lines(identifier: str, lines: list[str]) -> list[SessionEntry]:
        if not lines:
            raise ValueError("session is missing header")
        header = json.loads(lines[0])
        if (
            header.get("type") != "session"
            or header.get("version") not in {1, 2}
            or header.get("id") != identifier
        ):
            raise ValueError("invalid session header")
        entries: list[SessionEntry] = []
        nonempty = [line for line in lines[1:] if line.strip()]
        for index, line in enumerate(nonempty):
            try:
                entries.append(SessionEntry.from_dict(json.loads(line)))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                if index == len(nonempty) - 1:
                    break  # a process may have died while appending its final line
                raise ValueError("invalid session entry before end of JSONL log") from error
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("session contains duplicate entry ids")
        return entries

    async def append(self, identifier: str, entry: SessionEntry) -> None:
        path = self._path(identifier)
        if not path.exists():
            raise KeyError(f"session not found: {identifier}")
        if identifier not in self._lock_handles:
            raise SessionLockedError(f"session is not open for writing: {identifier}")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    async def close(self, identifier: str) -> None:
        handle = self._lock_handles.pop(identifier, None)
        if handle is None:
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def _acquire(self, identifier: str, handle: TextIO) -> None:
        if identifier in self._lock_handles:
            raise SessionLockedError(f"session is already open for writing: {identifier}")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SessionLockedError(f"session is already open for writing: {identifier}") from error
        self._lock_handles[identifier] = handle


class Session:
    def __init__(self, identifier: str, store: SessionStore, entries: list[SessionEntry]) -> None:
        self.id = identifier
        self._store = store
        self._entries = list(entries)
        self._by_id = {entry.id: entry for entry in entries}
        if len(self._by_id) != len(entries):
            raise ValueError("session contains duplicate entry ids")
        self._leaf_id = self._derive_leaf()
        self._closed = False

    def _derive_leaf(self) -> str | None:
        leaf: str | None = None
        for entry in self._entries:
            leaf = entry.payload["target_id"] if entry.type == "leaf" else entry.id
        return leaf

    async def entries(self) -> list[SessionEntry]:
        return list(self._entries)

    async def leaf_id(self) -> str | None:
        return self._leaf_id

    async def get_entry(self, entry_id: str) -> SessionEntry:
        try:
            return self._by_id[entry_id]
        except KeyError as error:
            raise KeyError(f"entry not found: {entry_id}") from error

    async def get_tree(self) -> tuple[SessionTreeNode, ...]:
        labels: dict[str, str] = {}
        visible = [entry for entry in self._entries if entry.type != "leaf"]
        visible_ids = {entry.id for entry in visible}
        children: dict[str | None, list[SessionEntry]] = {}
        for entry in visible:
            if entry.parent_id is not None and entry.parent_id not in visible_ids:
                raise ValueError(f"broken session parent reference: {entry.parent_id}")
            children.setdefault(entry.parent_id, []).append(entry)
            if entry.type == "label":
                target_id = str(entry.payload["target_id"])
                label = entry.payload.get("label")
                if label:
                    labels[target_id] = str(label)
                else:
                    labels.pop(target_id, None)

        def build(entry: SessionEntry) -> SessionTreeNode:
            return SessionTreeNode(
                entry=entry,
                children=tuple(build(child) for child in children.get(entry.id, [])),
                label=labels.get(entry.id),
            )

        return tuple(build(entry) for entry in children.get(None, []))

    async def append_message(self, message: AgentMessage) -> str:
        return await self._append("message", {"message": _message_to_dict(message)})

    async def append_model_change(self, provider: str, model_id: str) -> str:
        return await self._append("model_change", {"provider": provider, "model_id": model_id})

    async def append_thinking_level_change(self, level: str) -> str:
        return await self._append("thinking_level_change", {"level": level})

    async def append_active_tools_change(self, names: list[str]) -> str:
        return await self._append("active_tools_change", {"names": list(names)})

    async def append_compaction(
        self, summary: str, *, first_kept_entry_id: str | None, tokens_before: int, retained_tail: list[AgentMessage]
    ) -> str:
        return await self._append(
            "compaction",
            {
                "summary": summary,
                "first_kept_entry_id": first_kept_entry_id,
                "tokens_before": tokens_before,
                "retained_tail": [_message_to_dict(message) for message in retained_tail],
            },
        )

    async def append_branch_summary(self, summary: str, from_id: str) -> str:
        return await self._append("branch_summary", {"summary": summary, "from_id": from_id})

    async def append_label(self, target_id: str, label: str | None) -> str:
        if target_id not in self._by_id:
            raise KeyError(f"entry not found: {target_id}")
        return await self._append("label", {"target_id": target_id, "label": label})

    async def append_session_info(self, name: str) -> str:
        return await self._append("session_info", {"name": name.replace("\n", " ").strip()})

    async def append_custom(self, custom_type: str, data: Any = None) -> str:
        return await self._append("custom", {"custom_type": custom_type, "data": data})

    async def close(self) -> None:
        if self._closed:
            return
        await self._store.close(self.id)
        self._closed = True

    async def move_to(self, entry_id: str | None) -> None:
        if entry_id is not None and entry_id not in self._by_id:
            raise KeyError(f"entry not found: {entry_id}")
        self._leaf_id = entry_id

    async def branch_divergence(self, from_id: str, to_id: str) -> BranchDivergence:
        old_branch = await self.branch(from_id)
        target_branch = await self.branch(to_id)
        common_ancestor_id: str | None = None
        shared_count = 0
        for old_entry, target_entry in zip(old_branch, target_branch, strict=False):
            if old_entry.id != target_entry.id:
                break
            common_ancestor_id = old_entry.id
            shared_count += 1
        return BranchDivergence(common_ancestor_id, tuple(old_branch[shared_count:]))

    async def branch(self, from_id: str | None = None) -> list[SessionEntry]:
        current = self._leaf_id if from_id is None else from_id
        branch: list[SessionEntry] = []
        while current is not None:
            entry = self._by_id.get(current)
            if entry is None:
                raise ValueError(f"broken session parent reference: {current}")
            branch.append(entry)
            current = entry.parent_id
        branch.reverse()
        return branch

    async def build_context_messages(self) -> list[AgentMessage]:
        branch = await self.branch()
        latest_compaction = max((index for index, entry in enumerate(branch) if entry.type == "compaction"), default=-1)
        context: list[AgentMessage] = []
        start = 0
        if latest_compaction >= 0:
            compaction = branch[latest_compaction]
            context.append(
                CompactionSummaryMessage(
                    summary=str(compaction.payload["summary"]), tokens_before=int(compaction.payload["tokens_before"])
                )
            )
            context.extend(_message_from_dict(item) for item in compaction.payload.get("retained_tail", []))
            start = latest_compaction + 1
        for entry in branch[start:]:
            if entry.type == "message":
                context.append(_message_from_dict(entry.payload["message"]))
            elif entry.type == "branch_summary":
                context.append(BranchSummaryMessage(summary=entry.payload["summary"], from_id=entry.payload["from_id"]))
        return context

    async def messages_for_entries(self, entries: tuple[SessionEntry, ...]) -> list[AgentMessage]:
        """Project complete message-like entries for an abandoned branch summary."""
        messages: list[AgentMessage] = []
        for entry in entries:
            if entry.type == "message":
                messages.append(_message_from_dict(entry.payload["message"]))
            elif entry.type == "branch_summary":
                messages.append(
                    BranchSummaryMessage(summary=entry.payload["summary"], from_id=entry.payload["from_id"])
                )
            elif entry.type == "compaction":
                messages.append(
                    CompactionSummaryMessage(
                        summary=str(entry.payload["summary"]),
                        tokens_before=int(entry.payload["tokens_before"]),
                    )
                )
                messages.extend(_message_from_dict(item) for item in entry.payload.get("retained_tail", []))
        return messages

    async def entry_id_for_message(self, target: AgentMessage) -> str | None:
        """Return the current-branch entry that stores this exact message payload."""
        target_data = _message_to_dict(target)
        for entry in await self.branch():
            if entry.type == "message" and entry.payload.get("message") == target_data:
                return entry.id
        return None

    async def _append(self, entry_type: str, payload: dict[str, Any]) -> str:
        if self._closed:
            raise RuntimeError(f"session is closed: {self.id}")
        identifier = uuid.uuid4().hex[:12]
        entry = SessionEntry(identifier, self._leaf_id, time.time(), entry_type, payload)
        await self._store.append(self.id, entry)
        self._entries.append(entry)
        self._by_id[entry.id] = entry
        self._leaf_id = entry.id
        return entry.id


class SessionRepository:
    def __init__(self, store: SessionStore) -> None:
        self._store = store

    async def create(self) -> Session:
        identifier = uuid.uuid4().hex
        created_at = time.time()
        entries = await self._store.create(identifier, created_at)
        return Session(identifier, self._store, entries)

    async def open(self, identifier: str) -> Session:
        return Session(identifier, self._store, await self._store.load(identifier))
