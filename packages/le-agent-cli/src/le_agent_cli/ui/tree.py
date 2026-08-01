"""Readable, searchable session-tree projection and interactive navigation screens."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from le_agent_core.session import Session, SessionEntry, SessionTreeNode
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option


class TreeFilter(str, Enum):
    DEFAULT = "default"
    NO_TOOLS = "no_tools"
    USER_ONLY = "user_only"
    LABELED = "labeled"
    ALL = "all"

    @property
    def display_name(self) -> str:
        return {
            self.DEFAULT: "默认",
            self.NO_TOOLS: "隐藏工具",
            self.USER_ONLY: "仅用户",
            self.LABELED: "仅标签",
            self.ALL: "全部",
        }[self]


@dataclass(slots=True)
class TreeRow:
    entry_id: str
    depth: int
    summary: str
    preview: str
    entry_type: str
    message_role: str | None
    is_tool: bool
    on_current_path: bool
    label: str | None = None
    is_current: bool = False
    has_children: bool = False

    @property
    def display(self) -> str:
        branch = "  " * self.depth
        marker = "◆ current" if self.is_current else ("│" if self.on_current_path else "·")
        label = f"  #{self.label}" if self.label else ""
        return f"{branch}{marker}  {self.summary}{label}"


@dataclass(slots=True)
class SessionTreeModel:
    roots: tuple[SessionTreeNode, ...]
    current_path: frozenset[str]
    leaf_id: str | None
    tool_calls: dict[str, tuple[str, dict[str, Any]]]

    @classmethod
    async def from_session(cls, session: Session) -> SessionTreeModel:
        branch = await session.branch()
        roots = await session.get_tree()
        tool_calls: dict[str, tuple[str, dict[str, Any]]] = {}
        for entry in await session.entries():
            if entry.type != "message":
                continue
            message = entry.payload.get("message", {})
            if message.get("role") != "assistant":
                continue
            for part in message.get("content", []):
                if part.get("type") == "tool_call":
                    tool_calls[str(part["id"])] = (str(part["name"]), dict(part.get("arguments", {})))
        return cls(
            roots=roots,
            current_path=frozenset(entry.id for entry in branch),
            leaf_id=await session.leaf_id(),
            tool_calls=tool_calls,
        )

    def rows(
        self,
        tree_filter: TreeFilter = TreeFilter.DEFAULT,
        *,
        query: str = "",
        collapsed: frozenset[str] = frozenset(),
    ) -> list[TreeRow]:
        candidates: list[TreeRow] = []

        def visit(node: SessionTreeNode, depth: int) -> None:
            summary, preview, role, is_tool = self._describe(node.entry)
            row = TreeRow(
                entry_id=node.entry.id,
                depth=depth,
                summary=summary,
                preview=preview,
                entry_type=node.entry.type,
                message_role=role,
                is_tool=is_tool,
                on_current_path=node.entry.id in self.current_path,
                label=node.label,
                has_children=bool(node.children),
            )
            if self._included(row, tree_filter, query):
                candidates.append(row)
            if node.entry.id not in collapsed:
                for child in sorted(node.children, key=lambda item: item.entry.id not in self.current_path):
                    visit(child, depth + 1)

        for root in sorted(self.roots, key=lambda item: item.entry.id not in self.current_path):
            visit(root, 0)
        visible_current = next((row for row in reversed(candidates) if row.on_current_path), None)
        if visible_current:
            index = candidates.index(visible_current)
            candidates[index] = replace(visible_current, is_current=True)
        return candidates

    def _included(self, row: TreeRow, tree_filter: TreeFilter, query: str) -> bool:
        if row.entry_type == "leaf":
            return False
        if tree_filter is TreeFilter.DEFAULT:
            included = row.entry_type in {"message", "compaction", "branch_summary", "custom"}
        elif tree_filter is TreeFilter.NO_TOOLS:
            included = row.entry_type in {"message", "compaction", "branch_summary", "custom"} and not row.is_tool
        elif tree_filter is TreeFilter.USER_ONLY:
            included = row.message_role == "user"
        elif tree_filter is TreeFilter.LABELED:
            included = bool(row.label)
        else:
            included = True
        needle = query.strip().casefold()
        haystack = f"{row.summary} {row.preview} {row.label or ''} {row.entry_id}".casefold()
        return included and (not needle or needle in haystack)

    def _describe(self, entry: SessionEntry) -> tuple[str, str, str | None, bool]:
        if entry.type == "message":
            message = entry.payload.get("message", {})
            role = str(message.get("role", "message"))
            content = message.get("content", [])
            text = " ".join(
                str(part.get("text", "")).strip() for part in content if part.get("type") == "text"
            ).strip()
            if role == "user":
                return f"user: {self._short(text)}", text, role, False
            if role == "assistant":
                if text:
                    return f"assistant: {self._short(text)}", text, role, False
                call = next((part for part in content if part.get("type") == "tool_call"), None)
                if call:
                    detail = self._tool_detail(str(call.get("name", "tool")), dict(call.get("arguments", {})))
                    return detail, str(call.get("arguments", {})), role, True
                return "assistant: (empty)", "", role, False
            if role == "tool_result":
                call_id = str(message.get("tool_call_id", ""))
                name, arguments = self.tool_calls.get(
                    call_id,
                    (str(message.get("tool_name", "tool")), dict(message.get("details") or {})),
                )
                preview = " ".join(str(part.get("text", "")) for part in content)
                return self._tool_detail(name, arguments), preview, role, True
            return f"{role}: {self._short(text)}", text, role, False
        if entry.type == "compaction":
            summary = str(entry.payload.get("summary", ""))
            tokens = int(entry.payload.get("tokens_before", 0))
            return f"[compaction: {tokens:,} tokens] {self._short(summary)}", summary, None, False
        if entry.type == "branch_summary":
            summary = str(entry.payload.get("summary", ""))
            return f"[branch summary] {self._short(summary)}", summary, None, False
        if entry.type == "model_change":
            model = f"{entry.payload.get('provider', '')}/{entry.payload.get('model_id', '')}".strip("/")
            return f"[model: {model}]", model, None, False
        if entry.type == "label":
            label = str(entry.payload.get("label") or "移除标签")
            return f"[label: {label}]", str(entry.payload), None, False
        return f"[{entry.type}]", str(entry.payload), None, False

    @staticmethod
    def _short(value: str, limit: int = 88) -> str:
        clean = " ".join(value.split())
        return clean if len(clean) <= limit else f"{clean[: limit - 1]}…"

    @staticmethod
    def _tool_detail(name: str, arguments: dict[str, Any]) -> str:
        key = {"read": "path", "write": "path", "edit": "path", "bash": "command"}.get(name)
        detail = arguments.get(key) if key else None
        if detail is None:
            detail = next(iter(arguments.values()), "")
        suffix = f": {SessionTreeModel._short(str(detail), 64)}" if detail != "" else ""
        return f"[{name}{suffix}]"


@dataclass(frozen=True, slots=True)
class TreeSelection:
    entry_id: str


class TreeNavigator(ModalScreen[TreeSelection | None]):
    """Keyboard-first session tree browser. Enter selects; f filters; space folds; l labels."""

    BINDINGS = [
        ("escape", "cancel", "取消"),
        ("f", "cycle_filter", "筛选"),
        ("space", "toggle_fold", "折叠"),
        ("l", "label", "标签"),
    ]

    class LabelChanged(Message):
        pass

    def __init__(self, session: Session, model: SessionTreeModel) -> None:
        super().__init__()
        self.session = session
        self.model = model
        self.tree_filter = TreeFilter.DEFAULT
        self.collapsed: set[str] = set()
        self._rows: list[TreeRow] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("会话树 · Enter 选择 · F 筛选 · Space 折叠 · L 标签", id="tree-title"),
            Input(placeholder="搜索消息、工具、标签…", id="tree-search"),
            Static("筛选：默认", id="tree-filter"),
            Horizontal(OptionList(id="tree-options"), Static(id="tree-preview"), id="tree-content"),
            id="tree-dialog",
        )

    def on_mount(self) -> None:
        self._refresh_rows()
        self.query_one("#tree-search", Input).focus()
        self._update_responsive_layout(self.size.width)

    def on_resize(self) -> None:
        self._update_responsive_layout(self.size.width)

    def _update_responsive_layout(self, width: int) -> None:
        self.query_one("#tree-preview", Static).display = width >= 80

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "tree-search":
            self._refresh_rows()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id != "tree-options" or event.option.id is None:
            return
        row = next((item for item in self._rows if item.entry_id == event.option.id), None)
        if row:
            self.query_one("#tree-preview", Static).update(
                Text.assemble((row.summary + "\n\n", "bold #88c0d0"), row.preview or "无预览")
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "tree-options" and event.option.id:
            self.dismiss(TreeSelection(str(event.option.id)))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cycle_filter(self) -> None:
        filters = list(TreeFilter)
        self.tree_filter = filters[(filters.index(self.tree_filter) + 1) % len(filters)]
        self.query_one("#tree-filter", Static).update(f"筛选：{self.tree_filter.display_name}")
        self._refresh_rows()

    def action_toggle_fold(self) -> None:
        entry_id = self._highlighted_id()
        if not entry_id:
            return
        if entry_id in self.collapsed:
            self.collapsed.remove(entry_id)
        else:
            self.collapsed.add(entry_id)
        self._refresh_rows()

    def action_label(self) -> None:
        entry_id = self._highlighted_id()
        if entry_id:
            async def apply_label(value: str | None) -> None:
                await self._set_label(entry_id, value)

            self.app.push_screen(LabelScreen(), apply_label)

    async def _set_label(self, entry_id: str, value: str | None) -> None:
        if value is None:
            return
        await self.session.append_label(entry_id, value.strip() or None)
        self.model = await SessionTreeModel.from_session(self.session)
        self._refresh_rows()

    def _highlighted_id(self) -> str | None:
        option = self.query_one("#tree-options", OptionList).highlighted_option
        return str(option.id) if option and option.id else None

    def _refresh_rows(self) -> None:
        search = self.query_one("#tree-search", Input).value if self.is_mounted else ""
        self._rows = self.model.rows(self.tree_filter, query=search, collapsed=frozenset(self.collapsed))
        options = self.query_one("#tree-options", OptionList)
        options.clear_options()
        options.add_options(Option(row.display, id=row.entry_id) for row in self._rows)
        options.highlighted = 0 if self._rows else None


class LabelScreen(ModalScreen[str | None]):
    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("设置标签（留空即移除）"),
            Input(id="label-input"),
            Button("保存", id="save", variant="primary"),
            Button("取消", id="cancel"),
            id="label-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.dismiss(self.query_one("#label-input", Input).value)
        else:
            self.dismiss(None)


class NavigationChoice(str, Enum):
    SUMMARIZE = "summarize"
    DIRECT = "direct"
    CANCEL = "cancel"


class NavigationChoiceScreen(ModalScreen[NavigationChoice]):
    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("如何回溯到选中节点？"),
            Button("带摘要回溯", id=NavigationChoice.SUMMARIZE.value, variant="primary"),
            Button("直接回溯", id=NavigationChoice.DIRECT.value),
            Button("取消", id=NavigationChoice.CANCEL.value),
            id="tree-choice-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(NavigationChoice(event.button.id or NavigationChoice.CANCEL.value))
