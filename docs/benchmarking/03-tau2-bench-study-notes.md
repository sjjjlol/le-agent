# τ²-bench 学习笔记：从单方工具调用到双方协作

这篇笔记讨论的是 Sierra Research 的 `τ-bench` 与 `τ²-bench`，以及它们能给 LeAgent
原生 mini coding benchmark 什么启发。三者名字相近，但不是同一套实现：运行
`le-agent benchmark` 不会运行 τ²-bench，LeAgent 目前也没有 τ²-bench adapter、客服 domain、
user simulator 或 dual-control orchestrator。

资料核验日期为 **2026-08-08**。研究结论以 2024 年的
[τ-bench 论文](https://arxiv.org/abs/2406.12045)和 2025 年的
[τ²-bench 论文](https://arxiv.org/abs/2506.07982)为准；工程行为以
[官方仓库](https://github.com/sierra-research/tau2-bench)、
[evaluation 文档](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md)和
[CLI 文档](https://github.com/sierra-research/tau2-bench/blob/main/docs/cli-reference.md)为准。
需要注意，官方仓库的 `main` 在本次核验时（commit
[`668d3bcd`](https://github.com/sierra-research/tau2-bench/tree/668d3bcd135c02aa3438f987ef45735b7c163ee3)）
已经演进到 τ³-bench，并加入 voice
full-duplex 和 knowledge domain。下文会明确区分“2025 年 τ² 论文”与“2026 年当前仓库”，
不能把后者的新能力倒推成前者的论文贡献。

## τ-bench：single-control 的起点

原始 τ-bench 研究客服型 Tool-Agent-User interaction。一个 domain 提供业务 policy、
数据库 API tools、数据库数据和 task；语言模型 agent 要在多轮对话中向 LLM 模拟用户收集
信息、遵守 policy，并调用 API 完成航空或零售请求。这里是 **single-control**：只有 agent
能通过工具改变环境，user simulator 负责以自然语言给出目标、偏好与补充信息，本身没有
改变世界的工具。

这种设置已经比“给问题、收一句答案”丰富。task 的真实难点可能来自数据库推理、长期对话、
规则约束和用户表达的随机性。论文的 rule-based reward 不是比较一段固定回复，而是组合两类
结果：对话结束后的 DB 是否等于标注目标状态，以及 agent 是否向用户 COMMUNICATE 了必需
信息。这样，不同措辞和不同只读查询顺序只要得到相同业务结果，都可以成功。论文还用多次
独立 trial 观察可靠性，并区分两种常被混淆的指标：`pass@k` 是 k 次中至少一次成功，
`pass^k` 是 k 次全部成功。前者衡量多次尝试发现解的机会，后者强调客服 agent 的一致性。
这些定义与公式可在[原始论文第 3 节](https://arxiv.org/pdf/2406.12045)核对。

## τ²-bench：为什么要 dual-control

技术支持场景暴露了 single-control 的边界。用户可能必须在自己的手机上切换飞行模式、开启
移动数据或观察状态栏；agent 无权代做，只能解释、询问、指导并根据反馈继续诊断。
τ²-bench 因此加入 **dual-control**：agent 和 user 都能发消息、调用各自可见的工具，并在
一个 shared state（共享世界状态）中产生可相互影响的变化。2025 论文把它形式化为
Dec-POMDP；全局状态包含 agent 侧 DB、user 侧 DB 和 interaction history，而双方看到的是
各自局部 observation。这个设计不等于给双方完全相同的工具，而是保留权限与知识不对称：
例如 agent 查 CRM，用户操作模拟手机，两边的动作共同决定问题是否真正解决。

dual-control 的主要新增难点不是“工具数量翻倍”，而是协调。agent 必须判断缺什么观察，给出
用户可执行的准确步骤，等待正确动作与结果，再决定自己的下一步。用户可能理解错误或操作
失误；agent 需要恢复，而不能假设一次指令必然执行。τ²-bench 论文用 no-user、oracle plan
等 ablation 拆分纯推理问题与沟通/协调问题，并报告从自治控制转到 dual-control 后明显的
性能下降。该结论来自[τ²-bench 论文](https://arxiv.org/abs/2506.07982)，不是 LeAgent 当前
mini benchmark 的实测结论。

## Domain、policy、tools、tasks 与 user simulator

τ²-bench 延续模块化 domain 结构。一个 domain 至少回答四个问题：环境里有什么结构化状态；
agent 必须遵守什么 policy；agent 与用户分别能调用什么 tools；task 的初始条件、用户目标和
成功条件是什么。原有 airline/retail 保留单方工具控制，新增 telecom 才集中体现双方对共享
动态环境的控制。

telecom task 不是随意拼自然语言题面。2025 论文描述了从 atomic subtask 组合任务的方法：
初始化函数制造问题，solution 函数给出可行修复动作，assertion 函数验证终态；组合器控制
问题类型、步数与覆盖面，并检查应用全部解法后断言成立、未完成全部必要步骤前断言不成立。
这使任务具有可验证的程序语义，同时仍允许对话过程变化。

**user simulator** 也不是一个知道答案后随意“演戏”的 judge。它收到 scenario instruction，
只能通过用户侧工具观察或改变自己的环境，并被提示保持 reactive：通常在 agent 指导后执行
操作。工具返回人类可读信息，环境状态限制它能声称什么。这种把 simulator 与环境耦合的方式
减少自然语言 prompt 承担的隐藏状态描述，也让用户行为更可检查。最终评价对象仍是被测 agent
能否引导和协作，不是把双方当作对称的多 agent 竞赛。

## Orchestrator：half-duplex 与 full-duplex 必须分版本理解

2025 τ²-bench 论文的文本交互是按 turn 进行的：每一步只有 agent 或 user 一方行动，行动可以
是 message 或 tool call。这对应 half-duplex orchestrator，即双方轮流生成完整消息，工具结果
同步返回。它足以表达 dual-control，因为“谁能改状态”和“双方是否同时说话”是两个正交维度：
dual-control 讨论权限，half/full duplex 讨论通信时序。

截至本次核验，官方仓库当前 `main` 的
[orchestrator 文档](https://github.com/sierra-research/tau2-bench/blob/main/src/tau2/orchestrator/README.md)
同时列出 `Orchestrator` 与 `FullDuplexOrchestrator`。前者仍是 turn-based 标准 benchmark，
trajectory 是扁平 message 列表；后者服务当前 τ³/voice streaming，每个 tick 可同时含 agent
chunk、user chunk、双方工具调用与结果，trajectory 以 tick 分组。这里的 full-duplex 是
2026 当前仓库能力，不应写成 2025 τ² 论文原有贡献。CLI 里的 `--audio-native`、tick duration
和 interruption 参数也属于这一后续演进，可在[官方 CLI 文档](https://github.com/sierra-research/tau2-bench/blob/main/docs/cli-reference.md)
核对。

## Evaluation：DB、COMMUNICATE、ACTION 与 assertions

当前 task schema 的 `evaluation_criteria` 可以包含 `actions`、`env_assertions`、
`communicate_info`、`nl_assertions` 和 `reward_basis`。各部分含义如下：

- `DB`：在新建的 gold environment 中重放 reference actions 得到目标 DB hash，再与被测
  trajectory 结束后的 DB hash 比较。等价终态即可，不要求相同读取顺序。
- `COMMUNICATE`：检查规定字符串是否出现在 agent 消息中，用来覆盖“数据库做对了，但没有把
  必要事实告诉用户”的情况。
- `ACTION`：检查 reference actions 是否出现在实际 tool calls 中。只有 task 明确把它加入
  `reward_basis` 时，它才是最终 reward 的硬门槛。
- `ENV_ASSERTION`：直接在预测终态运行结构化环境断言；telecom task 适合用它检查用户设备和
  agent 后台共同形成的状态。
- `NL_ASSERTION`：用 LLM 判断历史是否满足自然语言断言；官方 evaluation 文档将其标为实验中
  的能力。它可以做诊断，但不应伪装成无噪声的确定性真值。

最终 reward 由 `reward_basis` 列出的 component **相乘**。例如 basis 为 `[DB,
COMMUNICATE]`，则结果是 `db_reward * communicate_reward`，任何一个为零，总 reward 就为
零。airline、retail、telecom 默认仍以终态/沟通相关 basis 为主；当前文档说明 `ACTION` 只在
少量 `banking_knowledge` task 中使用。详细门控规则见
[官方 evaluation 文档](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md)。

### Reference trajectory 不是唯一正确轨迹

`evaluation_criteria.actions` 记录的是 **one reference trajectory**。默认用途是：在干净环境
重放它，构造目标 DB end state；以及计算 partial action reward，帮助诊断 agent 与一条参考
路径的相似度。agent 可以先查用户再查订单，也可以相反；只要最终 DB 等价且完成沟通，仍可
通过。将 reference action 描述为“唯一正确步骤”会错误惩罚正确但不同的路径。

只有当 `RewardType.ACTION` 被明确放入 `reward_basis`，任务才要求匹配这些 action。此时 schema
确实把该列表当作假定唯一的可接受 trajectory，所以只适合路径本身就是评价对象、且作者有
把握不存在其他有效解的 task。即便 `tau2 view` 显示 partial action reward，它通常也只是
诊断信号，不等同于官方 leaderboard 的正确性判定。这一边界是理解 τ²-bench 评分最重要的
防误读点。

## Multi-trial、trajectory view 与 leaderboard

官方 CLI 的 `tau2 run` 用 `--num-trials` 重复 task，用 `--seed` 记录随机种子，并支持 task
筛选、最大步骤、重试和当前实现中的有限并发。结果保存在 simulation artifact，`tau2 view`
可查看完整或仅失败的 trajectory；full-duplex 结果还能展开 ticks。`tau2 evaluate-trajs` 可以
重评保存的轨迹，`tau2 leaderboard` 可以按 `pass_1`、`pass_2`、`pass_3`、`pass_4` 或成本
查看排名；提交流程另有轨迹校验。可操作参数以
[官方 CLI 文档](https://github.com/sierra-research/tau2-bench/blob/main/docs/cli-reference.md)为准。

这里还要区分 `pass^k` 与 `pass@k`。原始 τ-bench 用前者回答“同一任务连续 k 次都成功的概率”，
用后者回答“k 次里至少一次成功的概率”。当前 LeAgent 原生 benchmark 只实现后者的无偏估计，
不能把 LeAgent JSON 中的 `pass_at_k` 口头称为 τ-bench 的一致性 `pass^k`。

## 与 SWE-bench 的差别

[SWE-bench 论文](https://arxiv.org/abs/2310.06770)从真实 GitHub issue 与对应 pull request
构造软件工程问题：给 agent 一个代码库和 issue，要求生成能通过评测的 patch。它关注长上下文
代码理解、跨文件修改与测试执行；官方仓库当前使用 Docker 提高可复现性。τ²-bench 则关注
客服对话、domain policy、双方工具权限、user simulator 与共享环境中的协作。两者都重视
可执行环境、trajectory 和可验证终态，但环境、交互角色、评分对象与安全成本不同。

不能因为 LeAgent 的 task 是 coding task 就称它为 SWE-bench 兼容。LeAgent 当前只有三个刻意缩小、
离线可信的 fixture，没有真实 GitHub issue 数据集、Docker image 或 SWE-bench harness。
它更像教学用的纵向切片：展示一次真实 agent/tool loop 如何被收集、确定性评分与聚合。

## 映射到 LeAgent 原生 mini benchmark

| τ²-bench 概念 | LeAgent 当前对应物 | 关键缺口 |
|---|---|---|
| domain task | 三个内置 coding fixture + prompt | 没有客服 domain 或组合式 task generator |
| policy/tools/environment | benchmark system prompt、coding tools、临时 workspace | 没有双方各自 DB 与权限模型 |
| agent | `CodingSession` + provider/model | 对应成立，但用途是 coding agent |
| user simulator | 无 | 不是被“简化”为 fake provider，而是根本未实现 |
| dual-control | 无 | 当前只有一个 LeAgent agent 操作 workspace |
| orchestrator ticks | 一次 prompt 驱动的 agent/tool loop | 无双方 half/full-duplex 对话编排 |
| trajectory | 标准化 `AgentEvent`、tool start/end 与 assistant message | 不是 τ² simulation schema |
| reward | 必要 `EvaluationCheck` 全过才为 1 | 没有 `reward_basis` 配置或 DB/COMMUNICATE 类型体系 |
| multi-trial | `--trials`、success rate、`pass@k` | 串行执行，无 `pass^k` 与显著性分析 |
| viewer/leaderboard | Rich 摘要 + JSON | 无交互 viewer、托管榜单或上传 |

LeAgent 的实际边界可从 [`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)、
[`src/le_agent_coding/benchmark/collector.py`](../../src/le_agent_coding/benchmark/collector.py)、
[`src/le_agent_coding/benchmark/evaluators.py`](../../src/le_agent_coding/benchmark/evaluators.py)和
[`src/le_agent_coding/benchmark/metrics.py`](../../src/le_agent_coding/benchmark/metrics.py)核对。特别要避免
把 fake provider 当 user simulator：fake 是 task-specific scripted solution，用来验证 LeAgent
评测管线；它既不模拟用户随机性，也不构成 dual-control。

## 可借鉴但尚未实现的 adapter boundary

如果未来确实要评估客服 tool agent，可以在 `le_agent_coding` 应用层增加独立 adapter：把外部
domain/task 映射为运行配置，把双方消息和工具事件映射成稳定 trajectory，再把外部 evaluator
结果映射为 LeAgent 报告。adapter 不应进入可移植的 `le_agent`，也不应偷换当前
`EvaluationResult` 的语义。在写代码前还需要解决依赖版本、simulation schema、凭据、成本、
并发与外部环境隔离。这个方向目前只是设计建议，不是 LeAgent 已发布能力。
