"""Session-aware composition of Agent, compaction, persistence and safe save points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent import Agent, AgentState
from .compaction import (
    CompactionSettings,
    Summarizer,
    SummaryRequest,
    compact_session,
    estimate_context_tokens,
    should_compact,
)
from .loop import AgentEvent, AgentLoopConfig, AgentTool
from .session import Session


@dataclass(frozen=True, slots=True)
class TreeNavigationResult:
    old_leaf_id: str
    new_leaf_id: str
    common_ancestor_id: str | None
    summary_entry_id: str | None = None


class AgentHarness:
    def __init__(
        self,
        *,
        session: Session,
        config: AgentLoopConfig,
        tools: list[AgentTool[Any]] | None = None,
        system_prompt: str = "",
        compaction_settings: CompactionSettings | None = None,
        summarizer: Summarizer | None = None,
    ) -> None:
        self.session = session
        self.config = config
        self.tools = tools or []
        self.system_prompt = system_prompt
        self.compaction_settings = compaction_settings or CompactionSettings()
        self.summarizer = summarizer
        self.agent: Agent | None = None

    async def restore(self) -> Agent:
        messages = await self.session.build_context_messages()
        self.agent = Agent(
            self.config,
            initial_state=AgentState(system_prompt=self.system_prompt, messages=messages),
            tools=self.tools,
        )
        self.agent.subscribe(self._persist_message)
        return self.agent

    async def prompt(self, text: str) -> None:
        agent = self.agent or await self.restore()
        await agent.prompt(text)
        await self._auto_compact()

    async def continue_run(self) -> None:
        agent = self.agent or await self.restore()
        await agent.continue_run()
        await self._auto_compact()

    async def compact(
        self,
        summarizer: Summarizer | None = None,
        *,
        instructions: str | None = None,
    ) -> bool:
        active_summarizer = summarizer or self.summarizer
        if active_summarizer is None:
            raise RuntimeError("a summarizer is required for compaction")
        return await compact_session(
            self.session,
            self.compaction_settings,
            active_summarizer,
            instructions=instructions,
        )

    async def move_to(self, entry_id: str | None, *, summary: str | None = None) -> None:
        old_leaf = await self.session.leaf_id()
        await self.session.move_to(entry_id)
        if summary and old_leaf:
            await self.session.append_branch_summary(summary, old_leaf)
        self.agent = None

    async def navigate_tree(
        self,
        entry_id: str,
        *,
        summarize: bool = False,
        instructions: str | None = None,
        summarizer: Summarizer | None = None,
    ) -> TreeNavigationResult:
        """Move to a tree node, optionally preserving the abandoned branch as a child summary.

        Summary generation happens before the pointer is changed. A provider failure therefore
        leaves both the append-only history and the current leaf untouched.
        """
        old_leaf = await self.session.leaf_id()
        if old_leaf is None:
            raise RuntimeError("cannot navigate an empty session")
        await self.session.get_entry(entry_id)
        divergence = await self.session.branch_divergence(old_leaf, entry_id)
        if old_leaf == entry_id:
            return TreeNavigationResult(old_leaf, old_leaf, divergence.common_ancestor_id)

        summary: str | None = None
        if summarize and divergence.abandoned_entries:
            active_summarizer = summarizer or self.summarizer
            if active_summarizer is None:
                raise RuntimeError("a summarizer is required for summarized navigation")
            messages = await self.session.messages_for_entries(divergence.abandoned_entries)
            summary = await active_summarizer(
                SummaryRequest(kind="branch", messages=messages, custom_instructions=instructions)
            )

        await self.session.move_to(entry_id)
        summary_entry_id = None
        if summary:
            try:
                summary_entry_id = await self.session.append_branch_summary(summary, old_leaf)
            except Exception:
                await self.session.move_to(old_leaf)
                raise
        self.agent = None
        new_leaf_id = summary_entry_id or entry_id
        return TreeNavigationResult(
            old_leaf_id=old_leaf,
            new_leaf_id=new_leaf_id,
            common_ancestor_id=divergence.common_ancestor_id,
            summary_entry_id=summary_entry_id,
        )

    async def _persist_message(self, event: AgentEvent) -> None:
        if event.type == "message_end" and event.message is not None:
            await self.session.append_message(event.message)

    async def _auto_compact(self) -> None:
        if self.summarizer is None:
            return
        messages = await self.session.build_context_messages()
        if should_compact(
            estimate_context_tokens(messages),
            self.config.model.context_window,
            self.compaction_settings,
        ):
            await self.compact()
