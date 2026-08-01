# le-agent V2：TUI、命令与 Session Tree 回溯

本文描述 V2 的实际行为与验收方式。V2 保留 pi 的核心分层：Session 是追加式持久真源，AgentState 是可重建运行态；TUI、print 与 JSONL 模式共用同一个 AgentHarness。

## 启动与配置

要求 Python 3.12 与 uv。在仓库根目录执行：

```bash
uv sync --all-packages --dev --no-editable
export OPENAI_API_KEY=...
uv run le-agent
```

配置优先级为 CLI、环境变量、项目配置、用户配置、默认值。项目配置位于 `<workspace>/.le-agent/config.toml`，用户配置位于 `~/.le-agent/config.toml`；API key 只从 provider 配置指定的环境变量读取，绝不写入 TOML。默认模型是 `gpt-5.4-mini`；内置 OpenAI 型号还包括 `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`、`gpt-5.5` 与 `gpt-5.4`，并保留 `claude` 配置。未知模型必须显式配置 `provider`、`id`、`context_window` 与 `max_output_tokens`，不会猜测窗口大小。

缺少当前配置要求的 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY` 时，TUI 会显示一次警告但继续启动，以兼容无需密钥的本地代理。真实 API 验证应在启动 le-agent 的同一终端先导出相应变量。

常用启动参数：

```bash
uv run le-agent --model <name> --permission confirm
uv run le-agent --continue
uv run le-agent --resume <session-id>
uv run le-agent --no-session
uv run le-agent --print "检查项目并给出建议"
uv run le-agent --json "检查项目"
```

`--no-session` 使用内存 Session，因而 `/resume`、`/tree` 与 `/name` 会显示明确的禁用原因。

## V2 TUI

界面采用 C 方案：深色单列 transcript、常驻紧凑 Logo Header、原位更新的工具卡、紧凑状态栏与底部 composer。40 列终端的 Header 压缩为单行。状态栏的 context 占用优先采用最新 provider usage，并对 usage 之后的消息做保守估算。

每次供应商请求开始前，runtime 发出 `assistant_request_start`。TUI 随即显示 `正在等待 · 秒数 · Esc 中止`，不暴露模型名；首个 text、thinking、tool-call、error 或终态到达后移除。Provider 错误、中止和 worker 异常都会形成可读消息，不会留下空白 assistant。Escape 的取消会从 Agent consumer 逐层传递到 loop 与 provider producer，避免网络流在后台继续运行。

在 composer 输入 `/` 会立即显示完整命令表；继续输入会按命令名和中文描述过滤。可用键位：

- `↑` / `↓`：移动命令候选；`Tab`：补全；`Enter`：执行；`Escape`：关闭或取消。
- `Enter`：提交 steering；`Alt+Enter`：提交 follow-up；`Shift+Enter`：换行。
- `Alt+Up`：取回最后一条排队消息；`Escape`：中止生成或取消正在执行的压缩命令。
- `Ctrl+O`：展开或折叠所有工具卡；`Shift+Tab`：轮换 readonly、confirm、trust。
- `Ctrl+T`：打开会话树。

文本与 thinking delta 约每 33ms 合并刷新，但事件发送和 Session 持久化不节流。ToolCard 以 `tool_call_id` 为身份原位更新 running、success、error 或 aborted；Bash 完整输出写入 `~/.le-agent/logs/<session-id>/`，界面只显示受限尾部。

## 统一斜杠命令

- `/help`：命令列表。
- `/model [name]`：无参数时使用无搜索框列表，以 `↑/↓/Enter/Escape` 选择真实 model ID；也可传配置名、唯一 model ID 或 `provider/model-id`。切换保留当前 Session，并追加 `model_change`。
- `/settings`：选择权限模式。
- `/new`：新建 Session 并清空 transcript。
- `/clear`：仅清空当前 transcript，不改变 Session ID、leaf、entries、Context、队列或工作区。
- `/resume [session-id]`：搜索或恢复历史 Session。
- `/tree`：搜索、筛选、折叠、标记并回溯 Session Tree。
- `/compact [instructions]`：压缩 Context，可附带摘要聚焦要求；`Escape` 可取消。
- `/skills` 与 `/skill <name> [args]`：列出或显式调用 Skill；只向模型提供 `SKILL.md` 路径，由模型按需使用 read 读取，不注入完整正文。内置命令始终优先于同名 Skill。
- `/session`：显示 Session、模型、持久化模式、entry 数与 JSONL 绝对路径，并提供终端可点击链接；`--no-session` 明确显示“仅内存，无 JSONL 文件”。
- `/status`：只显示 `上下文：百分比 · used / window tokens`，与底部状态栏共用计算结果。
- `/name <name>`：设置 Session 名称。
- `/hotkeys`：显示快捷键；`/quit`：释放会话锁并退出。

## Session Tree 与 checkpoint

V2 Session header 的 `version` 为 2。每个 entry 都有不可变 `id` 与 `parent_id`，新 entry 作为当前指针的子节点追加。`Session.move_to()` 只移动进程内 leaf 指针，新代码不写 `leaf` entry；载入 V1 JSONL 时仍识别旧 `leaf` entry，并将其折算成初始指针，但不在树中展示。

因此 checkpoint 回溯是 pi 式指针语义：

1. 从旧 leaf 与目标节点分别回溯，计算共同祖先和被放弃分支。
2. 直接回溯只移动 leaf，不新增 entry，也不改写任何历史。
3. 带摘要回溯先在原 leaf 上生成被放弃分支摘要；摘要失败时指针与历史完全不变。
4. 摘要成功后移动到目标节点，并把 `branch_summary` 作为目标节点的子 entry 追加，`from_id` 指向旧 leaf。
5. AgentState 从新 leaf 投影出的 Context 重建，事件订阅重新绑定。

checkpoint **只回溯 Session 与模型 Context，不恢复工作区文件**。此前由 write、edit 或 bash 造成的磁盘变化仍保留；这是为了避免隐式覆盖用户文件和重复外部副作用。

直接回溯的 leaf 是进程内指针：回溯后生成的下一条完整 entry 会把新分支持久化；如果尚未追加任何 entry 就立即退出，重开时会从日志中最后追加的 entry 恢复。需要“回溯后立即退出仍固定该节点”时，应选择带摘要回溯，因为它会在目标节点下追加 `branch_summary`。这是“不再写 `leaf` entry”约束带来的明确边界。

`/tree` 默认显示 user、assistant、工具结果、compaction 与 branch summary，摘要形式如 `user: ...`、`assistant: ...`、`[read: pyproject.toml]`。当前路径优先排序，最近可见节点标记为 `◆ current`。五档筛选为默认、隐藏工具、仅用户、仅标签、全部；宽度达到 80 列时显示选中节点预览，40 列窄终端隐藏预览。

## Compaction 与错误恢复

Compaction 只追加 summary、`first_kept_entry_id`、`retained_tail` 和压缩前 usage，不删除历史。默认 `reserve_tokens=16384`、`keep_recent_tokens=20000`，再次压缩时把 previous summary 交给摘要器做增量更新。

Provider 错误统一写入 `AssistantMessage.error_code`：`context_overflow`、`rate_limit`、`timeout`、`authentication`、`permission`、`server_error`、`invalid_request`、`network`、`provider_error` 或 `aborted`。

只有 `context_overflow` 会自动恢复：错误 assistant entry 留在旧分支，指针退到错误前最后一个完整 entry，压缩后重试一次。摘要失败会回滚指针；第二次 overflow 以及其他错误都不会自动重放，从而避免重复工具副作用。

## 单写者与崩溃边界

macOS 上 JSONL Session 使用非阻塞 `flock`。同一 Session 同时只能有一个写者；`/new`、`/resume`、模型切换和退出会按所有权释放锁。最后一行若因崩溃而只写入一部分，恢复时忽略该行；更早的损坏会被视为日志错误。系统只恢复完整 message/tool-result 边界，不重放未完成的外部操作。

## 验收

```bash
uv run ruff check .
uv run mypy packages
uv run pytest --cov
uv run coverage report --include="*/le_agent_core/*" --fail-under=90
```

整体 branch coverage 阈值固化为 85%，核心包阈值为 90%。离线测试包含 OpenAI/Anthropic 流事件转换、overflow 恢复、单写者冲突、Runtime 替换、命令补全、工具卡、Tree 以及 40/80/120 列 Textual Pilot；不会访问付费 API。
