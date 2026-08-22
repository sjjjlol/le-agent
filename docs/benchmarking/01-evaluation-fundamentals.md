# Agent 评测基础：从测试到可解释的 benchmark

这篇教程面向第一次系统接触 agent evaluation 的贡献者。它讨论的不是“怎样让一次演示看起来成功”，而是怎样把能力问题变成可重复运行、可以诊断、不会把基础设施故障误算成模型失败的实验。LeAgent 的首版原生 benchmark 很小：只有三个内置 task、确定性 evaluator 和串行 runner。正因为小，我们可以看清每个概念的职责，也必须克制地解释结果。

LeAgent 的核心评测数据类型在 [`src/le_agent_coding/benchmark/models.py`](../../src/le_agent_coding/benchmark/models.py)，确定性评分实现位于 [`src/le_agent_coding/benchmark/evaluators.py`](../../src/le_agent_coding/benchmark/evaluators.py)，指标计算位于 [`src/le_agent_coding/benchmark/metrics.py`](../../src/le_agent_coding/benchmark/metrics.py)。阅读本文时可以随时沿这些路径核对代码。

## Unit test、integration test 与 agent eval

这几类验证都能发现问题，但回答的问题不同。

| 方法 | 固定什么 | 观察什么 | 适合回答的问题 |
|---|---|---|---|
| unit test | 输入、依赖和局部边界 | 一个函数或类的确定行为 | `pass@k` 公式是否正确、非法 manifest 是否被拒绝 |
| integration test | 多个真实组件和可控替身 | 组件之间能否正确协作 | fake provider 是否真的经过 session、工具、collector 和 evaluator |
| offline agent eval | task set、运行协议、模型配置 | agent 在独立 trial 中的结果与轨迹 | 某模型在给定条件下完成这些 task 的概率和失败模式 |
| benchmark | 一套版本化、可比较的 offline eval 协议 | 跨模型、版本或策略的统一指标 | 在同一 task set 和预算下，两个配置如何比较 |
| online eval | 生产流量、真实用户与线上约束 | 实际效果、安全、回退和长期反馈 | 上线后的 agent 是否帮助用户且没有造成不可接受风险 |

Unit test 可以证明 `estimate_pass_at_k` 对已知数字算对，却不能证明模型会修 bug。Integration test 可以证明 fake 脚本通过真实工具链完成 fixture，却不能把脚本的满分称为模型能力。Offline eval 可以在受控条件下比较配置，但它仍是现实工作的近似。Online eval 最接近真实分布，也最昂贵、最难控制，并且涉及用户隐私、安全与实验伦理。LeAgent 当前只实现小型 offline benchmark 及其基础设施测试，没有 online eval 系统。

“Eval”和“benchmark”也不是严格同义词。Eval 是一次有目标的测量活动，可以只为诊断某个回归；benchmark 强调固定 task、协议、版本和汇总规则，以便重复与比较。把临时 prompt 集合叫作 benchmark，却不保存版本、模型、trial 数和评分逻辑，会产生虚假的可比性。

## 基本对象：task、environment、agent 与 policy

一个 **task** 是待解决问题的可执行定义，通常包含 prompt、初始 fixture、时限和评分器名称。LeAgent 的 task 不只是自然语言题目；工作区初始状态与 evaluator 同样属于题目语义。改变任何一项，都可能是在改变试卷。

**Environment** 是 agent 行动并得到反馈的环境。对 LeAgent 而言，它是一次性复制的 workspace 加上 `read`、`write`、`edit`、`bash`、`web_search` 等 coding tools。其中 web search 引入外部网络、索引更新和配额变量，严格可复现的 task 应禁用它或注入固定 Fake Backend。临时目录提供 trial 间隔离，但不是操作系统安全沙箱。

**Agent** 是接收上下文、选择下一步并与环境交互的系统整体。在本项目的 real 模式中，它由已配置模型、`CodingSession`、agent loop、system prompt 和 coding tools 共同构成。只写模型名不足以完整描述被测对象。

**Policy** 可以理解为 agent 在当前历史下选择下一条消息或工具调用的策略。模型权重、system prompt、采样参数、工具描述、上下文裁剪和错误恢复都会改变这个策略。因此比较结果时，应固定或记录 provider、model、代码版本、task set 版本和运行参数，而不是把差异都归因于模型名称。

**Check** 是 evaluator 产生的一项可诊断断言，例如“可见测试通过”或“隐藏空白字符边界通过”。多个 check 汇成一次 evaluation result。LeAgent 只有全部必要 check 通过才给 `reward = 1`，否则为 `0`。这让“成功”含义清楚，也保留了局部失败线索。

## Trajectory、evaluator 与 reward

**Trajectory** 是一次 trial 中按时间排列的交互轨迹：assistant 消息、工具调用参数、工具结果和错误标志等。最终答案只告诉我们 agent 说了什么，trajectory 还能回答它读过哪些文件、是否先运行失败测试、工具在哪里出错、修复后是否验证。相同 reward 的两次运行可能有完全不同的风险和效率，因此 trajectory 是失败分析与审计材料，而不是装饰性日志。

在 LeAgent 的数据契约中，每个 `TrajectoryStep` 不含 `usage`；provider 上报的 token 用量放在
`TrialResult.usage` 单独保存，再由 run metrics 单独聚合。用量可与 trajectory 联合分析效率，但不是
trajectory step 的字段。

**Evaluator** 是把 run artifact 映射为评分结果的程序或评审流程。它必须在 agent 行动结束后独立判断，不应相信最终回答里“测试已通过”的自述。LeAgent 的 evaluator 直接检查最终 workspace 或运行自己的断言，并返回若干 check。

**Reward** 是单个 trial 的目标信号。LeAgent 使用二元 reward：全部必要 check 通过为一，否则为零。**Metric** 则是面向一批 trial 的统计摘要，例如成功率、平均时长、工具错误率或 `pass@k`。Reward 和 metric 不应混为一谈：时长可以帮助诊断效率，却没有被 LeAgent 加权进 reward；一个快速但错误的 run 不会因为便宜而获得“半成功”。

Outcome-based 评分只检查最终结果。例如代码行为和隐藏边界全部正确，就不要求 agent 产生某种固定 patch。优点是允许多种正确路径，也更贴近用户目标；缺点是 evaluator 必须覆盖重要边界，否则可能奖励投机解法。Trajectory-based 评分检查过程，例如是否泄露 secret、是否调用禁止工具、是否提供关键证据。它适合安全和流程约束，但过度规定步骤会惩罚同样正确的创新方案。实践中通常让 outcome 决定主要 reward，再把必要的安全约束做成 trajectory check，并把其余过程指标留作诊断。

### 三种评分者怎样组合

**Deterministic grader** 用测试、静态断言或结构化规则评分。它便宜、快速、可重复，失败原因容易定位；局限是覆盖范围有限，也可能被针对规则的投机输出骗过。LeAgent 首版只使用这种方式，并通过隐藏边界检查降低“只改可见测试”的风险。

**LLM judge** 能评价解释质量、风格、需求遵循等难以写成断言的内容，但结果受 judge 模型、prompt、位置偏差和随机性影响，还增加成本与供应商依赖。使用时应保存 judge 版本与 rubric，随机交换候选顺序，校准与人工标签的一致性，并允许“不确定”而不是强迫二选一。LeAgent 当前没有实现 LLM judge。

**人工评审** 最能处理新颖性和复杂语境，也能发现 rubric 漏洞；代价是慢、贵、评审者间存在分歧。需要盲评、清晰 rubric、重叠样本和一致性统计。一个稳健组合是：确定性 grader 覆盖可执行正确性，LLM judge 对大批开放式样本做带置信度的初筛，人工评审校准 judge、处理冲突和抽查高风险样本。不能因为三者都给分，就把不可比的分数简单相加。

## 指标不等于一个总分

**Success rate** 是通过 trial 数除以总 trial 数，对二元 reward 等于平均 reward。它直观，但必须同时给出样本量和置信区间；三个 task 各跑一次的百分比不能支持精细排名。

**工具错误率** 是 tool error 数除以 tool call 数。它能暴露参数错误、命令失败或环境问题，但不等于任务失败：诊断过程中的首轮失败测试在 LeAgent 工具层可能仍是一次正常完成的 `bash` 调用，命令输出包含退出码，却未必被标记为工具执行错误。解释指标时必须遵循实现语义。

**延迟** 至少应区分端到端 trial 时长、模型首 token 延迟与工具耗时。LeAgent 首版只汇总 trial duration。**Token** 用量只有 provider 实际上报时才有意义；缺失值应为 `null`，不能凭文本长度伪造。**成本** 还需要当时的模型价格、缓存计费和货币单位；LeAgent 记录 token，但当前不会把它换算为金额。比较质量、延迟和成本时应报告多维结果或 Pareto 前沿，而不是随意造一个权重总分。

失败分类本身也是重要指标。Agent 能正常结束但 check 不全通过，是能力数据；provider 崩溃、runner timeout 或 evaluator 崩溃，是基础设施类故障。若把两者都记成零分，模型质量会被网络与评测器稳定性污染。

## pass@k

当每个 task 独立生成 `n` 个候选，其中 `c` 个通过，而用户可以从 `k` 个不放回候选中至少得到一个成功结果时，常用无偏估计为：

```text
pass@k = 1 - C(n - c, k) / C(n, k)
```

以 `n = 4`、`c = 2` 为例。对于 `k = 1`，从四个候选中抽一个失败候选的概率是 `C(2,1) / C(4,1) = 2/4`，所以 `pass@1 = 1 - 1/2 = 0.5`。对于 `k = 2`，两次都落在两个失败候选上的概率是 `C(2,2) / C(4,2) = 1/6`，所以 `pass@2 = 1 - 1/6 = 5/6`。这不是把 `pass@1` 简单乘二，因为两个抽样事件并不按有放回方式独立发生。

当 `c = 0` 时所有合法 `pass@k` 都为零；当 `c = n` 时都为一。`k` 必须满足 `1 <= k <= n`。LeAgent 先逐 task 计算，再对拥有该 `k` 的 task 做 macro average，避免 trial 较多的 task 自动获得更大权重。`pass@k` 描述“给定多个尝试，至少一次成功”的机会，不代表用户一定能识别哪个候选正确，也不能掩盖单次尝试成本随 `k` 增加。

## 可复现性与数据污染

记录一个 **seed** 很有用，但 seed 不能让真实模型完全确定。远程服务可能更新模型权重与推理栈；并行调度、浮点计算、缓存、工具输出时间、网络重试和未暴露的采样细节也会改变结果。有些 provider 不承诺接受或严格执行 seed。LeAgent 当前把请求 seed 写入 artifact，不能据此宣称 real trial 可逐字复现。

因此需要 **repeated trials**。重复运行可以估计方差、发现偶发工具错误，并计算 `pass@k`。应在比较前固定 trial 数和停止规则，避免看到好结果就提前停止。模型非确定性很高时，报告均值之外还要给分布或置信区间；基础设施故障应单独列出，而不是悄悄删除或当作 task failure。

**Contamination** 指模型在训练、提示缓存或公开资料中见过评测题与答案，因记忆而非泛化得到高分。公开小 task 尤其不能被包装成未知能力测试。缓解方法包括保留未公开 test set、定期轮换、检测与训练语料的相似性，并把公开开发题与最终报告题分开。

**Evaluator leakage** 指 agent 获知隐藏 check、参考答案或评分器细节，从而针对评分规则而不是真实目标优化。LeAgent 的 task prompt 和被复制的 fixture 不包含 evaluator 实现，evaluator 源码仍在 `le_agent_coding` 项目包内，只是不在 trial workspace 副本里。这只降低偶然泄漏，不是已实现的安全能力：文件工具接受绝对路径和 `..` 父路径，shell 仍在宿主机权限下运行。真正防止泄漏需要未来的受限 worker 或 container，并把 evaluator 放在 agent 不可访问的控制面。

**Flaky test** 是相同代码在相同声明条件下仍偶发通过或失败，例如依赖时钟、网络、随机顺序或竞争条件。它会直接污染 reward，必须先隔离、重复验证或修复。**Infrastructure failure** 是 provider、工具分发、runner、存储或 evaluator 无法工作；它说明测量没有成功完成，而非 agent 解题错误。LeAgent 用独立状态区分这些情况。

### Split 与 task set 版本

合理的 train/dev/test split 应按泄漏风险分组，而不是把高度相似的题目随机拆散。Train 可用于微调或策略训练；dev 可反复用于 prompt、工具和错误恢复调试；test 只在决策节点运行，并限制查看详细答案。若多个 task 来自同一仓库或共享模板，应按仓库、问题族或时间切分，避免近重复样本跨集合。

Task set 必须版本化。至少记录每个 manifest、fixture、evaluator 和聚合规则的内容版本或 Git commit。修正 evaluator bug 后，应发布新版本并说明旧分数不可直接比较；仅改标题而不改语义可以保留版本，但也应记录变更。模型、provider、system prompt、工具实现和预算同样属于实验条件。

## LeAgent 内置 task 能说明什么

`repository_lookup` 检查最终回答能否复述 `welcome/render.py`、`format_salutation` 和 `build_welcome` 的调用关系。题面已经给出目标路径、符号与调用关系，评分器只检查最终回答是否包含这些信息，不读取 trajectory。因此，即使 real trial 获得满分，也不能证明 agent 自主沿调用链找到答案；fake provider 依次读取三个文件，只是在演示这条脚本化工具路径。

`targeted_edit` 用行为检查验证免运费边界修改及会员规则保持。`failing_test_fix` 的 evaluator 运行可见测试和额外的隐藏空白输入断言；real trial 的 reward 只证明可见测试与隐藏行为检查通过，不要求某种修复过程。观察失败、定位与编辑步骤只由 fake provider 的脚本化 trajectory 展示，评分器不会据此断言真实 agent 确实按这个顺序调查、定位或修改。三个 task 因而提供的是最终回答与 workspace 结果的受控检查样例，同时可验证 LeAgent 的 session、工具、trajectory、评分、汇总与报告管线。

它们不能说明模型能处理大型仓库、长期规划、复杂依赖、并发修改、模糊产品需求或真实安全威胁；也不能代表 SWE-bench、τ²-bench 或生产 coding 成功率。三个 task 的统计样本太少，不适合做细微模型排名。Fake provider 更不能作为能力成绩：脚本内含 task-specific 解法，它的满分只表示当前评测管线健康。正确的结论应限定为“在这个版本、这些 task、这些运行条件下观察到的结果”。

用于学习与管线自检时，可以运行：

```bash
uv run le-agent benchmark --provider fake
uv run le-agent benchmark --provider openai --model gpt-5 --trials 4
```

第二条命令会调用外部模型并可能产生费用；实际 provider 和 model 必须已在 LeAgent 配置中可用。下一篇将沿真实源码解释这两条命令如何变成 JSON artifact。
