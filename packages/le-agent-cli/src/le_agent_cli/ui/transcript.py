"""Scrollable transcript with coalesced assistant deltas and in-place tool cards."""

from __future__ import annotations

import time

from le_agent_ai.models import (
    AgentMessage,
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    StreamEvent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from rich.text import Text
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static


class AssistantMessageView(Static):
    def __init__(self, *, show_thinking: bool = True) -> None:
        super().__init__(classes="message assistant-message")
        self.text_content = ""
        self.thinking_content = ""
        self.show_thinking = show_thinking
        self.complete = False
        self.renderable = Text()

    def append_delta(self, event: StreamEvent) -> None:
        if event.type == "text_delta":
            self.text_content += event.delta or ""
        elif event.type == "thinking_delta":
            self.thinking_content += event.delta or ""
        elif event.type == "error":
            self.text_content = event.error_message or "模型请求失败"
        self.refresh_content()

    def finish(self, message: AssistantMessage) -> None:
        self.text_content = message.text or message.error_message or (
            "请求已中止" if message.stop_reason == "aborted" else ""
        )
        self.thinking_content = "".join(
            block.thinking for block in message.content if isinstance(block, ThinkingContent)
        )
        self.complete = True
        self.refresh_content()

    def refresh_content(self) -> None:
        rendered = Text()
        if self.show_thinking and self.thinking_content:
            rendered.append(f"thinking  {self.thinking_content}\n", style="#6f7a89 italic")
        rendered.append(self.text_content or ("…" if not self.complete else ""), style="#d8dee9")
        self.renderable = rendered
        self.update(rendered)


class WaitingMessage(Static):
    def __init__(self) -> None:
        super().__init__(classes="message waiting-message")
        self.started_at = time.monotonic()
        self.renderable = Text()

    def on_mount(self) -> None:
        self.set_interval(0.1, self.refresh_content)
        self.refresh_content()

    def refresh_content(self) -> None:
        elapsed = time.monotonic() - self.started_at
        frame = "◐◓◑◒"[int(elapsed * 8) % 4]
        rendered = Text.assemble(
            (f"{frame} 正在等待", "#88c0d0"),
            (f" · {elapsed:.1f}s · Esc 中止", "#6f7a89"),
        )
        self.renderable = rendered
        self.update(rendered)


class ToolCard(Static):
    def __init__(self, tool_call_id: str, tool_name: str) -> None:
        super().__init__(classes="message system-message")
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.state = "running"
        self.output = ""
        self.expanded = False
        self.renderable = Text()
        self.refresh_content()

    def append_update(self, text: str) -> None:
        self.output += text
        self.refresh_content()

    def finish(self, result: ToolResultMessage) -> None:
        self.state = "error" if result.is_error else "success"
        self.output = "".join(block.text for block in result.content)
        self.refresh_content()

    def refresh_content(self) -> None:
        icon = {"running": "●", "success": "✓", "error": "✗", "aborted": "■"}[self.state]
        color = {"running": "#ebcb8b", "success": "#a3be8c", "error": "#bf616a", "aborted": "#8f9baa"}[
            self.state
        ]
        rendered = Text.assemble((f"{icon} {self.tool_name}", color))
        if self.output:
            visible = self.output if self.expanded else self.output[-160:]
            rendered.append(f"\n{visible}", style="#8f9baa")
        self.renderable = rendered
        self.update(rendered)


class Transcript(VerticalScroll):
    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        can_focus: bool | None = None,
        can_focus_children: bool | None = None,
        can_maximize: bool | None = None,
    ) -> None:
        super().__init__(
            *children,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            can_focus=can_focus,
            can_focus_children=can_focus_children,
            can_maximize=can_maximize,
        )
        self.current_assistant: AssistantMessageView | None = None
        self.tool_cards: dict[str, ToolCard] = {}
        self._pending_events: list[StreamEvent] = []
        self._flush_timer: Timer | None = None
        self.waiting_message: WaitingMessage | None = None

    async def append_message(self, text: str, kind: str = "assistant") -> None:
        row = Static(text, classes=f"message {kind}-message")
        await self.mount(row)
        self.scroll_end(animate=False)

    async def clear_messages(self) -> None:
        was_waiting = self.waiting_message is not None
        if self._flush_timer is not None:
            self._flush_timer.stop()
        self._flush_timer = None
        self._pending_events.clear()
        self.current_assistant = None
        self.waiting_message = None
        self.tool_cards.clear()
        await self.remove_children()
        if was_waiting:
            await self.start_waiting()

    async def reload_from_context(self, messages: list[AgentMessage]) -> None:
        await self.clear_messages()
        for message in messages:
            if isinstance(message, UserMessage):
                text = "".join(block.text for block in message.content)
                await self.append_message(f"❯ {text}", "user")
            elif isinstance(message, AssistantMessage):
                has_visible_content = bool(message.text or message.error_message) or any(
                    isinstance(block, ThinkingContent) for block in message.content
                )
                if has_visible_content:
                    await self.finish_assistant(message)
                for block in message.content:
                    if isinstance(block, ToolCallContent):
                        await self.start_tool(block.id, block.name)
            elif isinstance(message, ToolResultMessage):
                if message.tool_call_id not in self.tool_cards:
                    await self.start_tool(message.tool_call_id, message.tool_name)
                self.finish_tool(message.tool_call_id, message)
            elif isinstance(message, CompactionSummaryMessage):
                await self.append_message(f"上下文摘要\n{message.summary}", "system")
            elif isinstance(message, BranchSummaryMessage):
                await self.append_message(f"分支摘要\n{message.summary}", "system")
            elif isinstance(message, CustomMessage) and message.display:
                await self.append_message(message.content, "system")

    async def start_assistant(self) -> AssistantMessageView:
        if self.current_assistant is None:
            self.current_assistant = AssistantMessageView()
            await self.mount(self.current_assistant)
        return self.current_assistant

    async def start_waiting(self) -> WaitingMessage:
        if self.waiting_message is None:
            self.waiting_message = WaitingMessage()
            await self.mount(self.waiting_message)
            self.scroll_end(animate=False)
        return self.waiting_message

    async def stop_waiting(self) -> None:
        waiting, self.waiting_message = self.waiting_message, None
        if waiting is not None and waiting.parent is self:
            await waiting.remove()

    async def queue_assistant_event(self, event: StreamEvent) -> None:
        await self.start_assistant()
        self._pending_events.append(event)
        if self._flush_timer is None:
            self._flush_timer = self.set_timer(0.033, self.flush_pending)

    def flush_pending(self) -> None:
        if self.current_assistant:
            for event in self._pending_events:
                self.current_assistant.append_delta(event)
        self._pending_events.clear()
        self._flush_timer = None
        self.scroll_end(animate=False)

    async def finish_assistant(self, message: AssistantMessage) -> None:
        view = await self.start_assistant()
        self.flush_pending()
        view.finish(message)
        self.current_assistant = None

    async def start_tool(self, call_id: str, name: str) -> None:
        card = ToolCard(call_id, name)
        self.tool_cards[call_id] = card
        await self.mount(card)
        self.scroll_end(animate=False)

    def update_tool(self, call_id: str, text: str) -> None:
        card = self.tool_cards.get(call_id)
        if card:
            card.append_update(text)

    def finish_tool(self, call_id: str, result: ToolResultMessage) -> None:
        card = self.tool_cards.get(call_id)
        if card:
            card.finish(result)

    def toggle_tools(self, expanded: bool) -> None:
        for card in self.tool_cards.values():
            card.expanded = expanded
            card.refresh_content()
