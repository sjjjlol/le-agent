# LeAgent 项目五维度总结：从架构到工程化

> 本文基于 [`zh-learning-guide.md`](zh-learning-guide.md) 与 LeAgent 当前源码整理，目标是形成一套适合简历、
> 项目介绍和技术面试的表达框架。文中只描述当前已经实现的能力，并明确尚未落地的部分。

## 1. 项目定位

LeAgent 是一个受 Pi 启发、使用 Python 实现的极简 Coding Agent。它不只是把用户输入发送给大模型，
而是构建了一个完整的 Agent Runtime：模型可以流式生成文本、思考和工具调用；Runtime 执行本地工具，
再把工具结果作为消息交还模型，直到任务完成。

```text
用户输入 → 模型流式响应 → 工具调用 → 本地执行
        ↑                         ↓
        └──── 工具结果回填 ←──────┘
```

项目的核心价值可以从五个角度概括：

1. 架构设计：如何隔离模型厂商、Agent 核心、Coding 应用和前端；
2. Agent Loop：如何让模型、工具和用户输入形成稳定的运行闭环；
3. 工具系统：如何把模型描述转换为可控的本地执行；
4. 记忆系统：如何持久化、恢复、分支和压缩长会话；
5. 可观测性与工程质量：如何让 Agent 可展示、可诊断、可测试、可评估。

这五个维度分别回答：系统如何扩展、如何运行、如何行动、如何记忆，以及如何保证质量。

## 2. 架构设计：分离可复用大脑与 Coding 产品

### 2.1 要解决的问题

模型 Provider、Agent 运行逻辑、Coding 工具和终端 UI 的变化原因并不相同。如果把它们写在一起，
更换模型、增加前端或修改会话策略都可能影响整个系统，也很难对 Agent 核心做确定性测试。

### 2.2 LeAgent 的设计

LeAgent 将系统拆为三个包：

| Module | 核心职责 | 隐藏的 Implementation |
| --- | --- | --- |
| `le_agent_ai` | 统一不同模型的流式通信 | HTTP、SSE、厂商 payload、重试、usage |
| `le_agent` | 提供可复用 Agent Harness | 消息、事件、工具协议、Loop、队列和取消 |
| `le_agent_coding` | 把通用 Harness 组装为 Coding Agent | cwd、文件工具、资源、技能、会话、CLI/TUI |

```text
CLI / Textual TUI
        ↓
le_agent_coding.CodingSession
        ↓
le_agent.AgentHarness → run_agent_loop
        ↓
ModelProvider Interface
        ↓
le_agent_ai Provider Adapter
```

关键设计是依赖倒置：`ModelProvider` Interface 定义在 `le_agent`，OpenAI-compatible、OpenAI Codex 与
Mistral 具体 Adapter 位于 `le_agent_ai`。Agent Loop 只消费统一的 `AssistantMessageEvent`，不认识厂商的
SDK、请求字段和 SSE 格式。

`AgentHarness` 与 `CodingSession` 也有明确分工：Harness 是纯内存、可复用的有状态 Agent 大脑；
CodingSession 在外层增加资源装配、持久化、上下文压缩和应用事件。Textual TUI 只是事件消费者，
不会成为 Agent 主控制器。

### 2.3 设计收益

- Provider、工具、存储和前端都形成了可替换 Seam；
- Agent Loop 的改动集中在核心 Module，不会扩散到具体 UI；
- FakeProvider、内存 Storage 和自定义 Tool 可以通过同一 Interface 注入测试；
- 同一 CodingSession 可以服务 print renderer、JSON 事件流和 Textual TUI。

### 2.4 面试表达

> 我把系统拆成模型适配层、通用 Agent 核心和 Coding 应用层。核心只依赖 Provider、Tool 和 Storage
> 等稳定 Interface，具体模型、文件工具和前端作为 Adapter 接入，因此新增 Provider 或替换 UI
> 不需要修改 Agent Loop。这种设计同时提升了复用性、变更局部性和可测试性。

## 3. Agent Loop：构建可控的模型—工具运行闭环

### 3.1 要解决的问题

Coding Agent 不是一次模型调用。模型可能连续请求多个工具，用户还可能在执行过程中改变方向、追加
任务或取消运行。因此 Runtime 必须明确消息回填、回合边界、终止条件和异常语义。

### 3.2 纯 Loop 与有状态 Harness

LeAgent 把运行控制拆成两个 Module：

- `run_agent_loop()` 是无持久状态的协调器，依赖都由参数和 callback 注入；
- `AgentHarness` 持有 transcript、运行状态、取消令牌以及 steering/follow-up 队列。

Loop 使用两层循环：内层完成“模型—工具—模型”的调用链，外层在当前任务即将结束时消费 follow-up。

```text
外层：整个 Agent run
  └─ 内层：模型响应 → 执行 ToolCall → 回填 ToolResult → 再次调用模型
  └─ steering：在回合边界尽快插入
  └─ follow-up：原任务自然结束后开启下一轮
```

一次典型运行包含以下步骤：

1. 将用户消息写入 transcript，并发出消息生命周期事件；
2. 调用 Provider，收集最终 `AssistantMessage`；
3. 按内容顺序执行其中的 ToolCall；
4. 将每个执行结果包装为 `ToolResultMessage` 回填 transcript；
5. 使用更新后的上下文再次调用 Provider；
6. 没有工具、steering 或 follow-up 后结束运行。

工具结果必须成为消息，而不能只显示在 UI。否则下一轮模型无法看到读取的文件、命令输出或工具错误，
也无法在恢复会话后重放完整推理上下文。

### 3.3 运行可靠性

- 同一 Harness 只允许一个 run 修改 transcript，避免重叠运行破坏顺序；
- cancellation token 显式传入 Provider 和 Tool，形成协作式取消；
- Provider 错误和取消会收敛为带 `error` 或 `aborted` stop reason 的 AssistantMessage；
- 普通工具异常转成错误 ToolResult，使模型有机会修正参数或选择其他方案；
- 中断后会为缺失结果的 ToolCall 补充错误 ToolResult，修复 Provider 所需的调用—结果配对。

### 3.4 当前能力边界

`AgentTool.execution_mode` 会参与批次调度。默认全局模式为 `parallel`；批次内任一已知工具为
`sequential` 时整批串行。并行 worker 的完成事件按实际时序发出，但结果按 ToolCall 源顺序回填 transcript。
当前是保守的整批 barrier，还没有按路径或资源冲突做局部并行。取消也是协作式的。

### 3.5 面试表达

> 我将无状态 Agent Loop 和有状态 Harness 分开：Loop 专注于模型调用、工具执行和结果回填，Harness
> 负责 transcript、单运行约束、取消以及 steering/follow-up 队列。难点不只是写一个 while 循环，
> 而是保证事件顺序、工具调用闭环、中途输入和异常恢复具有一致语义。

## 4. 工具系统：把模型意图转换为受控执行

### 4.1 要解决的问题

模型只会生成结构化的工具请求，本地 Runtime 必须负责参数约束、真实执行、进度反馈、异常隔离、
输出限制和结果回传。同时，通用 Agent 核心不能依赖文件系统或 shell 的具体策略。

### 4.2 两级 Tool Module

`le_agent.AgentTool` 是核心层 Interface，包含三类信息：

- 模型视图：`name`、`description`、JSON Schema；
- Runtime 视图：异步 executor、参数预处理、取消与进度 callback；
- 可选前端视图：调用和结果 renderer。

`le_agent_coding.ToolDefinition` 是 Coding 应用的构建形态，负责 cwd、参数检查、文件和 shell 行为等策略，
再通过 `to_agent_tool()` 适配为核心认识的 AgentTool。

```text
Coding ToolDefinition
  ├─ cwd / 参数校验 / 输出截断 / diff 元数据
  └─ to_agent_tool()
              ↓
AgentTool：Schema + Executor + Result
              ↓
Agent Loop
```

### 4.3 工具生命周期与扩展 Seam

一次工具调用会产生 start、可选 update、end 事件，随后产生最终 ToolResultMessage。`before_tool_call`
可以实现权限确认、阻断或审计，`after_tool_call` 可以脱敏、改写结果或调整错误标记。权限策略和扩展逻辑
因此不需要侵入 Agent Loop 或每一个工具实现。

LeAgent 当前默认内置 `read`、`write`、`edit`、`bash`、`web_search`：

- 相对路径绑定 CodingSession 的 cwd；
- read 支持文本和图片，并限制返回字节数与行数；
- write/edit 对同一路径使用进程内异步锁，避免本进程内写入交错；
- bash 支持取消并返回结构化执行信息；
- web_search 使用 Tavily，支持 freshness 和域名过滤，同时返回可读摘要与结构化来源；
- read/web_search 可并行，write/edit/bash 标记为串行；
- 工具错误以结果返回，不让普通输入错误终止整个 Agent 进程。

### 4.4 设计收益与边界

这套 Interface 让模型只看到稳定 Schema，Loop 只看到标准执行结果，而 cwd、文件格式、输出截断和
展示方式保持在 Coding Adapter 中。需要注意，Hooks 提供了实现权限系统的 Seam，但它本身不等于完整的
沙箱或授权模型；本地路径和 shell 的安全级别也不能被过度描述为系统级隔离。

### 4.5 面试表达

> 工具系统同时服务模型、Runtime 和前端，但我通过标准 AgentTool Interface 隐藏了 Coding 工具的
> cwd、校验和输出策略。工具结果统一回填 transcript，普通异常被隔离为模型可见的错误结果；
> before/after hooks 则为权限、审计和结果治理提供了扩展 Seam。

## 5. 记忆系统：历史不可变，上下文可投影

### 5.1 记忆的准确含义

LeAgent 的记忆系统是 Session Memory 与 Context Memory：它解决同一会话中的持久化、恢复、分支、回溯和
上下文压缩。它不是向量数据库、RAG，也不自动完成跨用户或跨会话的长期知识召回。

### 5.2 Append-only Session Tree

LeAgent 没有直接覆盖保存一个 messages 数组，而是把状态变化建模为追加式 `SessionEntry`。Message、模型
切换、thinking level、压缩摘要、分支摘要、标签和会话信息都拥有 `id + parent_id + timestamp`。

三个概念必须分开：

| 概念 | 含义 |
| --- | --- |
| Stored Entries | JSONL 中保存的全部历史和全部分支 |
| Active Leaf | 最新 LeafEntry 选中的当前目标节点 |
| Projected Context | 沿活动目标 parent 链重放得到的消息与配置 |

`parent_id` 定义历史结构，`LeafEntry.entry_id` 记录当前导航目标。用户返回旧节点重新提问时，只需从旧
节点追加新的 child，不会删除原分支。加载会话时，系统找到最新 LeafEntry，再通过 `path_to_entry()`
恢复 root-to-target 路径。

### 5.3 Context Projection 与 Compaction

`SessionState.from_entries()` 将活动路径投影为 Harness 所需的 messages、model、thinking level 等状态，
因此完整存储历史和本轮模型上下文可以拥有不同形态。

当上下文过长时，`CompactionEntry` 用摘要替换投影中的一组旧消息，但不会删除 JSONL 原始 Entry；
`BranchSummaryEntry` 则在回到旧节点时，把被放弃的当前分支摘要带入新分支。

```text
完整不可变历史
      ↓ 选择 Active Leaf
活动路径
      ↓ 应用 Compaction / Branch Summary
模型本轮看到的上下文
```

核心不变量是：

> 摘要改变模型下一轮看到什么，但不改写会话中已经发生过什么。

这使上下文压缩、分支回溯、审计和摘要重新生成可以同时成立。CodingSession 还同时提供阈值压缩和
context overflow 后的压缩重试，并限制自动重试次数，避免形成无限恢复循环。

### 5.4 复杂度与可靠性边界

恢复路径先以 `O(n)` 构建 id 索引，再用 `O(h)` 沿 parent 链回溯。Compaction 只缩短模型上下文，
不会回收 JSONL 存储空间。

当前 `SessionStorage` Interface 只有 `append()` 和 `read_all()`。JSONL Adapter 易于检查、迁移和测试，
但没有跨进程写锁、事务、`fsync` 或 torn-tail 自动修复。消息与 Leaf 是两次 append，崩溃窗口内可能
出现消息已经保存、活动指针尚未推进的情况。后续可以在 Storage Seam 下增加 `append_many()`、校验和、
快照与恢复日志，而不改变上层 Context Projection 语义。

### 5.5 面试表达

> 我把完整历史和模型上下文分开：JSONL 以 append-only Entry 保存全部分支，LeafEntry 选择活动目标，
> SessionState 再把活动路径投影为本轮上下文。Compaction 只修改投影而不删除历史，因此同时支持
> 长上下文控制、分支回溯和审计。这里的 memory 是会话级上下文管理，不是向量检索式长期记忆。

## 6. 可观测性与工程质量：让 Agent 可诊断、可测试、可评估

### 6.1 三层事件体系

Agent 的中间过程是产品体验和故障定位的一部分。LeAgent 将事件分为三层：

| 事件层 | 回答的问题 | 典型消费者 |
| --- | --- | --- |
| Assistant Stream Event | 模型正在生成什么 | Agent Loop |
| Agent Event | 当前 run、turn、message、tool 处于什么阶段 | Harness、Session、前端 |
| CodingSession Event | 应用是否还在压缩、重试或处理队列 | CLI、TUI、扩展 |

Provider Adapter 先产生私有事件，再由 canonicalizer 组装为统一、有序的 text、thinking 和 ToolCall
内容块。流式 delta 用于即时展示，最终 AssistantMessage 是内容顺序、usage、stop reason 和持久化的
权威事实。

`AgentEndEvent` 只代表核心 Loop 结束。CodingSession 后续可能还要压缩、自动重试或处理队列，因此
前端以 `AgentSettledEvent` 判断整个应用编排真正结束。`TuiEventAdapter` 把事件单向投影到 TuiState，
不会反向控制 Loop。

### 6.2 诊断与安全日志

`AgentCallDiagnosticLogger` 将异常和最终 Assistant Error 追加为结构化 JSONL，并记录 run、session、
provider、model、cwd 和 phase。对 Provider 错误只提取状态码、尝试次数和有限的分类字段，不把完整请求、
credential 或任意响应内容写入诊断日志。

这种设计让失败能够按一次 Agent call 关联和定位，同时缩小敏感信息泄露与日志无限膨胀的风险。

### 6.3 确定性测试与 Benchmark

LeAgent 的可替换 Seam 也是测试面：

- `FakeProvider` 重放预定义 Assistant 事件并记录调用，不需要真实网络；
- Fake Tool 可以确定性验证工具链、错误和 callback；
- 内存 SessionStorage 可以测试分支和重放，不依赖磁盘；
- TuiEventAdapter 可以直接用事件验证 UI 状态投影；
- pytest 覆盖 Provider 归一化、Agent Loop、Harness、工具、Session、CodingSession、CLI/TUI 等模块；
- Ruff、mypy strict 和 Python 3.12 约束静态质量。

项目还提供原生 Benchmark Runtime：每个 trial 复制隔离 workspace，复用 CodingSession 和工具运行真实
任务，收集 trajectory，执行确定性 evaluator，并汇总成功率、耗时、token/usage 等指标。这样评估的是
完整 Agent 行为，而不是只比较最终自然语言回答。

### 6.4 面试表达

> 我把运行过程建模为分层事件流：delta 服务实时 UI，最终消息服务持久化，session settled 服务应用
> 状态收敛。相同 Interface 还能注入 FakeProvider、Fake Tool 和内存 Storage 做确定性测试；结构化诊断
> 和隔离 Benchmark 则覆盖线上定位与效果评估，让 Agent 从“能跑”走向“可验证、可演进”。

## 7. 五个维度如何串成一次项目介绍

面试时不要把五部分讲成互不相关的功能清单，可以沿一次请求的数据流介绍：

```text
前端提交输入
  → CodingSession 装配 Coding 环境
  → AgentHarness 启动受控 run
  → Provider Adapter 输出统一流式事件
  → Agent Loop 执行 ToolCall 并回填 ToolResult
  → Session 追加 Message/Leaf Entry
  → Context Projection 恢复活动上下文
  → TUI、诊断和 Benchmark 消费事件与结果
```

推荐的两分钟版本：

> LeAgent 是一个 Python Coding Agent Runtime。我主要从五个维度理解它。第一，项目以 `le_agent_ai`、
> `le_agent`、`le_agent_coding` 三层分离模型适配、通用 Agent 大脑和 Coding 产品能力；第二，用无状态
> Agent Loop 加有状态 Harness 完成流式模型—工具闭环，并支持 steering、follow-up 和协作式取消；
> 第三，工具通过 Schema、Executor 和 Result 的统一协议接入，错误也会作为结果回填给模型；第四，
> 会话使用 append-only Session Tree 保存全部历史，通过 Active Leaf 和 Context Projection 支持分支恢复
> 与上下文压缩；第五，以分层事件、结构化诊断、Fake Adapter 和隔离 Benchmark 建立可观测、可测试、
> 可评估的工程闭环。整个设计的重点是把复杂行为放在清晰 Module 后面，让模型、工具、存储和前端都能
> 独立替换和演进。

## 8. 简历写法

### 8.1 项目名称与简介

**LeAgent｜Python Coding Agent Runtime｜个人项目 / 开源项目实践**

基于 Pi 极简 Agent Harness 思想构建的 Python Coding Agent，支持多模型流式接入、工具调用、分支会话、
上下文压缩及 CLI/TUI 交互，覆盖 Agent 从模型推理、本地执行到会话恢复的完整运行链路。

**技术栈：** Python 3.12、asyncio、Pydantic、HTTPX、Textual、Rich、Typer、pytest、Ruff、mypy

### 8.2 标准版：四条核心成果

- 设计 `le_agent_ai / le_agent / le_agent_coding` 三层 Coding Agent 架构，以 Provider、Tool、Storage Interface 隔离多模型通信、通用 Agent Runtime 与 Coding 应用，使模型适配器、CLI/TUI 前端和会话存储可独立替换。
- 实现事件驱动的异步 Agent Loop 与有状态 AgentHarness，打通模型流式输出、ToolCall 执行、ToolResult 回填和多轮推理闭环，并支持 Steering/Follow-up 队列、协作式取消及中断工具调用修复。
- 构建 Schema 驱动的工具系统，将模型可见描述与本地 Executor 解耦，统一参数处理、执行进度、异常隔离和结构化结果；通过 before/after Hooks 为权限校验、审计与结果治理预留扩展 Seam。
- 基于 append-only JSONL Session Tree 实现会话持久化、分支回溯与活动路径 Context Projection，通过 Compaction/Branch Summary 在保留原始历史的同时控制长上下文，并以分层事件、结构化诊断和 Fake Adapter 提升可观测性与可测试性。

### 8.3 一页简历精简版

- 搭建 AI/Agent/Coding 三层 Python Agent Runtime，通过统一 Provider 事件协议屏蔽 OpenAI-compatible、OpenAI Codex 与 Mistral 在流式响应、工具调用与错误处理上的差异。
- 实现异步模型—工具执行闭环，支持 ToolResult 回填、Steering/Follow-up、协作式取消、异常收敛及 CLI/Textual TUI 多前端事件消费。
- 设计 append-only Session Tree 与活动路径 Context Projection，支持分支恢复、历史审计和上下文压缩；结合 Fake Adapter 与结构化诊断提升 Agent 的可测试性和故障定位能力。

### 8.4 Agent Runtime / Infra 岗位版本

- 抽象 Provider-neutral Streaming Interface，将不同厂商的 HTTP/SSE 事件规范化为统一、有序的 text、thinking、ToolCall 内容流，使 Agent Loop 不依赖具体模型协议。
- 将纯函数式 Agent Loop 与有状态 Harness 分离，集中管理 transcript、回合终止、消息队列、取消和调用—结果一致性，便于使用 FakeProvider/Fake Tool 进行确定性测试。
- 以 append-only Entry、parent pointer 和 Active Leaf 建模可恢复会话树，将完整历史与模型上下文投影分离，并支持 Compaction、Branch Summary 和旧协议迁移。
- 建立 Provider/Agent/CodingSession 三层事件体系，统一实时 UI、会话持久化与错误诊断的观测入口。

### 8.5 Agent 应用开发岗位版本

- 构建支持 read/write/edit/bash/web_search 的 Coding Agent，完成串并行 ToolCall、结构化结果回填和错误自修正闭环，并对本地工具输出进行行数与字节数限制。
- 集成 Skills、Prompt Templates、项目上下文和 Extension Runtime，使工具、Slash Command、事件处理器及调用前后 Hooks 可按应用需求扩展。
- 基于统一 CodingSession Event 同时支持 print mode、JSON 事件流和 Textual TUI，实现流式文本、思考过程、工具卡片、队列、压缩与重试状态展示。
- 实现分支会话、上下文压缩和溢出后单次自动重试，提升长任务中的上下文连续性与交互可恢复性。

### 8.6 Memory / Context 方向版本

- 设计 append-only Session Entry 协议，将消息、模型配置、摘要、分支和活动指针统一建模，避免覆盖历史并支持会话审计与恢复。
- 通过 `parent_id + Active Leaf` 表达多分支历史，以 `O(n + h)` 的索引和路径回溯恢复当前活动上下文。
- 将 Stored History 与 Projected Context 分离，通过 Compaction 在投影中以摘要替换旧消息、保留近期上下文，同时保持原始 JSONL 历史不变。
- 区分 Branch Summary 与 Context Compaction：前者将被放弃分支的信息带入新路径，后者降低当前活动上下文的 token 占用。

### 8.7 面试表述的成果归属

如果 LeAgent 是自己实际参与实现或维护的项目，可以使用“设计、实现、构建”等动词，并准备对应 commit、
测试或设计文档作为证据。

如果主要工作是源码学习、复现或二次开发，应改成下面的表述：

- 深入分析 LeAgent 的三层 Agent 架构、事件驱动 Loop、工具协议和 Session Tree，并形成系统化源码学习与设计文档；
- 基于 LeAgent/Pi 架构思想复现或扩展相关 Module，明确说明自己实际实现的 Provider、Tool、Memory 或测试范围；
- 不将 LeAgent 已有但未亲自实现或验证成熟的能力直接表述为个人成果。

建议每条简历内容都能回答三个追问：为什么这样设计、关键数据如何流动、当前实现还有什么边界。

## 9. 高频追问与回答要点

### 9.1 为什么 Harness 和 CodingSession 都需要？

Harness 管理可复用的内存 Agent 状态和运行控制；CodingSession 管理 Coding 应用特有的资源、存储、
压缩和前端事件。把两者合并会让核心依赖 cwd、JSONL 和 UI 策略，降低非 Coding 场景的复用能力。

### 9.2 为什么工具结果必须进入 transcript？

因为模型下一轮需要读取工具输出，恢复会话也需要重放完整调用—结果关系。只在 UI 打印会造成模型状态
与用户看到的状态不一致。

### 9.3 为什么不直接保存 messages 数组？

会话还包含分支、模型切换、thinking level、摘要和标签。追加式 Entry 能统一表达这些状态变化，通过
parent 形成分支，并保留可审计历史；messages 只是当前活动路径的运行时投影。

### 9.4 为什么 delta 和最终消息都要保留？

delta 解决交互延迟，最终消息解决块边界、顺序、usage、停止原因和持久化权威性。只有 delta 会让恢复
过程重新猜测状态；只有最终消息则失去流式体验。

### 9.5 当前最值得继续演进的方向是什么？

1. 让 Agent Loop 真正读取 `execution_mode`，对只读工具并行、变更型工具按冲突域串行；
2. 为 SessionStorage 增加批量原子追加、尾行校验与周期快照；
3. 将 context overflow 从文本 marker 演进为统一的结构化错误分类；
4. 在现有 Benchmark 上增加多模型、多次 trial、成本—质量权衡和回归阈值。

## 10. 源码索引

| 主题 | 主要源码 |
| --- | --- |
| Provider Interface 与归一化 | `src/le_agent/provider.py`、`src/le_agent_ai/stream.py` |
| Agent Loop 与 Harness | `src/le_agent/loop.py`、`src/le_agent/harness.py` |
| 消息与事件 | `src/le_agent/messages.py`、`events.py`、`provider_events.py` |
| 工具 Interface 与实现 | `src/le_agent/tools.py`、`src/le_agent_coding/tools.py` |
| Session Tree 与投影 | `src/le_agent/session/entries.py`、`tree.py`、`memory.py` |
| CodingSession 编排 | `src/le_agent_coding/session.py`、`events.py`、`context_window.py` |
| 前端事件适配 | `src/le_agent_coding/tui/adapter.py`、`state.py` |
| 诊断与评估 | `src/le_agent_coding/diagnostics.py`、`src/le_agent_coding/benchmark/` |
| 核心测试 | `tests/test_le_agent_ai.py`、`test_agent_loop.py`、`test_agent_harness.py`、`test_session.py`、`test_coding_session.py` |
