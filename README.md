# LeAgent

LeAgent is a readable, extensible Python coding agent for the terminal. It provides an
interactive Textual UI, one-shot print modes, durable append-only sessions, extensions,
skills, themes, exports, and a deterministic benchmark.

## Architecture

LeAgent is one distribution with three explicit Python layers:

```text
le_agent_coding  →  le_agent  ←  le_agent_ai
```

- `le_agent` is the portable core: canonical messages, tools, events, the agent loop,
  `AgentHarness`, and session primitives.
- `le_agent_ai` adapts OpenAI-compatible, OpenAI Responses, OpenAI Codex, and Mistral
  APIs to the core provider contract.
- `le_agent_coding` is the application layer: CLI/TUI, persistence, resources,
  extensions, provider configuration, rendering, and built-in tools.

The core does not depend on the CLI, Textual, Rich, local paths, or application config.
Providers and frontends meet through typed canonical events.

## Install and run

LeAgent requires Python 3.12 or newer.

```bash
uv tool install le-agent
le-agent --version
le-agent
```

For local development:

```bash
git clone https://github.com/sjjjlol/le-agent.git
cd le-agent
uv sync --dev
uv run le-agent --help
```

To use your local checkout from any project, install it as an isolated CLI tool:

```bash
uv tool install --editable /absolute/path/to/le-agent
# If le-agent is not on PATH, add the directory printed by:
uv tool dir --bin

cd /path/to/another-project
le-agent
# Or select a workspace explicitly:
le-agent --cwd /path/to/another-project
```

Editable installation reflects source edits without reinstalling. Keep the checkout in
place; rerun the installation with `--reinstall` after changing dependencies or moving
the checkout. The tool environment is separate from the target project's environment.
Model configuration remains under `~/.le-agent/`.

For an installable artifact independent of the source checkout:

```bash
uv build
uv tool install /absolute/path/to/le-agent/dist/le_agent-0.2.0-py3-none-any.whl
```

If replacing an existing installation, add `--reinstall`. The wheel requires Python
3.12+ and installation of its dependencies; it is not a standalone executable.
Rebuild and reinstall the wheel to pick up subsequent source changes.

Print mode is suitable for scripts:

```bash
le-agent -p "summarize this repository"
le-agent --cwd /path/to/project -p "find the CLI entry point"
```

Run `le-agent --help` for the complete CLI and `le-agent providers` for the effective
provider catalog. Runtime data is stored under `~/.le-agent/`.

## Providers

The built-in runtime supports:

- OpenAI-compatible Chat Completions and Responses APIs
- OpenAI Codex subscription authentication
- Mistral Conversations
- custom OpenAI-compatible endpoints defined in `~/.le-agent/catalog.toml`

Native Anthropic and Google provider implementations are intentionally not included.
Models exposed through an OpenAI-compatible gateway remain usable through that gateway.

## Tools and execution

The default tools are `read`, `write`, `edit`, `bash`, and `web_search`.

Tool calls from one assistant message form a batch. The default is parallel execution,
but if any known tool in the batch is marked sequential, the whole batch runs
sequentially. `write`, `edit`, and `bash` are sequential; `read` and `web_search` are
parallel. Parallel batches perform preflight and `before_tool_call` hooks in source
order, emit completion events as calls finish, and commit tool-result messages back to
the transcript in source order. `after_tool_call` is also fully supported. LeAgent does
not attach a permission policy to either hook.

`web_search` uses Tavily. Set `TAVILY_API_KEY` for predictable limits; without it the
official Tavily keyless mode is used on a best-effort basis. Disable registration with
`CodingSessionConfig(web_search_enabled=False)`.

## Extensions and sessions

Extensions may register tools, commands, hooks, renderers, and UI components. User
extensions live in `~/.le-agent/extensions/`; project extensions may live in
`.le-agent/extensions/`. A Python project can declare entries under
`[tool.le-agent].extensions`.

Sessions are append-only JSONL trees under `~/.le-agent/sessions/`. Compaction changes
the active context projection without rewriting history. This release is a deliberate
breaking upgrade and does not read the former le-agent session or configuration schema.

## Fully local Mem0 with Ollama

[`examples/mem0_ollama.py`](examples/mem0_ollama.py) configures Mem0 without a remote
API key. It uses `qwen3:1.7b` for memory extraction, `all-minilm:latest` for
384-dimensional embeddings, and embedded Qdrant for local persistence under
`.le-agent/mem0/`.

```bash
ollama pull qwen3:1.7b
ollama pull all-minilm
uv sync
uv run python examples/mem0_ollama.py
```

The Ollama URL and model names can be overridden with `OLLAMA_BASE_URL`,
`MEM0_LLM_MODEL`, and `MEM0_EMBED_MODEL`. The example also disables Mem0's anonymous
telemetry so the entire memory path stays local.

## Benchmark and development

```bash
uv run le-agent benchmark --provider fake
uv run python -m pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m mypy src
```


## Agent Debugger

在 TUI 输入 `/debug 任务描述`，可在独立 Git 工作区按工具批次保存检查点，分支干预、运行固定验收、比较差异，并在预览和确认后采用结果。支持保存失败重试及应用中断恢复；本地执行不是权限沙箱。

无需模型凭据的固定轨迹演示：`uv run python -m le_agent_coding.debugger.demo`。

详见 [使用与验证](docs/agent-debugger-usage.md)、[设计](docs/agent-debugger-design.md) 和 [面试话术](docs/agent-debugger-interview.md)。
