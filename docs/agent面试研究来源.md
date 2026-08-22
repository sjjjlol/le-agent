# 2025—2026 Agent 工程岗位面试考察方向研究

> 研究目的：为 LeAgent 项目的面试问答扩展提供来源依据。本文优先采用公司官方岗位描述、官方 SDK/框架文档、协议规范和论文；公开面经只作为趋势补充，不视为企业官方题库。
>
> 调研时间：2026 年 8 月。

## 一、结论摘要

当前主流 Agent 工程岗位考察的并不是“是否会调用某个 Agent 框架”，而是能否把不稳定的模型能力变成一个可控制、可恢复、可观测的工程系统。最常见的能力面可以归纳为：

1. Agent 架构与模块边界；
2. Agent Loop、流式事件和运行控制；
3. 工具契约、执行治理与失败恢复；
4. Memory、Context Engineering 和长任务续航；
5. 可靠性、持久化和分布式系统语义；
6. 权限、安全边界和 Human-in-the-loop；
7. 可观测性与故障定位；
8. Agent Eval 的基本方法；
9. Python async、并发和后端系统设计。

LeAgent 的简历内容与前四项高度吻合，但面试时很可能从已有实现继续追问可靠性、安全、观测和异步语义。评估系统即使暂时不写入简历，也应能诚实说明项目当前边界以及下一步如何建设。

## 二、官方岗位描述反映出的考察重点

### 1. Agent Runtime 与核心执行循环

OpenAI 的 Codex Core Agent 岗位把 `core execution loop`、工具使用策略、上下文构造、长周期任务，以及 token、延迟、可靠性、成本和容量列为核心工作。它说明 Agent 岗位会直接考察 Runtime，而不只是 Prompt 或模型 API 调用。

对应的典型面试问题：

- Agent 与固定 Workflow 的本质区别是什么？
- 一轮 `user → model → tool call → tool result → model` 如何执行？
- 运行结束条件有哪些，如何防止无限循环和重复调用？
- 为什么工具结果必须进入下一轮模型上下文？
- 如何统一限制最大轮次、时间、token 和费用预算？

来源：

- [OpenAI：Applied AI Engineer, Codex Core Agent](https://openai.com/careers/applied-ai-engineer-codex-core-agent-san-francisco/)
- [OpenAI Agents SDK：Agent loop、tools、sessions、guardrails 与 tracing](https://openai.github.io/openai-agents-python/)
- [Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [ReAct 论文：交错进行推理与环境行动](https://arxiv.org/abs/2210.03629)

### 2. 分层、抽象与平台边界

岗位描述反复强调 Provider 集成、共享平台原语、Agent Runtime、外部工具和 UI/产品层之间的清晰边界。Anthropic 的 Managed Agents 工程设计进一步把长任务系统拆为 session、harness 和 sandbox。这与 LeAgent 的 `le_agent_ai / le_agent / le_agent_coding` 分层高度对应。

对应的典型面试问题：

- 为什么要把 Provider、Agent Harness 和 Coding App 分层？
- 哪些概念属于通用 Agent 内核，哪些属于 Coding Agent 产品层？
- 更换模型厂商、TUI 或存储实现，各会影响哪些层？
- 为什么 UI 应消费事件，而不是直接嵌入 Agent Loop？
- 为什么自己实现 Harness，而不是直接采用 LangGraph 等框架？

来源：

- [Anthropic：Scaling Managed Agents—Decoupling the brain from the hands](https://www.anthropic.com/engineering/managed-agents)
- [OpenAI：Software Engineer, Codex Core Agents](https://openai.com/careers/software-engineer-codex-core-agents-san-francisco/)
- [Sim：Software Engineer, Agents](https://jobs.ashbyhq.com/sim/c04eccb8-cbd8-4dc7-8299-b1f4c8aa1225)
- [Docker：Senior Software Engineer, AI Developer Tools](https://jobs.ashbyhq.com/docker/b059e0ac-c4b2-48a9-88b8-2877bc1228a5/)

### 3. 工具系统与 Agent-Computer Interface

工具是确定性系统与非确定性模型之间的契约。官方资料重点关注 Schema、参数校验、错误回传、工具描述质量、审批、权限和结果的 token 效率。工具数量增大后，工具发现和上下文占用也会成为系统问题。

对应的典型面试问题：

- 模型可见 Schema 与本地 Executor 为什么要解耦？
- 未知工具、JSON 解析失败、Schema 错误和业务异常如何区别处理？
- 工具错误应该给用户看，还是结构化后回填模型？
- before/after Hook 如何实现权限校验、审计、脱敏和输出治理？
- 大量工具全部塞进 Prompt 有什么问题，如何按需发现？
- 多个工具调用能否并行？怎样区分只读工具和有副作用工具？

来源：

- [Anthropic：Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Anthropic：Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
- [OpenAI Agents SDK：Tools](https://openai.github.io/openai-agents-python/tools/)
- [MCP：Server concepts](https://modelcontextprotocol.io/docs/learn/server-concepts)
- [Toolformer 论文：模型何时、如何调用 API](https://arxiv.org/abs/2302.04761)

### 4. Memory 与 Context Engineering

行业语境中的 Memory 不只是“保存聊天记录”。需要区分原始历史、当前模型上下文、线程级 Checkpoint、跨线程长期记忆，以及外部知识检索。Anthropic 将 Context 视为有限预算，强调选择最小但高信号的信息集合，并使用 just-in-time retrieval、compaction 和结构化工作笔记支持长任务。

对应的典型面试问题：

- Memory、Session、Context、RAG 分别解决什么问题？
- 为什么不能把全部历史直接传给模型？
- Compaction 的触发条件、覆盖范围和摘要有效性如何确定？
- 摘要丢失关键工具结果或产生漂移怎么办？
- 分支后如何构造活动路径，旧分支 Summary 是否还能复用？
- 短期 Checkpoint 与跨会话长期记忆怎样隔离？

来源：

- [Anthropic：Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Anthropic：Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [OpenAI Agents SDK：Sessions](https://openai.github.io/openai-agents-python/sessions/)
- [LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

### 5. 可靠性、恢复与持久化语义

生产 Agent 必须处理 Provider 429/5xx、流式连接断开、工具执行失败、进程崩溃、恢复后副作用重放等问题。Checkpoint 能恢复计算状态，但不能自动保证外部副作用 exactly-once；有写操作时仍需幂等键、事务、outbox 或补偿机制。

对应的典型面试问题：

- Provider 流在半途中断时，已经展示和持久化的状态如何处理？
- Tool 已执行成功，但 ToolResult 尚未落盘时崩溃，恢复后怎么办？
- 为什么 tool call 与 tool result 必须配对？孤立 ToolCall 如何修复？
- Retry 为什么可能造成重复写文件、重复扣款或重复发消息？
- Append-only JSONL 是否等于 crash-safe？半行损坏、并发写和 fsync 怎么处理？
- at-most-once、at-least-once 和 exactly-once 在 Agent 工具执行中如何取舍？

来源：

- [OpenAI Agents SDK：Running agents](https://openai.github.io/openai-agents-python/running_agents/)
- [OpenAI Agents SDK：Streaming result 与取消语义](https://openai.github.io/openai-agents-python/results/)
- [LangGraph：Interrupts、恢复与副作用幂等](https://langchain-ai.github.io/langgraph/concepts/breakpoints/)
- [LangGraph Agent Server：Checkpoint、队列和恢复](https://langchain-ai.github.io/langgraph/concepts/langgraph_server/)
- [Anthropic：Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)

### 6. 安全、最小权限与 Human-in-the-loop

Agent 会主动作用于环境，因此安全不能只靠 System Prompt。官方资料强调 Prompt Injection、Excessive Agency、Sandbox、最小权限、工具级 Guardrail、高风险操作审批、凭证隔离和敏感 Trace 治理。

对应的典型面试问题：

- Prompt Injection 为什么不能只用提示词防御？
- 读文件、写文件、Shell、网络和外部事务工具应如何分级？
- 哪些动作必须由用户审批，何时可以自动执行？
- 如何防路径逃逸、命令注入、Secret 泄漏和间接 Prompt Injection？
- Agent 级 Guardrail 与 Tool 级 Guardrail 有什么区别？
- 如果 Guardrail 与 Agent 并行执行，失败前工具已经产生副作用怎么办？

来源：

- [OpenAI Agents SDK：Guardrails](https://openai.github.io/openai-agents-python/guardrails/)
- [OpenAI Agents SDK：Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- [MCP 规范：Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [MCP 规范：Tool safety](https://modelcontextprotocol.io/specification/2025-03-26/index)
- [OWASP：LLM06:2025 Excessive Agency](https://owasp.org/www-project-top-10-for-large-language-model-applications/2_0_vulns/LLM06_ExcessiveAgency.html)

### 7. 可观测性与故障定位

Agent 的一次 Run 跨越模型调用、工具执行、Handoff、Guardrail 和存储等多个组件，普通应用日志很难直接解释失败原因。主流实现会通过 run/turn/tool-call ID 关联 Trace 和 Span，并观测成功率、延迟、token、成本、重试和人工接管。

对应的典型面试问题：

- 如何把一次 Run 中的模型调用和多个 ToolCall 串起来？
- 应记录哪些 Event、Trace、Metric，哪些内容不能记录？
- 如何判断失败源于模型、Provider、Context、Tool 还是 Runtime？
- Event Sourcing 与 Tracing 有什么区别？
- 不存储 Chain-of-thought 时如何调试 Agent？
- 怎样重放历史执行，同时避免再次触发外部副作用？

来源：

- [OpenAI Agents SDK：Tracing](https://openai.github.io/openai-agents-python/tracing/)
- [OpenAI Agents SDK：Configuration 与敏感日志控制](https://openai.github.io/openai-agents-python/config/)
- [TRM Labs：AI Agent Engineer](https://jobs.ashbyhq.com/trm-labs/828b60b2-ac8f-407d-92a0-8b794c8cf391)
- [Traversal：AI Engineer—Agents](https://jobs.ashbyhq.com/traversal/de8e7ab2-03bc-4bd1-b016-8599579875d4)

### 8. Agent Evaluation

虽然 LeAgent 当前不宜把成熟 Eval 系统写进简历，但面试中仍很可能被问到“如何证明 Agent 变好了”。Agent Eval 应优先检查最终环境 Outcome，并结合轨迹检查；常见 Grader 包括代码规则、模型评分和人工评分。非确定性还要求多次 Trial，并区分能力评估与回归评估。

适合 LeAgent 的诚实回答是：当前使用 Fake Provider、Fake Tool 和单元测试验证 Runtime 的确定性行为，但这不等于 Agent Behavioral Eval；下一步可以从真实失败样本构建 Golden Tasks，衡量任务成功率、工具选择/参数准确率、成本、延迟和失败类型。

对应的典型面试问题：

- Unit Test 与 Agent Eval 的区别是什么？
- 为什么不能只看最终自然语言回答？
- 如何评估 Tool-call Accuracy、Trajectory 和最终 Outcome？
- LLM-as-a-judge 有哪些偏差，如何校准？
- 如何区分 Capability Eval 与 Regression Eval？
- 为什么同一任务需要多次 Trial？

来源：

- [Anthropic：Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [OpenAI：Research Engineer, Frontier Evals & Environments](https://openai.com/careers/research-engineer-frontier-evals-and-environments-san-francisco/)
- [SWE-bench 论文：真实 GitHub Issue 的 Coding Agent 评估](https://arxiv.org/abs/2310.06770)
- [AgentBench 论文：跨环境的 Agent 评估](https://arxiv.org/abs/2308.03688)

### 9. Python Async 与后端系统设计

Agent Loop 天然涉及异步流、长连接、并发工具、取消、超时和背压。Agent 岗仍然会考扎实的语言与系统基础，而不是只问 LLM 概念。

对应的典型面试问题：

- 为什么模型流适合使用 Async Iterator？
- 取消如何从 CLI/TUI 传播到 Provider Stream 和 Tool Executor？
- 为什么捕获 `CancelledError` 后通常必须重新抛出？
- 多个 ToolCall 使用 `gather` 还是 `TaskGroup`，失败语义有何差别？
- 同步文件/子进程操作如何避免阻塞 Event Loop？
- 如何用 Semaphore 限流、Queue 实现背压并保证单 Active Run？
- Steering 与 Follow-up 并发入队时如何避免竞态和饥饿？

来源：

- [Python 官方文档：Coroutines and Tasks](https://docs.python.org/3/library/asyncio-task.html)
- [OpenAI：Software Engineer, Agent Infrastructure](https://openai.com/careers/software-engineer-agent-infrastructure-san-francisco/)
- [Cartesia：Software Engineer, Agents](https://jobs.ashbyhq.com/cartesia/16cd6cd7-454b-4e44-9a04-6a4677a3e920/)
- [WRITER：Software Engineer, Agents](https://jobs.ashbyhq.com/writer/6bbdb7ac-d081-4788-952b-6648be46a3bc)

## 三、公开面经呈现出的问法趋势

公开面经不是官方题库，存在以下限制：

- 依赖候选人回忆，可能遗漏或重构题目；
- 存在转载、搬运甚至拼接问题；
- 中文公开样本偏向校招、实习和少数大厂；
- 无法据此计算严格的行业题目出现频率；
- 岗位方向不同，Applied AI、Agent Infra、算法和产品工程的题型差异明显。

因此，下列“高频”仅表示多个独立公开来源反复出现，不代表经过统计验证：

1. **项目深挖先于概念问答**：从项目目标、个人贡献和选型出发，沿简历中的 Agent、Tool、Memory、RAG 等关键词连续追问。
2. **要求完整讲清 Agent Loop**：模型何时选择工具、Runtime 如何解析、Executor 怎样执行、ToolResult 如何回填，以及循环如何结束。
3. **重视失败分型和恢复**：未知工具、参数错误、权限失败、网络超时和业务失败分别怎么处理。
4. **Memory 问题会追到隔离和压缩**：保存、选择、压缩、持久化以及多用户污染问题都会被问到。
5. **Agent 岗仍考工程基本功**：Python、异步并发、网络、数据库、消息队列、日志、系统设计和算法题不会因为岗位名称带 Agent 而消失。
6. **MCP、Skills、多 Agent、Checkpoint 和 Eval 正在升温**，但是否重点考察高度依赖具体岗位。

社区趋势来源：

- [牛客：腾讯/百度等大模型与 Agent 面经汇总](https://www.nowcoder.com/discuss/878600528970735616)
- [牛客：Agent 岗 23 问面经整理](https://www.nowcoder.com/discuss/864153617182355456)
- [牛客：大模型 Agent 进阶面问题整理](https://www.nowcoder.com/discuss/908750485325086720)
- [LLM Interview Guide：公开岗位真题汇总](https://meko1.github.io/llm-interview-guide/interview/real-questions)
- [Reddit / YC 社区：AI Engineer interview assignment 讨论](https://www.reddit.com/r/ycombinator/comments/1jnfijm/what_is_your_interview_assignment_for_ai_engineers/)

## 四、最适合 LeAgent 项目的面试追问清单

### 架构设计

- 为什么是 `le_agent_ai / le_agent / le_agent_coding` 三层，而不是一个大 Agent 类？
- 三层之间的依赖方向是什么？如何防止核心 Harness 反向依赖 CLI/TUI？
- Provider Protocol 的最小接口是什么？如何吸收不同厂商的流事件差异？
- Storage Interface 为什么不能泄漏 JSONL 的存储细节？
- 如果换成数据库、Web UI 或新的 Provider，代码改动边界在哪里？

### Agent Loop 与运行控制

- 画出从 UserMessage 到最终 AssistantMessage 的完整事件序列。
- 流式文本、Reasoning Delta 和 ToolCall Delta 怎样组装成权威消息？
- Harness 为什么限制单 Active Run？
- Steering 与 Follow-up 有什么语义差别，分别在什么时候消费？
- 取消发生在 Model Stream、Tool Execution 或持久化阶段，分别如何处理？
- 为什么要修复未完成 ToolCall？它解决了什么，又没有解决什么？

### 工具系统

- 模型可见 Schema 和 Executor 为什么解耦？
- 参数校验在哪层执行？Schema 通过但业务语义错误怎么办？
- Hook 能否修改参数、阻断调用或覆盖结果？Hook 自身失败怎么办？
- 多个工具是否并行？有副作用的工具如何排序、审批和保证幂等？
- Tool Error 中哪些信息可以回填模型，为什么不应暴露完整 Traceback？

### 会话与上下文

- 为什么采用 Append-only JSONL，而不是覆盖快照或直接使用 SQLite？
- `parent_id` 如何形成 Session Tree，Leaf 如何决定 Active Path？
- Context Projection 与持久化历史为什么是两个概念？
- Compaction Summary 与 Branch Summary 的职责有什么差别？
- JSONL 文件无限增长、损坏尾行、并发写入如何生产化改进？

### 可靠性、安全与工程边界

- 工具成功但 Result 未持久化时如何恢复？
- Transcript 结构合法是否等于外部副作用 exactly-once？
- Provider 429/5xx 与网络断流如何设计重试和退避？
- Shell/File 工具如何做路径限制、Sandbox、超时、审批和审计？
- Prompt Injection 通过工具结果进入上下文时怎么办？
- 当前项目如果上线，Sandbox、Durable Execution、Telemetry、Eval 应按什么顺序补齐？

### Async 与系统设计

- Async Generator 的关闭和异常传播语义是什么？
- `CancelledError` 为什么不能随意吞掉？
- `TaskGroup` 与 `gather` 如何选择？
- Streaming Producer 比 UI Consumer 快时如何实现背压？
- 多用户并发下 Session、Queue 和 Active Run 如何隔离？

## 五、回答这些问题的推荐结构

对每个问题使用同一条回答链路：

> 需求或故障模式 → 设计选择 → LeAgent 中的实现落点 → 代价与边界 → 生产化下一步

例如回答 Append-only Session：

1. 先说明目标是保留审计历史、支持分支和恢复；
2. 再说明通过追加 Entry 和 `parent_id` 构建树；
3. 解释当前上下文由活动 Leaf 投影，而不是修改原历史；
4. 承认 JSONL 在并发写、索引和崩溃一致性方面的边界；
5. 最后给出 SQLite/Postgres、事务、校验和、Checkpoint 等生产化方向。

## 六、LeAgent 面试表述的能力边界

以下边界应主动说清楚，能体现工程判断，而不是削弱项目价值：

- Hooks 是权限和审计的扩展 Seam，不等于已经实现完整 RBAC。
- Append-only 表示逻辑历史不可变，不等于文件写入已经 crash-safe。
- Fake Provider / Fake Tool 测试属于确定性 Runtime 测试，不等于成熟的 Behavioral Eval。
- 中断 ToolCall 修复保证 Transcript 结构合法，不等于外部副作用 exactly-once。
- 支持异步 Loop 不自动意味着多个 ToolCall 已安全并行。
- Context Compaction 控制 token 使用，但摘要仍可能丢失信息或发生漂移。

这些限制正是面试时适合展开的系统设计空间。
