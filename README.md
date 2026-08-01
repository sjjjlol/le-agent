# le-agent

`le-agent` 是一个用于学习和展示 agent 工程能力的 Python 3.12 coding agent：它保留了 pi 的小型事件驱动 Agent Loop、追加式 Session Tree、按当前分支投影 Context，以及“只追加、不改写历史”的 Compaction 语义；终端体验则采用 Claude Code 风格的 Textual 全屏界面。

它受 [badlogic/pi-mono](https://github.com/badlogic/pi-mono) 启发，特别参考其 Agent Loop、Session 与 Compaction 的架构思想（MIT）。本项目是独立的 Python 实现，不复制其 TypeScript 源码。

## 快速开始

需要 macOS、Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
export OPENAI_API_KEY=...
uv sync --all-packages --dev --no-editable
uv run le-agent
```

macOS 下建议保留 `--no-editable`：部分 Python 安装会忽略位于隐藏 `.venv` 中的 editable `.pth` 文件，
导致 console script 已生成但无法导入 `le_agent_cli`。

如果已有 `.venv` 曾安装过同版本的旧 workspace wheel，更新源码后可强制重建三个本地包：

```bash
uv sync --all-packages --dev --no-editable \
  --reinstall-package le-agent-ai \
  --reinstall-package le-agent-core \
  --reinstall-package le-agent-cli
```

默认模型是 `gpt-5.4-mini`。密钥只从环境变量读取，不会写入 TOML；切换到内置 Claude 配置前设置
`export ANTHROPIC_API_KEY=...`。缺少当前 provider 要求的环境变量时，TUI 会显示警告并继续启动，便于使用
不需要密钥的本地 OpenAI-compatible 代理。

`--model claude` 或 `--model openai/gpt-5.4` 可覆盖默认模型；`--permission readonly|confirm|trust`
控制工具权限。可用模式：

```bash
uv run le-agent "检查这个仓库"
uv run le-agent --print "解释当前架构"
uv run le-agent --json "列出测试"
uv run le-agent --continue "继续上次任务"
uv run le-agent --resume SESSION_ID "恢复指定会话"
```

内置模型包括 `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`、`gpt-5.5`、`gpt-5.4`、
`gpt-5.4-mini` 与 `claude`。如需增加代理或未知模型，可在项目 `.le-agent/config.toml` 或
`~/.le-agent/config.toml` 显式配置 provider、model ID、`context_window` 和能力；le-agent 不猜测未知模型的窗口。

## 架构

| 包 | 职责 |
| --- | --- |
| `le-agent-ai` | Pydantic canonical messages、事件流、模型注册与 OpenAI/Anthropic 适配器 |
| `le-agent-core` | 无状态 loop、有状态 `Agent`、Session Tree、Context 投影、Compaction、Harness |
| `le-agent-cli` | 四个 coding tools、权限、Skills、配置、JSONL/print 与 Textual TUI |

核心语义和设计边界见 [架构文档](docs/architecture.md)、[Memory 与 Compaction](docs/memory.md) 和 [安全边界](docs/safety.md)。SDK 使用方式见 [SDK 文档](docs/sdk.md)。

## TUI

默认 TUI 提供常驻紧凑 Logo、transcript、内联工具结果、composer、审批弹窗和状态栏。模型请求开始后会显示
`正在等待 · 1.8s · Esc 中止`，首个输出或错误到达后自动移除。

常用命令包括：

- `/new` 创建新 Session 并清屏；`/clear` 只清空界面，不改变 Session、Context、队列或工作区。
- `/resume` 与成功的 `/tree` 按目标分支 Context 重绘；有效 `/compact` 按压缩后的 Context 重绘。
- `/model` 使用 `↑/↓/Enter/Escape` 选择真实型号，不提供搜索框。
- `/session` 显示 Session 身份、entry 数和可点击 JSONL 文件链接；`/status` 只显示 Context token 占用。
- `/help`、`/settings`、`/skills`、`/skill`、`/name`、`/hotkeys` 与 `/quit` 提供其他控制。

`Escape` 中止运行、`Shift+Tab` 切换权限、`Ctrl+O` 展开工具输出。

Skills 来自 `~/.le-agent/skills/*/SKILL.md` 与项目 `.le-agent/skills/*/SKILL.md`；项目同名 Skill 覆盖用户级 Skill。系统提示仅列出目录，模型需要时通过 `read` 工具读取正文。

## 开发与验证

```bash
uv run ruff check .
uv run mypy packages
uv run pytest --cov
uv run coverage report --include="*/le_agent_core/*" --fail-under=90
```

真实 API smoke tests 是显式 opt-in 的：配置有效 key 后手动使用 `--print` 或 `--json` 运行；CI 不调用付费 API。
