# LeAgent 项目面试问答：从简历五条主线展开

> 本文根据 LeAgent 项目简历描述、项目源码与公开 Agent 岗位面经整理。公开面经只能反映有限样本中的
> 提问趋势，不能代表所有公司；具体技术答案以 LeAgent 当前实现和文末一手资料为准。

## 1. 简历基线

### 项目介绍

参考 Pi 的极简 Harness 思想，从零实现面向 Coding 场景的 Python Agent Runtime，支持多模型
流式接入、工具调用、分支会话、上下文压缩及 CLI/TUI 交互。

### 核心经历

1. 设计 `le_agent_ai / le_agent / le_agent_coding` 三层架构，以 Provider Interface 和分层
   Message/Event 协议隔离模型通信、Agent Runtime 与 Coding 应用；通过事件流统一模型增量输出、
   工具执行与会话编排，使 Provider、Tool 及 CLI/TUI 可独立替换和测试。
2. 实现异步 Agent Loop，打通 Streaming、ToolCall、ToolResult 与多轮推理闭环；支持
   Steering、Follow-up、Cancel 及 before/after hooks，并对同批工具调用进行可控串并行调度。
3. 设计 append-only JSONL Session Tree，以 `parent_id + leaf` 持久化会话分支，基于活动路径完成
   Context Projection；通过增量 Compaction、recent tail 保留和单次 Overflow Retry 支持长会话恢复。
4. 构建可扩展的 Tool/Skill 体系：以统一 Schema、Executor 和 Result 协议接入文件操作、Shell 与
   Web Search；从用户和项目目录发现 Skills，仅将元数据注入 System Prompt，在任务命中时按需加载正文。
5. 完善运行可靠性：实现 Provider Retry、协作式取消和工具异常隔离；为中断产生的悬空 ToolCall
   补齐 ToolResult，维持 ToolCall/ToolResult 配对不变量，使会话可恢复、可重放。

### 五条简历主线的追问地图

| 简历主线 | 必答问题 | 深挖问题 |
| --- | --- | --- |
| 三层架构与分层事件 | Q1–Q4、Q16–Q17 | Provider 归一化、Frontend 事件投影 |
| Agent Loop 与串并行 | Q9–Q12、Q18 | Hook 语义、取消和确定性回填 |
| Session Tree 与 Context | Q29–Q35、Q38 | Branch Summary、Overflow Retry、Storage 边界 |
| Tool/Skill 体系 | Q20–Q23、Q27–Q28C | 权限、Web Search、MCP |
| 可靠性与恢复 | Q13–Q15A、Q44、Q49 | 可观测性和生产化取舍 |

## 2. 面试趋势与准备优先级

近期公开面经中，与这份简历高度相关的提问主要集中在：

- 让候选人从一次请求出发讲清完整架构与数据流；
- Agent Loop 的终止条件、死循环防护、失败/中断和安全重试；
- Tool Schema、工具选择、参数错误、权限审批和多工具并发冲突；
- 短期上下文、摘要触发、原始历史保留以及长期记忆的边界；
- 单 Agent 与多 Agent 的选择，而不是默认使用复杂编排；
- SSE 流式输出、Python async、测试、诊断和生产可靠性；
- 简历中的每个设计为什么存在、替代方案是什么、当前还缺什么。

准备优先级建议：

| 优先级 | 内容 | 原因 |
| --- | --- | --- |
| P0 | 三层事件数据流、Loop、Session/Compaction、Tool/Skill | 五条简历主线，必然会被深挖 |
| P0 | 取消、Provider Retry、悬空 ToolCall 修复、串并行收敛 | 简历明确声称的可靠性机制 |
| P1 | 安全、测试、可观测性 | 用于判断项目是否达到工程级而非 Demo |
| P1 | Python asyncio、SSE、Pydantic/Schema | Agent 应用岗仍会考扎实的语言和后端基础 |
| P2 | RAG、长期记忆、MCP、多 Agent | LeAgent 当前不是这些方向，但面试常用来考扩展设计 |
| P2 | Transformer、Token、KV Cache、模型参数 | 检查候选人是否理解 Agent 所依赖的模型基础 |

## 3. 项目总览与架构设计

### Q1：请用两分钟介绍这个项目

**口述回答：**

LeAgent 参考 Pi 的极简 Harness 思想，是我从零实现的 Python Coding Agent Runtime。它和普通
聊天程序的区别是存在模型—工具闭环：模型可以读写文件、执行 Shell 和搜索网页，Runtime 将真实
结果回填后，模型再决定下一步。

架构上分为三层：`le_agent_ai` 屏蔽模型 Transport 差异，`le_agent` 定义 Message/Event、Tool、Loop 和
Harness，`le_agent_coding` 装配 Tool/Skill、Session、Context 与 CLI/TUI。三层事件流将模型增量输出、工具执行
和应用编排分开；因此 Frontend 只消费事件，不反向依赖 Core。

在运行上，CodingSession 装配 Harness，Harness 驱动 Loop。Loop 可并发执行安全的同批 ToolCall，但稳定地按源
顺序回填 ToolResultMessage。会话用 append-only Session Tree 保存分支，按 Active Leaf 投影模型上下文；Tool 扩展
行动能力，Skill 则按需补充任务知识。取消或恢复时，系统会修复未闭合的 ToolCall/ToolResult 对，保持后续 transcript
可重放。

项目的重点不是某个 Prompt，而是让完整 Coding Agent 在分层、并发、上下文与异常恢复上都有明确语义。

**可能追问：** 为什么不用 LangChain？三层是否过度设计？一次 read 工具调用经过哪些类型？

### Q2：为什么要拆成 `le_agent_ai / le_agent / le_agent_coding` 三层？

**口述回答：**

三个层次的变化原因不同：模型厂商协议会随 API 改变；Agent Loop 的消息和工具闭环相对稳定；Coding
应用会不断增加命令、技能、路径和 UI。如果混在一起，换一个 Provider 或增加一个前端都会影响核心循环。

拆分后，`le_agent` 只依赖 ModelProvider、AgentTool 等 Interface；具体 Provider 和 Coding Tool 作为
Adapter 接入。这样同一个 Harness 可以用于非 Coding 场景，CodingSession 也可以连接 print、JSON 或
Textual 前端。测试时则可以注入 FakeProvider、Fake Tool 和内存 Storage，不访问真实网络和磁盘。

**关键取舍：** 分层增加了 Adapter 和数据转换代码，但换来了更强的变更局部性与确定性测试能力。

### Q2A：“分层 Message/Event 协议”具体分了哪些层？

**口述回答：**

我把“可重放的事实”和“运行中的过程”分开。Message 保存 User、Assistant 和 ToolResult 这些需要持久化、
重放给模型的事实；Event 只表示运行过程。Event 又分三层：

1. Provider/Assistant 流事件描述 text、thinking 和 tool-call block 正在如何生成；
2. Agent Event 描述 run、turn、message 和 tool execution 的生命周期；
3. CodingSession Event 补充 queue、compaction、auto retry 和 settled 等应用编排语义。

这样 Provider Adapter 只产生统一流，Loop 只关心 Agent 生命周期，CLI/TUI 和 Extension 只消费事件。流式 delta 可以丢，最终
Message 才是持久化与恢复的权威数据。代价是事件类型较多，必须守住 start/update/end 和终态收敛的协议。

### Q3：为什么 `ModelProvider` Interface 定义在 `le_agent`，具体实现却在 `le_agent_ai`？

**口述回答：**

因为 Interface 应由调用方需要的能力决定。Agent Loop 只需要
`stream_response(model, system, messages, tools, signal)` 返回统一异步事件流，所以协议属于
`le_agent`。`le_agent_ai` 的 OpenAI-compatible、OpenAI Codex 和 Mistral Adapter 反过来实现它。这是依赖倒置：核心不导入具体厂商，
新增 Provider 只需要完成消息转换、流解析和事件规范化，不修改 Loop。

### Q4：如果新增一家模型 Provider，需要实现哪些部分？

**口述回答：**

我会先完成 LeAgent Message、Tool 到厂商 payload 的序列化，然后实现 HTTP/流式 transport 和厂商事件解析，
最后将私有 ProviderEvent 送入 canonicalizer，输出统一的 AssistantMessageEvent。还需要映射 finish reason、
usage、thinking/tool-call replay metadata、错误与可重试条件，并用预制流测试 text、thinking、ToolCall、
终态缺失、取消和错误场景。Provider 选择和 credential 生命周期属于 `le_agent_coding` 的 runtime 装配，不放进
Agent Loop。

### Q5：为什么不直接用 LangChain/LangGraph？

**口述回答：**

这个项目的目标之一是理解并控制最小 Agent Runtime，因此自己实现消息、事件、Loop 和 Session 原语，
能够明确事件顺序、错误语义、持久化格式和依赖方向。LangGraph 更适合需要图状态机、节点编排和成熟生态
集成的业务系统；LeAgent 更适合希望保持小核心、可读源码以及自己掌握运行语义的场景。生产选型不应以“是否
自己造轮子”为目标，而要比较团队熟悉度、调试能力、扩展需求、依赖成本和交付速度。

### Q6：LeAgent 是 ReAct Agent 吗？

**口述回答：**

它具备 ReAct 的核心闭环：模型根据上下文决定行动，Runtime 执行工具，将 observation 作为
ToolResultMessage 回填，再让模型继续推理。但 LeAgent 不要求把 Thought 以文本形式暴露，也不依赖固定的
“Thought/Action/Observation”字符串模板，而是使用 Provider 的结构化 text、thinking 和 ToolCall 内容块。
因此更准确的说法是 Pi 风格的 tool-using Agent Loop，而不是绑定某一种 Prompt 模板的 ReAct 实现。

### Q7：什么时候选择 Workflow，什么时候选择 Agent？

**口述回答：**

流程稳定、分支有限、正确性要求高时优先用代码编排的 Workflow，因为它更确定、易测试、成本可控；任务
开放、步骤无法预先枚举、需要根据环境反馈动态决定下一步时才使用 Agent。也可以混合：代码负责权限、
状态转换和关键业务流程，模型只负责分类、生成候选计划或在受限工具集合里选择下一步。自主性不是越高
越好，应与动作风险和任务不确定性匹配。

### Q8：什么时候需要多 Agent？LeAgent 为什么以单 Agent 为核心？

**口述回答：**

只有当任务确实存在可独立并行的子问题、需要不同权限或上下文隔离、或者不同角色有明确专业 Interface
时，多 Agent 才能带来收益。否则它会引入通信协议、共享状态、一致性、成本和故障传播问题。LeAgent 先把单
Agent 的 Loop、工具、记忆和恢复做好，这也是更小、更可靠的基线。若扩展多 Agent，我会优先把子 Agent
作为有 Schema 的 Tool，由主 Agent 保持最终控制；需要完全移交会话所有权时再使用 handoff。

## 4. Agent Loop 与运行控制

### Q9：一次用户请求如何完整运行？

**口述回答：**

CodingSession 先加载 Tool/Skill 与项目资源，展开显式 Skill/Template 命令，并检查队列与压缩条件，再调用
Harness。Harness 将 transcript、Provider、Tools 和 callback 交给 `run_agent_loop()`。Loop 追加 UserMessage，
流式收敛出最终 AssistantMessage。

如果 AssistantMessage 包含 ToolCall，Loop 先决定整批串行或并行；并行路径按源顺序做 preflight 和
before hook，worker 的 update/end 可按实际完成顺序发出，结果则按源 ToolCall 顺序追加为
ToolResultMessage。模型看到结果后进入下一轮。工具链结束后处理 follow-up，CodingSession 增量持久化最终消息；
只有压缩、重试和队列都处理完，才发出 AgentSettledEvent 让 UI 退出 running 状态。

### Q10：为什么要把 Loop 和 Harness 分开？

**口述回答：**

Loop 是控制流，最好显式接收所有依赖，通过事件返回过程，这样可以用一个消息列表、FakeProvider 和
FakeTool 直接测试。Harness 则拥有跨调用的 transcript、运行标志、取消令牌和消息队列。两者分开后，
Loop 不需要知道持久化和 UI，Harness 也不重新实现模型—工具算法。这是“纯协调器 + 有状态外壳”的设计。

### Q11：Agent Loop 如何判断结束？怎样防止死循环？

**口述回答：**

正常结束条件是 AssistantMessage 没有 ToolCall，并且 steering 与 follow-up 队列都为空。异常终止包括
Provider error、取消以及超过 `max_turns`。LeAgent 当前有明确的最大模型回合限制，但还可以继续增加总耗时、
token/成本、连续相同 ToolCall、相同参数重试次数以及任务级 deadline 等预算。对于有副作用工具，不能在
不确认幂等性的情况下自动重试。

**不要只回答：** “设置一个 while 和最大次数”。面试官关心的是不同预算、重复模式检测与安全停止。

### Q12：Steering 和 Follow-up 有什么区别？

**口述回答：**

Steering 用于修正正在进行的任务方向，在一个模型—工具回合结束后尽快插入当前 run；Follow-up 则在
原工具链自然空闲、run 原本即将结束时，再开启下一轮输入。两者使用不同 deque，避免“立即纠偏”和
“做完后再问”语义混在一起。队列还支持一次取一条或一次排空，前者让模型有机会逐条响应。

### Q13：取消是如何实现的？能保证立即停止吗？

**口述回答：**

Harness 创建协作式 cancellation token，并将它传给 Provider、重试等待和 Tool。调用 `cancel()` 只是
设置状态，具体实现必须主动检查；`asyncio.CancelledError` 也与普通工具异常区分处理。因此它不是操作
系统级强杀，不能保证不检查 token 的第三方调用立即退出。生产中还应配合 HTTP timeout、子进程终止、
task cancellation 和资源清理，并确保取消后补齐未完成 ToolCall 的错误 ToolResult。

### Q14：为什么中断的 ToolCall 必须修复？

**口述回答：**

许多 Provider 要求每个已经出现在 AssistantMessage 中的 ToolCall，都有同 id 的 ToolResult。进程取消
或从旧会话恢复时如果缺失 result，下一次请求可能被 Provider 拒绝。Harness 会扫描 transcript，为未闭合
调用补一条 `is_error=True` 的 synthetic ToolResultMessage；CodingSession 在加载时还会持久化修复结果。
这是维护协议结构不变量，不是伪造工具成功。

### Q15：Provider、工具和 Hook 抛异常时分别怎么处理？

**口述回答：**

Provider 流内的可预期错误规范化为 AssistantErrorEvent，最终成为 error AssistantMessage；普通工具异常
转成错误 AgentToolResult，让模型可以读取错误后调整；`CancelledError` 保留取消语义并继续抛出。当前
before/after callback 自身抛异常不会由工具异常隔离逻辑吞掉，因此权限或审计 Hook 必须可靠；生产中我会
为 Hook 增加明确的 fail-open/fail-closed 策略和诊断。

### Q15A：简历里说的“运行可靠性”到底保证了什么？

**口述回答：**

我没有把它表述成“生产级高可用”，而是围绕 Agent Runtime 的四个失败边界定义可恢复语义：

1. Provider 在尚未输出内容时遇到可重试的瞬时故障，才做有上限、可取消的退避重试；
2. 普通工具异常隔离为 `is_error=True` 的 ToolResult，让模型有机会修正，而不终止整个进程；
3. 取消保留协作式语义，不把 CancelledError 伪装成普通工具失败；
4. 如果中断或旧会话留下只有 ToolCall、没有 ToolResult 的悬空调用，恢复前补齐同 id 的错误结果，
   维持 Provider transcript 的调用—结果配对不变量。

这些机制保证的是协议结构可闭合、会话可继续重放，不是外部副作用 exactly-once。例如 Shell 在取消前可能已经修改文件，
补一条错误 ToolResult 只修复 transcript，不会自动回滚环境。生产化还需要幂等键、状态查询、补偿、权限和沙箱。

### Q16：流式输出为什么需要 start/update/end 三类事件？

**口述回答：**

Start 建立临时 UI 状态，Update 携带 text/thinking/tool-call delta 保证低首字延迟，End 携带最终权威
Message，确定内容块顺序、usage、stop reason 和持久化内容。如果只存 delta，恢复时要重新猜测块边界；
如果只发最终消息，用户会等待完整生成。TUI 会用最终消息替换临时 buffer，避免流式状态成为事实来源。

### Q17：为什么 AgentEnd 之后还需要 AgentSettled？

**口述回答：**

AgentEnd 只说明这一次 Harness Loop 结束，但 CodingSession 可能还要处理 context overflow 压缩、压缩后
重试、阈值压缩或排队输入。若 UI 在 AgentEnd 就允许新 run，可能与这些应用级编排重叠。AgentSettled
代表 Session 的全部后续工作都已安定，是交互前端真正退出 loading 的信号。

### Q18：工具调用如何安全并行？

**口述回答：**

当前实现已采用两阶段批处理，避免多个协程直接同时修改 transcript：

1. **先决定整批模式。** 若运行配置要求串行，或本批任一个工具的 `execution_mode == "sequential"`，
   整个 batch 都串行；否则才进入并行路径。这是保守 barrier：例如一个 `edit` 与两个 `read` 同批出现时，
   不让 read 看到写入前后的不确定状态。
2. **按 ToolCall 原顺序做 preflight。** 依次查找工具、准备/校验参数、执行 before hook/权限审批；找不到工具、
   校验失败或被拒绝的调用立即得到错误结果，不进入并发执行。这样审批顺序、审计和拒绝语义仍是确定的。
3. **只并发执行已批准的调用。** 实现用受控的 `asyncio.create_task` 集合和事件队列启动 worker；每个任务持有自己的 call id、进度回调和取消 token。`tool_execution_update/end` 可以按
   实际完成顺序到达 UI，降低慢工具阻塞快工具结果的等待。
4. **稳定地收敛结果。** 等待整批任务结束后，按 assistant 原始 ToolCall 顺序创建并追加
   `ToolResultMessage`，再请求下一轮模型；不要按完成顺序写 transcript。Pi 也是“执行完成事件可按完成顺序
   发出、结果消息按源 ToolCall 顺序回填”，从而同时保证并发效率和 Provider replay 的确定性。
5. **处理取消、失败和终止。** 取消时向整批任务广播 token、等待清理，并为每个未完成调用补齐错误
   `ToolResultMessage`；单工具异常隔离为该调用的 `is_error=True` 结果。若有 `terminate` 语义，应在整批
   收敛后统一判断，不能让一个协程直接破坏其他任务的收尾。

[Pi 的默认策略](https://github.com/badlogic/pi-mono/blob/3a0b9a3eeee280f750edb3b0fc8a1b9093e88137/packages/agent/src/agent-loop.ts#L411-L554)
只用 `sequential/parallel` 做整批门控；LeAgent 面向 coding 工具还应进一步增加副作用和资源冲突元数据（如
`read/write`、规范化文件路径、全局 shell/网络资源），让不冲突的读操作并发、相同资源的写操作串行或加锁。
当前保守 barrier 以工具元数据决定整批串并行；未来才可用路径/资源冲突域细化。

**直白例子：** 模型在同一条 AssistantMessage 中先后请求 `read(a.py)`、`read(b.py)`。两者都是只读且没有
共享副作用，preflight 通过后可以同时开始：假设 `read(b.py)` 先在 100ms 完成，UI 可以先显示它的完成事件；
`read(a.py)` 在 500ms 完成。等两者都结束后，transcript 仍固定追加 `ToolResult(a.py)`、
`ToolResult(b.py)`，与模型最初的调用顺序一致，所以下一轮模型看到的上下文可重放、可测试。

如果同一批变成 `read(a.py)`、`edit(a.py)`、`read(b.py)`，且 `edit` 标为 `sequential`，Pi 风格的保守策略会让
**三者全部串行**：先读 `a.py`，再修改 `a.py`，最后读 `b.py`。这牺牲了一点吞吐量，却避免“一个 read 恰好读到
编辑前还是编辑后版本”的竞态。LeAgent 未来若增加文件路径冲突元数据，可以进一步优化为：`read(b.py)` 与前两个
操作并行，但同一路径 `a.py` 上的 read/edit 保持有序。

### Q19：Python asyncio 在这个项目中容易出现哪些问题？

**口述回答：**

主要风险包括：在 async 函数中执行阻塞文件或 subprocess 操作导致事件循环卡顿；取消没有继续传播；
创建 task 后未等待造成异常丢失；多个协程同时修改 transcript 或文件；listener 执行过慢反压整个事件流；
持锁期间 await 导致锁范围过大。LeAgent 用单 run 约束 transcript 修改，文件 mutation 使用进程内锁，Provider
和 Tool 使用显式 cancellation token。进一步可将阻塞 I/O 放入线程或使用异步实现，并用结构化并发管理
task 生命周期。

## 5. Tool/Skill 体系

### Q20：模型是怎样决定调用哪个工具的？

**口述回答：**

Runtime 将工具的名称、自然语言描述和 JSON Schema 与消息一起发送给模型，模型根据当前目标和描述生成
结构化 ToolCall。Runtime 不应完全相信模型：它仍需检查工具是否存在、解析和验证参数、执行权限策略，
并将真实环境结果回填。工具描述要清晰区分适用场景和副作用，避免多个工具语义重叠导致选择不稳定。

### Q21：为什么使用 JSON Schema？Schema 能保证什么、不能保证什么？

**口述回答：**

JSON Schema 给模型提供结构化参数约束，也让 Provider 能使用 function calling，并便于不同工具共用统一
Interface。它能表达字段类型、required、enum 和基本约束，但不能保证业务语义、安全性和资源存在性。
例如 path 是字符串不代表允许访问，command 合法 JSON 也不代表安全执行。因此还需要应用层校验、路径
策略、权限审批和执行时错误处理。

### Q22：为什么要同时有 ToolDefinition 和 AgentTool？

**口述回答：**

AgentTool 是核心 Interface，只保留模型 Schema、标准 Executor、结果和可选 renderer；ToolDefinition
是 Coding 层的构建形态，可以携带 cwd、参数模型、路径规则、prompt guideline、输出截断和 diff 元数据。
`to_agent_tool()` 是 Adapter Seam。这样核心 Loop 不会因为新增文件工具策略而变化，Coding 工具也不必
直接暴露所有实现细节。

### Q23：如果模型调用不存在的工具或传入错误参数怎么办？

**口述回答：**

Loop 先通过 name 索引工具。不存在或执行失败时应生成与原 ToolCall id 对应的错误 ToolResultMessage，
把可修正信息交给模型，而不是让整个 Python 进程退出。参数错误也在 Coding Tool Adapter 中校验并转换为
结构化错误。还要限制错误内容，避免把凭证、堆栈或超大响应直接送入模型。

### Q24：before/after Hooks 可以做什么？

**口述回答：**

before hook 在执行前接收完整 ToolCall，可以做 deny-by-default 权限检查、用户确认、配额和审计；阻止时
仍应生成对应错误 result。after hook 接收工具结果，可以脱敏、截断、补充审计信息或调整错误标记。Hook
位于 Loop 和工具之间，因此策略能统一作用于内置与扩展工具，而不必改每个 executor。

### Q25：如何设计工具权限和安全边界？

**口述回答：**

我会按能力和风险分层：只读工具可默认允许；文件修改限定 workspace 并展示 diff；shell、网络、外部消息
和不可逆动作需要显式批准；高风险工具在沙箱或最小权限身份中运行。还要防 prompt injection：网页或文件
内容是不可信数据，不能改变系统权限；工具结果要标记来源，credential 不进入模型上下文；审批应绑定具体
工具、参数和作用域，避免一次批准被复用为更大权限。

LeAgent 当前通过 cwd、参数检查和 before/after Hook 提供实现这些策略的 Seam，但不是完整安全沙箱。面试时
应明确“已实现”和“生产化方案”的区别。

### Q26：ToolCall/message 的 id 如何生成？工具失败后如何重试？

**口述回答：**

`ToolCall.id` 通常由 Provider 在响应中返回，LeAgent 的 Provider adapter 原样保留，用它关联同一轮的
`ToolResultMessage.tool_call_id`；如果 Provider 没返回，则按当前流内索引回退生成
`tool-call-{index}`。LeAgent 的 `UserMessage`、`AssistantMessage` 和 `ToolResultMessage` 本身没有通用
`message_id`；只有持久化外层的 `MessageEntry.id`，由 `uuid4().hex` 生成。`response_id`（如果有）是
Provider 的响应标识，不是 LeAgent 自己生成的消息 id。

当前“工具失败”不会由工具层自动重跑：工具异常、找不到工具、取消或 Hook 拦截都会被包装成
`is_error=True` 的 `ToolResultMessage`，沿原 `tool_call_id` 回填 transcript，由模型决定是否换参数或
再次发起新的 ToolCall。因此暂不维护 tool attempt，也不对有副作用的写入、支付、发消息等操作盲目重试。
需要重试时，应先确认幂等性，并配合 idempotency key、状态查询或补偿事务。

要区分 Provider 请求重试：各 adapter 对尚未产生内容的网络错误和瞬态 HTTP 错误（408/409/425/429/5xx）
按 `max_retries` 重试，默认最多 2 次，退避为 `0.25s * 2^attempt` 并受上限约束；已产生流式内容后不重试，
等待期间也可被取消。另有一次性的 context-overflow 压缩后重试，但这也不是工具重试。

### Q27：工具数量很多、Schema 占用上下文怎么办？

**口述回答：**

可以先按场景或权限做静态裁剪，再通过命名空间、工具搜索或渐进式披露，只向模型加载少量候选工具的完整
Schema。还可以缩短重复描述、缓存稳定前缀，并监控“工具 Schema token / 总输入 token”比例。但路由器
也可能错过正确工具，所以要提供 fallback 搜索，并用真实任务验证召回率。MCP 解决的是外部工具连接与
发现协议，不会自动解决工具选择质量、权限和 token 成本。

### Q28：MCP 和 LeAgent 的 AgentTool 有什么关系？

**口述回答：**

AgentTool 是 LeAgent Runtime 内部的统一工具 Interface；MCP 是跨进程、跨产品连接工具与上下文能力的开放
协议。接入 MCP 时，可以将 MCP server 暴露的 tool schema 和调用结果适配成 AgentTool，Loop 仍不需要
认识 MCP transport。MCP 降低连接成本，但调用方仍要做 server 信任、权限、schema 校验、超时、审计和
prompt injection 防护。

### Q28A：`web_search` 是怎样接入的？

**口述回答：**

`le_agent_coding.web_search` 定义 provider-neutral 的 `WebSearchBackend` 协议和 Tavily 适配器，
再通过 `ToolDefinition.to_agent_tool()` 进入通用 Loop。工具返回给模型的可读摘要，同时在
`details` 中保留 URL、发布时间、相关度和 request id，方便引用与诊断。它默认可并行，
支持 API key 和 keyless best-effort，也能通过 `web_search_enabled=False` 在 Session 装配时关闭。

边界是：搜索摘要不是页面全文，结果可能过时或不准确，不应把工具成功等同于事实已验证。

### Q28B：Tool 和 Skill 的职责有什么不同？

**口述回答：**

Tool 扩展 Agent 的**行动能力**：它有模型可见的 name/description/Schema 和本地 Executor，执行后必须产生
ToolResult 回填 transcript。Skill 扩展 Agent 的**任务知识**：它是带 name、description 和正文的 `SKILL.md`，
告诉模型在某类任务中应遵循什么流程、查看哪些资源。Skill 本身不产生外部副作用，它可以指导模型如何
选择和组合 Tool。

因此两者不是两套重复的插件系统：Tool 解决“能做什么”，Skill 解决“这类任务怎样做”。权限、取消和结果
治理属于 Tool Runtime；发现、优先级和上下文占用属于 Skill Resource System。

### Q28C：Skill 怎样发现和按需加载？为什么不直接注入全文？

**口述回答：**

LeAgent 按优先级加载用户和项目资源：`~/.le-agent/skills`、`~/.agents/skills`、
`<cwd>/.le-agent/skills`、`<cwd>/.agents/skills`，同名 Skill 由更高优先级的项覆盖。系统提示词只放每个 Skill 的
name、description 和 path，让模型在描述命中当前任务时再读取对应 `SKILL.md`；用户也可以用 `/skill:<name>`
显式展开内容。

这是 Progressive Disclosure：如果启动时注入所有正文，Skill 越多，System Prompt 越长，无关指令也会互相竞争。
只注入索引可以降低稳定上下文开销，代价是模型可能漏掉本应加载的 Skill，所以 description 必须准确，显式命令作为
fallback，并应通过真实任务检查 Skill 召回率与最终成功率。

## 6. 记忆、会话与上下文

### Q29：LeAgent 的“记忆系统”具体指什么？

**口述回答：**

这里指 Session Memory 和 Context Memory：完整会话怎样持久化、恢复和分支，以及有限 context window
中模型当前看到哪些消息。LeAgent 当前没有向量数据库、用户画像或跨会话语义召回，所以不能把它描述为长期
记忆系统。这个区分很重要：transcript 存储解决“不丢历史”，context projection 解决“本轮看什么”，
retrieval memory 才解决“从大量历史中按需找什么”。

### Q30：为什么用 append-only Entry，而不是直接保存 messages 数组？

**口述回答：**

会话除了消息，还有模型切换、thinking level、标签、压缩、分支摘要和活动位置。覆盖 messages 数组会
丢失这些变化的历史，也难以表达分支。LeAgent 将所有变化建模为带 `id/parent_id/timestamp` 的 Entry，旧节点
不修改，新分支只需指向旧 parent。运行时 messages 是指定活动路径重放后的投影，而不是唯一事实来源。

### Q31：`parent_id` 和 `LeafEntry.entry_id` 有什么区别？

**口述回答：**

`parent_id` 是历史结构指针，决定一个 Entry 从哪个节点继续；`LeafEntry.entry_id` 是导航指针，选择当前
要重放到哪个目标。LeafEntry 自身不是一条对话消息，后续消息也接在它指向的 target 上，而不是接在
LeafEntry.id 上。这样“历史结构”和“当前选择”都能通过 append 记录，又不会让导航事件污染模型上下文。

### Q32：会话恢复的完整流程是什么？

**口述回答：**

Storage 读取全部 Entry，CodingSession 找到存储顺序中最后一个 LeafEntry，取其 entry_id；
`path_to_entry()` 先构建 id 索引，再从 target 沿 parent 反向回溯并检测重复 id、缺失节点和环；反转为
root-to-target 路径后，`SessionState.from_entries()` 投影 messages、model、thinking level 和 context ids，
最后同步给 Harness。复杂度是 `O(n + h)`，其中 n 是全部 Entry 数，h 是活动路径高度。

### Q33：Context Projection 的价值是什么？

**口述回答：**

它把“数据库里发生过什么”和“模型这轮应该看到什么”分开。JSONL 可以保留全部分支和审计历史，模型只
接收 Active Leaf 对应路径以及压缩后的上下文；Harness 也不必成为第二套会话数据库。分支切换、压缩和
模型配置恢复都集中在投影 Module 中，减少调用方对 parent 回溯和摘要替换细节的了解。

### Q34：什么时候触发 Compaction？如何选择被压缩内容？

**口述回答：**

LeAgent 有三类触发：用户手动、达到自动 token 阈值、Provider 返回 context overflow。规划时选择活动路径中
较旧的一段消息生成摘要，并保留近期上下文；摘要以 CompactionEntry 追加，记录
`replaces_entry_ids`，随后写新的 LeafEntry。重放时只在投影中用摘要替换这些旧消息，原始 Entry 不删除。
overflow 压缩成功后只做一次应用级 retry，防止无限恢复循环。

### Q35：摘要应该全量生成还是增量更新？怎样防止信息丢失？

**口述回答：**

首次可以对旧前缀生成结构化摘要；再次压缩时应把已有摘要作为先前状态，与新被压缩消息做增量更新，
避免反复总结全部历史造成成本增长。摘要至少保留目标、已完成工作、关键事实、用户约束、文件/命令状态、
未解决问题和重要错误。原始历史必须持久化，以便审计和重新生成摘要；还应保留最近若干完整消息，避免
摘要丢失当前任务的细粒度状态。

LeAgent 的关键保证是“历史不变、投影可变”，但摘要本身仍可能失真，不能把 compaction 说成无损压缩。

### Q36：Compaction 和 Branch Summary 有什么区别？

**口述回答：**

Compaction 面向当前活动路径过长的问题，以当前 leaf 为 parent，在投影中替换一组旧消息；Branch Summary
用于用户回到旧节点开启新分支时，把被放弃的当前活动路径信息带到新分支，它挂在选中的分支点上，不替换
旧 rows。两者都不删除原历史，但触发时机、parent 和上下文语义不同。

### Q37：为什么选择历史 UserMessage 时回到它的 parent？

**口述回答：**

不带摘要地选择一条 UserMessage，通常意味着用户想编辑后重新提交。LeAgent 将 target 设为这条消息的
parent，并把原文本作为 input prefill 返回；编辑提交后才产生新的 UserMessage。若直接从原 UserMessage
继续，就会隐式复用旧输入，使新分支的语义不清晰。

### Q38：JSONL append-only 是否就等于可靠？

**口述回答：**

不等于。当前 Adapter 没有跨进程锁、事务、fsync 和 torn-tail 自动恢复，消息和对应 Leaf 还是两次
append；进程在中间退出时，可能出现消息已写但活动指针未推进。append-only 的优势是实现简单、易检查、
支持分支和迁移，不是数据库级 durability 保证。若继续演进，我会给 Storage Interface 增加原子
`append_many()`，Adapter 内加入校验和、尾行恢复与周期快照。

### Q39：长上下文越来越大，为什么还需要压缩和记忆？

**口述回答：**

窗口更大只提高上限，不消除延迟、成本、注意力稀释和工具输出膨胀。把所有历史都发送给模型还会引入无关
信息，降低关键约束的可见性。因此仍需要区分完整历史、工作上下文和按需检索记忆，并根据任务保留近期
状态、摘要旧信息。上下文工程的目标不是把窗口塞满，而是让模型看到完成当前决策所需的最小充分信息。

### Q40：如果要给 LeAgent 增加真正的长期记忆，你会怎么设计？

**口述回答：**

我不会把向量库直接塞进 `le_agent`。会在 Coding 应用层增加 MemoryStore Interface 和检索/写入 Tool：
写入时从对话中提取候选事实，做来源、时间、置信度、去重和冲突处理；读取时根据当前任务按语义、时间和
重要度召回，再经过 rerank 和 token budget 裁剪后注入上下文。用户偏好、情景事件和项目知识应分开存储，
支持用户查看、修改和删除。还要防止错误或恶意内容污染长期记忆，并用记忆相关任务验证写入准确率、召回
率和最终任务收益。

## 7. 多模型、流式通信与重试

### Q41：不同模型 Provider 最难统一的是什么？

**口述回答：**

以 OpenAI Codex 和 Mistral Conversations 为例，差异不只是 URL：

- **请求协议不同：** Codex 使用 Responses 风格的 `instructions + input items`，还带 reasoning 和 replay 元数据；Mistral 适配 Conversations/对话语义，拥有独立的 payload 和流解析器。
- **认证和运行时约束不同：** Codex 需要 access token 与 account id，并能从认证目录发现 model limits；Mistral 使用 API key 与自身 endpoint/模型配置。
- **流式事件不同：** Codex 发 `response.*` 事件，例如 output-text/reasoning/function-call delta；Mistral 的 `_MistralStreamParser` 负责把自身 chunk 归一化。
- **工具调用和回填不同：** 各 Adapter 需要映射调用 ID、JSON 参数增量和工具结果 envelope；Codex 还需保留 encrypted/replay metadata。
- **终态和元数据不同：** finish reason、usage、缓存 token、错误事件以及“是否已经产生内容”的判断字段
  都需要逐 Provider 映射，不能直接把某个厂商的字段名暴露给 Agent Loop。

统一分三层：第一层由各 Adapter 负责原生 payload、认证、SSE transport、增量解析、ID 映射和错误/重试判断；
第二层先转换为 Provider-neutral 的 `ProviderEvent`，包括 `response_start`、text/thinking delta、
`tool_call`、`response_end`、retry 和 error；第三层由 `canonicalize_provider_stream()` 转成统一的
`AssistantMessageEvent`。最终上层只看到有序的 `AssistantMessage.content`（`TextContent`、
`ThinkingContent`、`ToolCall`）、统一的 `Usage`、`stop_reason` 和 `ToolResultMessage` 回填，Agent Loop
不需要知道 Codex 的 `response.*` 或 Mistral 的原始 chunk 字段。这保留了 Provider 的原生能力，
同时把核心稳定在 LeAgent 的消息、事件和工具协议上。

### Q42：怎样保证 text、thinking 和 ToolCall 的顺序？

**口述回答：**

canonicalizer 维护当前 active block 的 index 和 kind。text/thinking 切换前先关闭旧 block；ToolCall
出现时也先关闭活动文本块，再追加独立内容块。流式 partial snapshot 使用 deep copy，避免异步消费者看到
后续 mutation。内容顺序以实际流为权威，终态只补 usage、finish reason 和 replay metadata，不能重排。

### Q43：为什么使用 SSE？和 WebSocket 怎么选？

**口述回答：**

模型生成通常是“客户端发一个请求、服务端持续返回事件”的单向流，SSE 基于 HTTP、实现和代理兼容性
较简单，也方便按事件解析。若需要长期双向低延迟交互，例如实时语音、客户端持续发送控制事件或复用一个
连接，WebSocket 更合适。LeAgent 上层消费的是异步事件 Interface，transport 选择应隐藏在 Provider Adapter，
不让 Loop 依赖 SSE。

### Q44：Provider Retry 和 Context Overflow Retry 有什么区别？

**口述回答：**

Provider Retry 是 transport 层对瞬时网络或 HTTP 条件的重试，由具体 Adapter 判断是否安全，并使用可取消
指数退避；已经部分输出的流不能随意重放。Context Overflow Retry 是 CodingSession 的应用编排：先压缩
上下文，再重新运行一次 Harness。

这里的“重试”不会增加 context window 的容量。Provider/model 的窗口上限保持不变；改变的是本次请求实际
占用的 token 数，因此为下一轮释放出更多 headroom。发生 overflow 后，CodingSession 会：

1. 保留 append-only session 中的原始消息和工具结果，不删除历史事实；
2. 选取活动上下文中较早的一段消息，通常保留最近约 `20_000` tokens 的消息；
3. 用独立的摘要请求把被替换部分压缩成结构化 summary，包含 Goal、Progress、Key Decisions、Next Steps
   和 Critical Context 等信息；
4. 追加 `CompactionEntry`，其 `replaces_entry_ids` 指向被压缩的消息；重放活动路径时，把这些消息投影成一条
   `UserMessage`，内容形如 `Previous conversation summary:\n...`，并保留未被压缩的最近消息；
5. 用这个新的消息投影替换 Harness 的 active messages，然后调用 `continue_()` 重新请求模型。

所以 retry 看到的上下文通常是“摘要 + 最近原文/工具结果 + 原有 system prompt 和 tools”，不是原来的完整
历史，也不是只剩摘要。摘要会丢失部分细节，质量取决于摘要模型；但原始 Entry 仍在 JSONL session tree 中，
只是当前 active context projection 不再直接发送它们。当前 overflow 路径是一次压缩后重试，压缩失败则不再重试。

### Q45：多模型支持为什么不只是切换 base URL？

**口述回答：**

除了 payload 和 stream parser，还涉及模型能力、context window、thinking 配置、图片支持、工具 Schema
限制、认证方式、credential refresh、错误分类和 usage。Runtime 还要根据 catalog 选择合适 Adapter 和模型
上限。若只替换 base URL，容易在 ToolCall、reasoning 或 replay 上产生隐蔽兼容问题。

## 8. 可测试性、可观测性与工程边界

### Q46：LLM 输出非确定，怎么测试 Agent Loop？

**口述回答：**

单元测试不请求真实模型，而使用 FakeProvider 重放预定义 Assistant 事件，并记录传入的 messages 和 tools；
Fake Tool 返回确定结果或异常；内存 Storage 测试 Session 重放。这样可以精确断言事件顺序、ToolResult
回填、steering/follow-up、取消、修复和 compaction 语义。真实模型测试适合放在单独的集成或回归层，
不能让核心状态机测试受模型随机性和网络影响。

简历正文不写静态的测试数字；面试如果追问证据，我会指向对应失败路径的测试，并展示投递前重跑的当前 CI/pytest 结果，
而不背一个容易过期的 passed 数字。

### Q47：如何观测一次 Agent run？

**口述回答：**

我会用 run id 串起 Provider 请求、Agent turn、Message、ToolCall、ToolResult、压缩和重试。指标至少包括
请求次数、输入/输出 token、首 token/总耗时、turn 数、工具成功率、错误分类、取消和 context 使用率。
LeAgent 已经有分层事件和结构化错误日志，能记录 provider、model、session、phase，同时只提取有限的安全错误
字段。生产 tracing 还必须默认保护模型和工具输入输出中的敏感信息，并支持采样与保留策略。

### Q48：怎样判断一个 Agent 是“完成了”，而不是过早宣布成功？

**口述回答：**

不能只相信模型的最终文本。Coding Agent 应将完成条件落到外部证据：修改目标文件、命令退出码、相关
测试通过、diff 与任务约束一致。对高风险任务还需用户确认。LeAgent 的 Loop 只判断协议层终止，业务层是否
成功需要工具结果和任务验证补充；这也是 Runtime termination 与 task correctness 的区别。

### Q49：这个项目目前有哪些不足？

**口述回答：**

我会明确说五点：第一，并行调度仍是整批 barrier，没有资源冲突图；第二，JSONL 没有
事务、fsync、跨进程锁和 torn-tail 恢复；第三，context overflow 主要通过错误文本 marker 识别，缺少统一
结构化错误码；第四，Skill 召回依赖 description 与模型决策，还没有专门的召回/任务成功率 eval；第五，
web search 依赖外部服务与配额，搜索结果也需上层验证。能清楚说明边界和演进顺序，
比把项目包装成生产完备系统更可信。

### Q50：如果继续演进，优先级怎么排？

**口述回答：**

我会先补可靠性，再扩能力：首先扩展 Storage Interface，支持 message + leaf 原子批量追加、尾行校验和
快照；其次结构化 Provider 错误与 retry policy；然后将已有 execution_mode 调度扩展为基于副作用和资源冲突域的部分并行，同时为 Tool 选择和 Skill 召回建立版本化 eval。最后再考虑长期记忆、MCP 和多 Agent。原因是这些高级能力都会放大状态、一致性和权限问题，
底层不可靠时越复杂越难排障。

## 9. 基础知识补充题

### Q51：Temperature、Top-p 对 Agent 有什么影响？

**口述回答：**

它们控制采样分布，但不是“幻觉开关”。工具选择和结构化任务通常希望更稳定，可用较低随机性；创意生成
可以提高。实际支持范围由模型决定，推理模型可能弱化或不支持这些参数。Agent 可靠性更依赖上下文、工具
Schema、验证、权限和停止条件，不能只靠把 temperature 设为 0。

### Q52：Context Window 和模型记忆是什么关系？

**口述回答：**

模型 API 通常是无状态的，本轮能使用的信息来自请求上下文或服务端维护的等价会话状态。Context Window
是一次推理可处理的 token 上限，不等于持久化记忆。应用必须保存 transcript，并决定每轮回放、压缩或
检索哪些内容。LeAgent 的 Session Tree 负责持久历史，Context Projection 负责本轮输入。

### Q53：为什么工具调用会显著消耗 Token？

**口述回答：**

每轮输入不仅有对话，还包含 system prompt、工具名称/描述/Schema、历史 ToolCall 与 ToolResult；命令日志
和文件内容尤其容易膨胀。多轮 Agent 又会重复发送历史前缀。因此要做工具裁剪、结果截断、稳定前缀缓存、
context accounting 和 compaction，而不是只优化最终回答长度。

### Q54：结构化输出失败怎么办？

**口述回答：**

首先优先使用 Provider 原生 function calling 或 schema-constrained output；Runtime 再做严格解析和业务
校验。失败时返回明确、有限的校验错误，让模型只修正无效字段，并限制重试次数。不能用宽松正则静默猜测
高风险参数，也不能因为 JSON 合法就跳过权限和业务约束。

### Q55：Prompt Injection 对 Coding Agent 为什么更危险？

**口述回答：**

因为 Coding Agent 会读取仓库、网页和命令输出，这些不可信内容可能包含伪装指令；同时它还拥有文件和
shell 等真实执行能力。防御要以能力安全为核心：明确指令优先级，把外部内容当数据；最小化工具权限；
敏感动作审批；限制 workspace、网络和 credential；记录调用并展示副作用。Prompt 文案只能降低概率，
不能替代权限和沙箱。

## 10. 回答方法：避免“背答案感”

每道项目题可以使用四段式回答：

1. **问题：** 当时要解决什么工程矛盾；
2. **设计：** Interface、数据流和关键不变量；
3. **取舍：** 为什么没选另一个方案，付出了什么成本；
4. **边界：** 当前没有实现什么，下一步如何演进。

例如回答 Session Tree：

> 线性 messages 很难同时表达分支、审计和活动位置，所以我用带 parent_id 的 append-only Entry 保存
> 完整历史，并用 LeafEntry 选择当前目标；加载时重放 root-to-leaf 路径，投影为 Harness messages。
> 这样压缩和回溯不需要删除历史，代价是恢复需要 O(n+h) 扫描，JSONL 也不具备数据库事务。下一步会在
> Storage Interface 下增加原子批量追加和快照，而保持上层投影语义不变。

遇到项目中没有实现的能力时，使用下面的句式：

> LeAgent 当前没有实现 X，现有 Seam 在 Y；如果面向生产扩展，我会先解决 A 的不变量，再用 B 的方式接入，
> 因为直接增加 X 会带来 C 风险。

## 11. 资料来源

### 11.1 公开面经与趋势样本

- [Agent 开发面经总结：阿里、蚂蚁、字节等公开样本](https://www.nowcoder.com/discuss/877151327091027968)：出现项目架构、Loop 终止、死循环、工具、失败重试、单/多 Agent、RAG 与后端基础等问题。
- [Agent 开发岗位记忆系统追问复盘](https://www.nowcoder.com/discuss/909210322701942784)：集中体现摘要触发、全量/增量摘要、原始历史保留、按需检索与工具 token 成本等追问方式。
- [AI Agent / LLM 应用工程师题库汇总](https://github.com/harrisliangsu/ai-agent-engineer-handbook/blob/main/interview-prep/interview-questions.md)：用于补充问题分类；其中二手结论和未经核验的数字不作为本文技术事实依据。

### 11.2 一手技术资料

- [OpenAI Agents SDK：Agent Loop](https://openai.github.io/openai-agents-python/running_agents/)：工具回填、handoff、final output、max turns、streaming 和 tracing 的官方运行语义。
- [OpenAI Agents SDK：Tools](https://openai.github.io/openai-agents-python/tools/)：function tools、local runtime tools、approval、tool search、timeout 与错误处理。
- [OpenAI Agents SDK：Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)：LLM 编排、代码编排、manager-as-tools、handoff 与并行模式的取舍。
- [OpenAI Agents SDK：Tracing](https://openai.github.io/openai-agents-python/tracing/)：对 LLM、工具、handoff、guardrail 和自定义事件的 trace，以及敏感数据配置。
- [Anthropic：Trustworthy agents in practice](https://www.anthropic.com/research/trustworthy-agents)：Agent 自主循环、人类控制、工具权限、安全、透明度与隐私。
- [Model Context Protocol 官方文档](https://modelcontextprotocol.io/docs/getting-started/intro)：MCP 的 client/server 架构和工具连接定位。
- [ReAct 论文](https://arxiv.org/abs/2210.03629)：交错推理与行动、使用环境 observation 继续决策的基础模式。
- [MemGPT 论文](https://arxiv.org/abs/2310.08560)：分层记忆与有限上下文管理的经典设计。

### 11.3 LeAgent 源码证据

- 架构与 Provider：`src/le_agent/provider.py`、`src/le_agent_ai/stream.py`
- Loop 与 Harness：`src/le_agent/loop.py`、`src/le_agent/harness.py`
- Tool/Skill：`src/le_agent/tools.py`、`src/le_agent_coding/tools.py`、`web_search.py`、`skills.py`、`system_prompt.py`
- 会话树：`src/le_agent/session/entries.py`、`tree.py`、`memory.py`、`storage.py`
- CodingSession：`src/le_agent_coding/session.py`、`events.py`、`context_window.py`
- 测试：`tests/test_le_agent_ai.py`、`test_agent_loop.py`、`test_agent_harness.py`、`test_session.py`、`test_coding_session.py`、`test_skills.py`、`test_web_search.py`
