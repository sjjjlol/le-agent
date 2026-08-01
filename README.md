# le-agent

`le-agent` 是一个用于学习和展示 agent 工程能力的 Python 3.12 coding agent：它保留了 pi 的小型事件驱动 Agent Loop、追加式 Session Tree、按当前分支投影 Context，以及“只追加、不改写历史”的 Compaction 语义；终端体验则采用 Claude Code 风格的 Textual 全屏界面。

它受 [badlogic/pi-mono](https://github.com/badlogic/pi-mono) 启发，特别参考其 Agent Loop、Session 与 Compaction 的架构思想（MIT）。本项目是独立的 Python 实现，不复制其 TypeScript 源码。

## 快速开始

需要 macOS、Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
cp config.example.toml .le-agent/config.toml
export ANTHROPIC_API_KEY=...
uv sync --all-packages --dev --no-editable
uv run le-agent
```

`--model claude` 覆盖默认模型；`--permission readonly|confirm|trust` 控制工具权限。可用模式：

```bash
uv run le-agent "检查这个仓库"
uv run le-agent --print "解释当前架构"
uv run le-agent --json "列出测试"
uv run le-agent --continue "继续上次任务"
uv run le-agent --resume SESSION_ID "恢复指定会话"
```

完整配置见 [config.example.toml](config.example.toml)。未知模型必须显式配置 `context_window` 和能力，le-agent 不猜测模型上下文窗口。

## 架构

| 包 | 职责 |
| --- | --- |
| `le-agent-ai` | Pydantic canonical messages、事件流、模型注册与 OpenAI/Anthropic 适配器 |
| `le-agent-core` | 无状态 loop、有状态 `Agent`、Session Tree、Context 投影、Compaction、Harness |
| `le-agent-cli` | 四个 coding tools、权限、Skills、配置、JSONL/print 与 Textual TUI |

核心语义和设计边界见 [架构文档](docs/architecture.md)、[Memory 与 Compaction](docs/memory.md) 和 [安全边界](docs/safety.md)。SDK 使用方式见 [SDK 文档](docs/sdk.md)。

## TUI

默认 TUI 提供 transcript、内联工具结果、composer、审批弹窗和状态栏。常用命令：`/help`、`/model`、`/tree`、`/compact`、`/skills`、`/status`、`/quit`。`Escape` 中止运行、`Shift+Tab` 切换权限、`Ctrl+O` 展开工具输出。

Skills 来自 `~/.le-agent/skills/*/SKILL.md` 与项目 `.le-agent/skills/*/SKILL.md`；项目同名 Skill 覆盖用户级 Skill。系统提示仅列出目录，模型需要时通过 `read` 工具读取正文。

## 开发与验证

```bash
uv run ruff check .
uv run mypy packages
uv run pytest --cov
```

真实 API smoke tests 是显式 opt-in 的：配置有效 key 后手动使用 `--print` 或 `--json` 运行；CI 不调用付费 API。
