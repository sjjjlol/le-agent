# Agent 面试专题：评测、Hooks 与编排架构

> 调研日期：2026-08-19。以下结论优先引用官方文档、官方源码与论文；它是给 `LeAgent项目面试问答.md` 补充第 8—11 题的依据，不宣称 LeAgent 未实现的能力。

## 8. 怎么校验 Agent 确定性的结果？

先区分两件事：**模型行为通常不确定**，但任务的验收、工具契约和评测环境可以尽量确定。不能以“同一 prompt 再跑一次输出不同”判断系统错误；应校验每次 trial 是否满足可验证的成功条件。

推荐回答：

1. 把任务定义为 `输入 + 初始环境 + 预算 + 成功条件`，每次在干净、隔离的环境执行；固定工具版本、prompt、模型配置、代码提交和 task 版本。
2. 结果优先用确定性 grader：代码测试、数据库终态断言、JSON Schema/字段断言、静态分析或安全策略。不要相信 Agent 自称“已完成”。对开放式质量再加 rubric 化 LLM judge，并用人工样本校准。
3. 同一配置跑多个 trial，报告成功率/`pass@k`、延迟、成本、工具错误率，并把 provider/超时/evaluator 故障与正常的任务失败分开。
4. 保存 transcript/trajectory（模型输出、tool call、tool result、最终环境），抽样复查失败和异常高分；这既能检查 grader 是否误判，也能防止 agent 钻评分漏洞。
5. 把用户 bad case 转成版本化的回归题；新策略先跑 regression suite，再以线上监控、A/B 与人工审核补足离线评测的盲区。

LeAgent 的当前落点：仓库已有 `le-agent benchmark` 的隔离 workspace、确定性 evaluator、trajectory 和多 trial 汇总；它适合证明评测管线与几个固定任务的结果，不能用 fake provider 的满分证明真实模型能力。细节见 [LeAgent 评测基础](benchmarking/01-evaluation-fundamentals.md)。

一手依据：

- [Anthropic：Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) 将 task、trial、grader、transcript 分开定义，并建议隔离环境、检查轨迹、把生产反馈纳入 eval。
- [OpenAI Evals design guide](https://platform.openai.com/docs/guides/evals) 说明应以任务成功标准和可复现评测来比较系统；[Graders API](https://platform.openai.com/docs/api-reference/graders) 给出可管理的代码/模型评分接口。
- [SWE-bench 论文](https://arxiv.org/abs/2310.06770) 展示了用真实任务的测试结果验证 coding agent patch，而不是比对单一参考文本。

## 9. Hooks 怎么校验工作？

Hook 是生命周期的扩展点，不应成为“偷偷改变核心语义”的黑盒。校验分三层：**契约、集成、运行审计**。

| 层次 | 要校验什么 | 可执行办法 |
| --- | --- | --- |
| 单元/契约 | 触发时机、入参、返回值、异常和是否允许修改/阻断 | 用 fake tool/provider 记录调用序列，断言 `before → executor → after`；覆盖拒绝、超时、异常、取消和重试。 |
| 集成 | Hook 是否真的包裹生产执行路径 | 走真实 runner 与一个无副作用测试工具，断言 hook 的审计记录与工具执行观测事件能关联到同一次调用。 |
| 线上审计 | Hook 策略是否有效且没有伤害成功率 | 将 allow/deny/redact/override、policy version、耗时和异常写入 trace；抽样复核 allow 与 deny，比较拦截率、误拦率、绕过率、延迟和任务成功率。 |

安全类 `before` hook 应采用 fail-closed：验证参数 schema、授权范围、风险等级与审批 token，未通过时**不得调用**副作用工具。`after` hook 应校验结果 schema、脱敏及审计落盘；不要把 after hook 当作已发生写操作的安全屏障。若 Hook 自身有外部副作用，也要设置幂等键并为其单独设超时/失败策略。

可用于面试的测试例：对一个假 `send_money` 工具，未审批时断言 executor 的调用次数为零；审批后断言 hook、executor、after hook 按顺序各一次；让 after hook 抛错，断言业务结果状态、审计失败状态和重试语义符合预先定义的契约。若需要关联 ID，使用工具执行观测事件——不要假设每个 before hook payload 天然携带它。不要只测“日志打印过”。

LeAgent 当前实现：`le_agent_coding.extensions` 已在 coding app 层提供 `tool_call`（执行前，可 block 或改参数）和 `tool_result`（执行后，可改结果）扩展 Hook；它们通过包装工具 executor 接入，刻意不污染通用 `le_agent`。`tool_call` handler 抛异常时 fail-safe 地阻断调用；`tool_result` handler 抛异常时保留原结果并记录诊断。当前 `ToolCallHookEvent` 本身不带 `tool_call_id`，需要与 `tool_execution_start/end` 观测事件配合关联。实现与已覆盖的测试见 [`extensions/runtime.py`](../src/le_agent_coding/extensions/runtime.py)、[`extensions/api.py`](../src/le_agent_coding/extensions/api.py) 和 [`test_extensions.py`](../tests/test_extensions.py)。

一手依据：

- [OpenAI Agents SDK：Lifecycle](https://openai.github.io/openai-agents-python/ref/lifecycle/) 定义 `on_tool_start`/`on_tool_end` 等运行生命周期回调；[Agents 文档](https://openai.github.io/openai-agents-python/agents/#lifecycle-events-hooks) 区分 run-scoped 与 agent-scoped hooks，并说明 tool hook 可获得 `tool_call_id`、名称和参数等元数据。
- [OpenAI Agents SDK：Tool input/output guardrails](https://openai.github.io/openai-agents-python/guardrails/) 说明工具调用可在执行前做输入治理、在输出返回模型前做输出治理；高风险副作用仍需要审批与最小权限。
- [Claude Code：Hooks reference](https://code.claude.com/docs/en/hooks) 给出 `PreToolUse` 可阻断、`PostToolUse`/失败事件可复核的完整生命周期，以及输入/输出 JSON 和 matcher 的端到端例子。
- [OWASP LLM06：Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/) 将过度权限、过度功能和过度自主性列为 Agent 风险，支持把权限校验放在工具执行边界。

## 10. Agent 效果一般且有 bad case，怎样确定优化方向？

不要先凭直觉改 prompt。先把“感觉一般”变成可分桶、可复现、可排序的证据。

```text
线上反馈 / eval 失败
        ↓
最小可复现 task + 保留 transcript、环境、版本
        ↓
失败归因：规格/评分器 | 模型决策 | context | tool | runtime/基础设施
        ↓
按 用户影响 × 出现频率 × 可修复性 排序
        ↓
为 bad case 写 regression eval，比较单变量改动的质量/成本/延迟
        ↓
灰度或 A/B → 线上监控 → 回灌新的失败样本
```

具体做法：

- 先排除“假失败”：题目是否含糊、grader 是否过严、环境是否 flaky、harness 是否人为限制能力。0% 并不自动等于模型不行。
- 从 trajectory 判断首个错误点：没有检索到关键上下文是 retrieval/context 问题；选错工具或参数是 tool schema/description/策略问题；执行正确但终态错误是规划或恢复问题；provider/超时/权限拒绝则属系统问题，不能混入模型能力分数。
- 优化应一次只改变一个主要变量（模型、prompt、上下文选择、tool 描述、策略/guardrail、预算），在固定 regression set 上多 trial 比较；关注质量、成本、延迟的 Pareto 关系，而非只追一个总分。
- bad case 进入回归集前应脱敏、去重和按场景/风险分层；保留一部分未参与调优的 holdout，防止只对已知题过拟合。

一手依据：

- [Anthropic：Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) 建议从人工检查与真实用户失败起步，阅读 transcript 验证失败公平性，并同时使用自动评测、生产监控、A/B 与人工校准。
- [Anthropic：Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 将上下文视为有限预算，支持把“上下文缺失/噪声”作为独立优化假设，而非一律归咎模型。
- [OpenAI Agents SDK：Tracing](https://openai.github.io/openai-agents-python/tracing/) 展示 trace/span 记录模型、工具、handoff 与 guardrail 的做法；可用关联 ID 定位失败落在哪个边界。

## 11. Workflow 和一个整体的 Agent 有什么区别？

这里的“整体 Agent”应理解为一个能在运行时自主决定下一步的闭环系统，而不是“代码里只有一个类”。两者可以组合：固定 workflow 的某一步可以调用一个 agent，agent 内部也可以有确定的安全/执行子流程。

| 维度 | Workflow | 整体 Agent |
| --- | --- | --- |
| 控制权 | 开发者预先在代码中规定路径、分支和重试 | 模型基于当前上下文动态选择下一步与工具 |
| 运行形态 | 有限、可预测的 DAG/状态机/编排 | `observe → decide → act → observe` 循环，直到达成或预算耗尽 |
| 强项 | 任务边界清楚、合规要求严格、需要稳定延迟/成本/可审计路径 | 任务步骤不确定，需要探索、工具选择、长期适应或处理例外 |
| 风险与治理 | 主要验证每条预定义路径及边界条件 | 还需评测策略、轨迹、安全约束、停止条件、预算和异常恢复 |
| 典型例子 | 固定字段抽取 → 校验 → 入库 → 通知 | coding/research agent 自主检索、调用工具、验证并修复 |

选择原则是先用满足目标的最简单方案：若规则和成功路径可清楚枚举，workflow 通常更可预测、便宜；当路径无法预先穷举而模型的动态决策带来明显收益，再引入 agent loop，并用工具权限、预算和 eval 管住自由度。

LeAgent 的映射：`AgentHarness` 是可复用的自主 loop；`AgentSession` 提供 coding 环境和状态；CLI/TUI 是消费者。一个固定 workflow 可以在外层串联这些能力，但不应把 UI 或业务编排反向耦合进 harness。

一手依据：

- [Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) 明确区分：workflow 是 LLM/工具沿预定义代码路径编排；agent 由 LLM 动态控制过程和工具使用，并建议先从最简单方案开始。
- [ReAct 论文](https://arxiv.org/abs/2210.03629) 描述了 reasoning 与 action 交替、根据环境观察更新后续行动的 agent loop 基本形态。
- [OpenAI Agents SDK：Running agents](https://openai.github.io/openai-agents-python/running_agents/) 展示运行器在工具调用与模型响应之间循环，直至产生最终输出的运行语义。

## 面试回答的诚实边界

- **LeAgent 已有：**确定性工具/Runtime 测试、原生小型 benchmark、隔离 trial、确定性 evaluator、trajectory 与报告。
- **LeAgent 尚未有：**面向生产流量的 A/B、在线观测闭环、通用 Hook API、完整安全审批/策略系统。
- **因此：**用“现有证据 + 下一步设计”回答，明确不把 fake provider 或理论方案包装为已上线能力。
