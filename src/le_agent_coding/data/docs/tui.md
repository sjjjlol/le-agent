# LeAgent TUI

LeAgent's full interactive interface uses Textual behind an adapter boundary. `le_agent` emits provider-neutral events; `le_agent_coding.tui` consumes and renders them.

For current behavior in a LeAgent checkout, read:

- `website/content/guides/tui.md`
- `website/content/reference/keybindings.md`
- `src/le_agent_coding/tui/`

Do not introduce Textual dependencies into `le_agent`. Keep reusable behavior in the harness/session layers and UI behavior in the adapter. Use Textual pilot tests and fake providers for deterministic interaction tests.
