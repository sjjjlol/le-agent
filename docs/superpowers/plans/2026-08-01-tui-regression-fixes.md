# TUI Regression Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `/tree` markup crashes, stale status-bar content, and slash commands with arguments being replaced by completion.

**Architecture:** Preserve the current event-driven TUI. Render session-tree labels as Rich `Text` so user-controlled values are always literal, refresh shared chrome after every command/runtime event, and let the composer submit slash commands once arguments are present.

**Tech Stack:** Python 3.12, Textual, Rich, pytest, uv.

## Global Constraints

- Do not change Session Tree pointer or Memory semantics.
- Add a failing regression test before each production change.
- Keep status updates event-driven; do not add a polling timer.
- Run Ruff, mypy, targeted tests, and the complete test suite before completion.

---

### Task 1: Render tree rows as literal text

**Files:**
- Modify: `packages/le-agent-cli/src/le_agent_cli/ui/tree.py`
- Test: `packages/le-agent-cli/tests/test_tree_ui.py`

**Interfaces:**
- Consumes: `TreeRow.display: str`
- Produces: `Option(Text(row.display), id=row.entry_id)` without Rich markup interpretation

- [ ] **Step 1: Write the failing test**

Create a tool result whose bash command includes markup-significant brackets and `-E`, open `TreeNavigator`, and assert the modal renders without raising `MarkupError` and displays the command literally.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/le-agent-cli/tests/test_tree_ui.py::test_tree_navigator_treats_tool_summaries_as_literal_text -v`

Expected: FAIL with Rich `MarkupError`.

- [ ] **Step 3: Write minimal implementation**

Wrap every dynamic tree row label in `rich.text.Text` before passing it to `Option`:

```python
options.add_options(Option(Text(row.display), id=row.entry_id) for row in self._rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run the new test and all `test_tree_ui.py` tests; expect PASS.

### Task 2: Refresh status after commands and runtime events

**Files:**
- Modify: `packages/le-agent-cli/src/le_agent_cli/ui/app.py`
- Test: `packages/le-agent-cli/tests/test_tui.py`

**Interfaces:**
- Consumes: `LeAgentApp._sync_bundle_ui()` and `LeAgentApp._refresh_status()`
- Produces: synchronized Header and StatusBar after command-side bundle mutation

- [ ] **Step 1: Write the failing test**

Execute a command that mutates `bundle.model_name`, `policy.mode`, and Session identity, then assert `StatusBar.renderable.plain` reflects the new values immediately after `_execute_command()` returns.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/le-agent-cli/tests/test_tui.py::test_command_mutations_refresh_status_bar_immediately -v`

Expected: FAIL because the old status text remains.

- [ ] **Step 3: Write minimal implementation**

Call `_refresh_status()` from `_sync_bundle_ui()` after refreshing `BrandHeader`, making every successful command update both shared chrome widgets.

- [ ] **Step 4: Run test to verify it passes**

Run the new test and all `test_tui.py` tests; expect PASS.

### Task 3: Submit slash commands that contain arguments

**Files:**
- Modify: `packages/le-agent-cli/src/le_agent_cli/ui/composer.py`
- Test: `packages/le-agent-cli/tests/test_tui.py`

**Interfaces:**
- Consumes: `Composer._submit(delivery)` and command completion messages
- Produces: Enter submits `/name test01`; Enter still completes `/name` when completion is active

- [ ] **Step 1: Write the failing test**

Type `/name test01` into `Composer`, press Enter, and assert the emitted submission is exactly `('/name test01', 'steer')` and the composer clears.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/le-agent-cli/tests/test_tui.py::test_composer_submits_slash_command_with_arguments -v`

Expected: FAIL because no `Submitted` event is emitted.

- [ ] **Step 3: Write minimal implementation**

When Enter is pressed on a slash command, split the stripped text after the command name. If it contains whitespace and a non-empty argument, call `_submit('steer')`; otherwise post `CompletionRequested(execute_if_exact=True)`.

- [ ] **Step 4: Run test to verify it passes**

Run the new composer test and existing completion tests; expect PASS.

### Task 4: Full verification and commit

**Files:**
- Modify: only the files listed above and this plan

- [ ] **Step 1: Run static checks**

Run `uv run ruff check .` and `uv run mypy packages`; expect both to exit 0.

- [ ] **Step 2: Run complete tests**

Run `uv run pytest --cov`; expect all tests to pass and overall coverage to remain at least 85%.

- [ ] **Step 3: Verify core coverage**

Run `uv run coverage report --include='*/le_agent_core/*' --fail-under=90`; expect at least 90%.

- [ ] **Step 4: Commit scoped files**

Stage only the plan, three TUI implementation files, and two test files. Commit with the Chinese message `修复会话树与终端状态交互`.
