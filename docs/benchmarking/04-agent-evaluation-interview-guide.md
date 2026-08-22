# Agent evaluation 面试指南

这份指南把话术分成三种证据标签。面试时不要省略标签所表达的边界：

- **LeAgent 已实现**：当前仓库有可指认的源码、测试或 CLI 行为。
- **τ²-bench 中实现**：来自 τ²-bench 论文或官方仓库，不是 LeAgent 产品能力。
- **未来工作**：合理的扩展方向，但尚未落地。

## 30 秒版本

**LeAgent 已实现：**“普通单元测试能证明组件正确，却不能回答模型驱动的 coding agent 能否在
真实工具循环中完成任务。我给 LeAgent 增加了一个小型原生 benchmark：三个离线 task 分别覆盖
结构化最终回答契约、精确编辑和修复失败测试；第一题的 fake 只展示脚本化只读路径，
即使 real trial 满分也不能证明自主仓库检索能力。同一条 runner 路径既能跑 deterministic fake
provider 验证评测管线，也能跑已配置的 real provider。评分器直接检查最终回答或 workspace，
不用 LLM judge；collector 保存由 `TrajectoryStep` 组成的 tool call、结果与最终消息轨迹，
`TrajectoryStep` 不含 `usage`。provider 用量放在 `TrialResult.usage` 单独记录和聚合。多次
trial 再聚合成功率与 pass@k。fake 的 100% 只说明管线健康，不代表任何模型能力。”
入口与状态定义见 [`src/le_agent_coding/cli.py`](../../src/le_agent_coding/cli.py)和
[`src/le_agent_coding/benchmark/models.py`](../../src/le_agent_coding/benchmark/models.py)。

**LeAgent 已实现证据：**task 加载见
[`src/le_agent_coding/benchmark/tasks.py`](../../src/le_agent_coding/benchmark/tasks.py)，执行循环见
[`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)，评分见
[`src/le_agent_coding/benchmark/evaluators.py`](../../src/le_agent_coding/benchmark/evaluators.py)，轨迹见
[`src/le_agent_coding/benchmark/collector.py`](../../src/le_agent_coding/benchmark/collector.py)。

## 2 分钟版本

**LeAgent 已实现（Situation）：**“LeAgent 原来有 provider、agent loop、tools、session 和 CLI 的测试，
但这些测试主要回答软件组件是否正确，缺少端到端的 agent task 结果。与此同时，一个大规模
benchmark 会引入数据下载、容器和昂贵模型调用，不适合作为第一阶段教学实现。”
当前应用层入口见 [`src/le_agent_coding/cli.py`](../../src/le_agent_coding/cli.py)。

**LeAgent 已实现（Task）：**“目标是做一个最小但完整的 evaluation vertical slice：task 要可信且
离线；agent 必须走正常 `CodingSession` 和 coding tools；评分要确定、可复现；运行后既有
总体指标，也保留可诊断的 trajectory；fake 与 real 的意义必须严格分开。”
组件边界见 [`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)和
[`src/le_agent_coding/benchmark/evaluators.py`](../../src/le_agent_coding/benchmark/evaluators.py)。

**LeAgent 已实现（Action）：**“我把 benchmark 放在 `le_agent_coding`，没有污染可复用的
`le_agent`。manifest loader 只接受三个硬编码 evaluator 名；runner 为每个 trial 复制全新
workspace，串行驱动 `CodingSession`，在超时后分类状态，并在正常结束后调用隐藏 evaluator；
collector 从公开 event 归一化 assistant message 与 tool start/end；reporter 原子写 JSON 并
输出 Rich 摘要。fake provider 仍通过真实 session/tool loop，而不是直接改 fixture。核心实现见
[`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)、
[`src/le_agent_coding/benchmark/fake.py`](../../src/le_agent_coding/benchmark/fake.py)和
[`src/le_agent_coding/benchmark/reporting.py`](../../src/le_agent_coding/benchmark/reporting.py)。”

**LeAgent 已实现（Result）：**“现在可以用默认 fake 无凭据验证整条基础设施，也可以复用 LeAgent
provider 设置做真实模型 trial。每次 trial 有 `passed`、`task_failed`、`agent_failure`、
`timeout` 或 `evaluation_failure`；正常 task 未通过仍会生成完整 artifact，批处理不会把有价值
的失败样本误当程序崩溃。指标含 success rate、工具错误率、用量以及无偏 `pass@k`。实现能
诚实回答小任务上的行为，但不能外推到大型生产仓库。”
状态与指标字段见 [`src/le_agent_coding/benchmark/models.py`](../../src/le_agent_coding/benchmark/models.py)
和 [`src/le_agent_coding/benchmark/metrics.py`](../../src/le_agent_coding/benchmark/metrics.py)。

## 深入追问

### 为什么不只看最终回答？

**LeAgent 已实现：**最终 reward 回答“任务是否达标”，trajectory 回答“为什么”。同样的零分可能是
没找到文件、工具报错、错误修改、provider 中止或 evaluator 故障；只看最后一句话无法区分。
collector 记录序号、相对时间、tool name/arguments、结果、error 与截断标志，并只采集完整
assistant message，避免重复保存 streaming partial。证据见
[`src/le_agent_coding/benchmark/collector.py`](../../src/le_agent_coding/benchmark/collector.py)和
[`src/le_agent_coding/benchmark/models.py`](../../src/le_agent_coding/benchmark/models.py)。

### 为什么不用 LLM judge？

**LeAgent 已实现：**首版三个 task 都能用代码定义成功，因此 deterministic grader 更便宜、更快、
更可复现，也避免 judge model 版本和 prompt 改动造成漂移。`repository_lookup` 检查必要结构化
事实；编辑与 bug 修复用独立进程执行行为断言，且隐藏 case 不依赖 agent 自报。证据见
[`src/le_agent_coding/benchmark/evaluators.py`](../../src/le_agent_coding/benchmark/evaluators.py)。

**未来工作：**若 task 目标天然主观，可增加人工 rubric 或可选 LLM judge，但要保存 judge
model、prompt、版本和原始依据，并用人工样本做校准；它不应替代可确定评分的检查。

### 如何处理随机性？

**LeAgent 已实现：**CLI 支持 `--trials` 和记录 `--seed`，每个 task/trial 使用新的 workspace，结果
按 task 聚合成功数并报告 `pass@k`。真实 provider 是否严格遵守 seed 由 provider 决定，所以
artifact 中的 seed 是实验元数据，不能宣称让外部模型完全确定。证据见
[`src/le_agent_coding/cli.py`](../../src/le_agent_coding/cli.py)、
[`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)和
[`src/le_agent_coding/benchmark/metrics.py`](../../src/le_agent_coding/benchmark/metrics.py)。

### 为什么 task failure 仍 exit 0？

**LeAgent 已实现：**`task_failed` 表示 agent 正常完成、evaluator 正常工作，只是必要 check 没全部
通过；这是 benchmark 的有效观测，不是 CLI 基础设施错误。只有 `agent_failure`、`timeout`、
`evaluation_failure` 导致非零退出。这样 CI 可以区分“成功生成了一份低分报告”和“根本没完成
评测”。证据见 [`src/le_agent_coding/cli.py`](../../src/le_agent_coding/cli.py) 的
`_run_benchmark_cli` 与 [`src/le_agent_coding/benchmark/models.py`](../../src/le_agent_coding/benchmark/models.py)。

### 如何避免 evaluator leakage？

**LeAgent 已实现的工程卫生措施（不是安全能力）：**task prompt 与复制进 trial workspace 的
fixture 不包含 evaluator 实现，manifest 只能引用 `KNOWN_EVALUATORS`，不能声明任意
import 或 shell command。evaluator 源码实际仍位于 LeAgent 的 `le_agent_coding` 项目包内，只是没有被复制进
trial workspace。这种 prompt/fixture 布局只降低偶然泄漏，不防御主动探测。证据见
[`src/le_agent_coding/benchmark/tasks.py`](../../src/le_agent_coding/benchmark/tasks.py)、
[`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)和
[`src/le_agent_coding/data/benchmark_tasks/failing_test_fix/task.toml`](../../src/le_agent_coding/data/benchmark_tasks/failing_test_fix/task.toml)。

**LeAgent 已实现的边界：**文件工具可使用绝对路径或 `..` 父路径越过临时 workspace，
shell tool 则在宿主机权限下运行。因此“evaluator 没放进 fixture”不等于“agent 不可能读到”。
当前工具创建路径见
[`src/le_agent_coding/tools.py`](../../src/le_agent_coding/tools.py)。

**未来工作：**真正防止 evaluator leakage 需要把 trial 放入受限 worker 或 container，仅挂载
必要 workspace，限制网络、进程和资源，并在 agent 不可访问的控制面运行 evaluator。

### pass@k 怎么推导？

**LeAgent 已实现：**若一个 task 跑 `n` 次、其中 `c` 次成功，不放回选取 `k` 个 trial，全部失败的
组合数是 `C(n-c, k)`，所有组合数是 `C(n, k)`；因此至少一次成功的无偏估计为
`1 - C(n-c,k) / C(n,k)`。实现限制 `1 <= k <= n`，先逐 task 计算，再做 macro average。
证据见 [`src/le_agent_coding/benchmark/metrics.py`](../../src/le_agent_coding/benchmark/metrics.py)。

**τ²-bench 中实现：**原始 τ-bench 还强调 `pass^k`，即 k 次全部成功的一致性指标。它和 LeAgent
当前 `pass@k` 不是同一指标，不能混用。定义见
[τ-bench 论文](https://arxiv.org/abs/2406.12045)。

### 如何扩展沙箱？

**未来工作：**先定义 runner 与执行后端的协议，让 task 输入、超时、resource limits、输出
artifact 和清理结果可序列化；再用一次性容器或受限 worker 执行 agent 和 evaluator，禁用默认
网络、只挂载 task、设置 CPU/内存/磁盘/进程上限，并将 credential 仅注入 provider 进程。
迁移后要保持当前 `TrialResult` 状态语义，避免把容器启动错误记成 task failure。

### 如何扩展并发？

**未来工作：**在 task/trial 级增加有上限的 worker pool，而不是并发同一个 workspace；每个
worker 保持独立目录和 collector，聚合层按稳定键排序。还要增加 provider rate-limit、重试
预算、全局取消、成本上限和原子 artifact 合并。当前 runner 明确是串行循环，证据见
[`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)。

## τ-bench 与 τ²-bench 的 30 秒介绍

**τ²-bench 中实现：**“τ-bench 评估客服 agent 能否在多轮用户对话中遵守 domain policy、调用
API tools，并让最终 DB 与沟通信息满足目标；用户由 LLM 模拟但不能操作环境，是
single-control。τ²-bench 把技术支持扩展为 dual-control：agent 和 user simulator 分别用工具
观察、改变共享动态环境，从而测量 agent 除了推理之外，能否准确指导用户协作。user simulator
被工具和可观察状态约束，任务可以用程序化 assertions 验证。”论文证据见
[τ-bench](https://arxiv.org/abs/2406.12045)与
[τ²-bench](https://arxiv.org/abs/2506.07982)。

## τ-bench 与 τ²-bench 的 2 分钟介绍

**τ²-bench 中实现：**“原始 τ-bench 解决了两个缺口：单次 function call 测试看不到长期用户
交互，也看不到复杂 policy adherence。它构造 airline/retail domain，用数据库、API、policy
和 task 驱动 LLM user 与 agent 对话，以终态 DB 和必须传达的信息做快速 rule-based reward，
再用 multi-trial 的 `pass^k` 观察一致性。”

**τ²-bench 中实现：**“它的限制是 user 仍是被动信息源。τ²-bench 新增 telecom
dual-control domain：agent 侧 CRM 和 user 侧模拟设备共同构成 shared state，双方只有局部
观察和不同工具。agent 可能要让用户查状态栏、切数据开关，自己再改后台配置；成功依赖推理、
通信和协调。telecom task 从 initialization、solution、assertion 原子组件组合，user simulator
被工具结果约束。论文用 no-user 等 ablation 分离推理失败与协调失败。”

**τ²-bench 中实现：**“评分不是把一条 reference trajectory 当唯一答案。官方 schema 由
`reward_basis` 决定哪些 component 相乘：DB、COMMUNICATE、ACTION、ENV assertion 或实验性
NL assertion。默认 reference actions 多用于在 gold environment 构造目标 DB 或诊断；仅当
`ACTION` 明确进入 basis 才硬匹配路径。CLI 支持多 trial、trajectory viewer 和 leaderboard。”
评分细节见[官方 evaluation 文档](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md)。

**τ²-bench 中实现的版本边界：**“2025 论文的文本 simulation 是轮流行动。当前官方 `main`
到 2026 年已演进为 τ³-bench，并新增 voice full-duplex/tick orchestrator；这是仓库后续能力，
不是 τ² 论文的原始结论。”当前能力见
[官方仓库](https://github.com/sierra-research/tau2-bench)与
[CLI 文档](https://github.com/sierra-research/tau2-bench/blob/main/docs/cli-reference.md)。

## τ²-bench 与 SWE-bench 对比

**τ²-bench 中实现：**τ²-bench 的基本单元是客服 domain 中 agent/user/environment 的多轮协作，
重点是 policy、局部观察、双方 tools 与共享终态。结果可能依赖 agent 能否让用户执行正确动作。

**τ²-bench 中实现（与对比基准的差异）：**τ²-bench 不以代码 patch 为评价对象。对照而言，
SWE-bench 的基本单元是真实 GitHub issue + repository，agent 生成修改代码库的 patch，由测试
环境判断问题是否解决；当前 SWE-bench 官方 harness 使用 Docker 改善复现。来源见
[SWE-bench 论文](https://arxiv.org/abs/2310.06770)和
[官方仓库](https://github.com/SWE-bench/SWE-bench)。

**LeAgent 已实现：**LeAgent mini benchmark 在交互形态上更接近小型 coding-agent eval，在工程思想上
借鉴了 task/environment/trajectory/reward 分层，但只有三个可信 fixture、临时目录和确定性
grader。它既不是 SWE-bench adapter，也不是 τ²-bench adapter。证据见
[`src/le_agent_coding/data/benchmark_tasks`](../../src/le_agent_coding/data/benchmark_tasks)与
[`src/le_agent_coding/benchmark`](../../src/le_agent_coding/benchmark)。

## 8 个面试官挑战问题

### 1. “fake 能全过，是否说明 LeAgent agent 很强？”

**LeAgent 已实现：**不能。fake 是按 task 编写的 scripted provider response，知道如何调用工具解决
fixture；它验证 event 组装、tool dispatch、collector、evaluator、reporting 的集成。终端也会
明确警告不代表模型能力。证据见
[`src/le_agent_coding/benchmark/fake.py`](../../src/le_agent_coding/benchmark/fake.py)和
[`src/le_agent_coding/benchmark/reporting.py`](../../src/le_agent_coding/benchmark/reporting.py)。

### 2. “为什么不用模型最后说‘测试通过’作为得分？”

**LeAgent 已实现：**模型声明不是证据。编辑任务由 evaluator 重新 import/执行行为断言；修 bug
任务另跑可见测试和隐藏 whitespace case。reward 是所有 `EvaluationCheck.passed` 的合取。
证据见 [`src/le_agent_coding/benchmark/evaluators.py`](../../src/le_agent_coding/benchmark/evaluators.py)。

### 3. “不同正确 patch 会不会被误判？”

**LeAgent 已实现：**`targeted_edit` 和 `failing_test_fix` 检查行为而非 patch 文本，因此不同实现只要
满足新行为与回归条件都可通过；`repository_lookup` 则只检查题面要求的结构化事实，
所以 real 满分也不证明 agent 曾自主检索仓库或沿调用链定位。当前
grader 仍可能因测试覆盖不足产生 false positive，这是小 task 设计需要持续审查的风险。证据见
[`src/le_agent_coding/benchmark/evaluators.py`](../../src/le_agent_coding/benchmark/evaluators.py)。

### 4. “三个 trial 有一个成功，pass@2 是多少？”

**LeAgent 已实现：**`n=3,c=1,k=2`，全失败的组合占 `C(2,2)/C(3,2)=1/3`，所以
`pass@2=2/3`。这表示随机取两次至少一次成功的估计，不表示连续两次都成功。实现见
[`src/le_agent_coding/benchmark/metrics.py`](../../src/le_agent_coding/benchmark/metrics.py)。

### 5. “为什么 evaluator 崩溃不能直接给 agent 零分？”

**LeAgent 已实现：**因为这会把测量系统故障混入能力失败，污染成功率并误导模型比较。LeAgent 用
`evaluation_failure` 单独记录，基础设施状态让 CLI 非零退出；正常 check 不通过才是
`task_failed`。证据见 [`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)。

### 6. “临时目录就是 sandbox 吗？”

**LeAgent 已实现：**不是。它隔离不同 trial 的文件副本并支持清理，但 shell tool 没有 OS 级安全
边界。只允许项目内可信 task；外部不可信 fixture 需要容器或 worker 隔离后才能安全支持。
复制与清理见 [`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)。

### 7. “为什么不要求 agent 走参考步骤？”

**LeAgent 已实现：**LeAgent 的行为评分器检查终态/结果，不要求 patch 形状；这样不会惩罚等价解。
证据见 [`src/le_agent_coding/benchmark/evaluators.py`](../../src/le_agent_coding/benchmark/evaluators.py)。

**τ²-bench 中实现：**官方 `evaluation_criteria.actions` 通常只是一条 reference trajectory，
用于构造目标 DB 或诊断；只有 `ACTION` 被加入 `reward_basis` 才硬匹配。不能把 reference action
普遍说成唯一正确路径。来源见[官方 evaluation 文档](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md)。

### 8. “结果能直接拿去做模型排行榜吗？”

**LeAgent 已实现：**当前 artifact 记录 provider/model、git commit、trial、usage 和检查详情，适合
本地对照与失败分析；但只有三个小 task，缺少 task-set version、置信区间、显著性分析、污染
审计与托管提交校验，因此不应宣称是有统计代表性的公共排行榜。字段证据见
[`src/le_agent_coding/benchmark/models.py`](../../src/le_agent_coding/benchmark/models.py)。

**未来工作：**扩大经审计的数据集，冻结版本，容器化环境，预注册实验设置，报告置信区间与
成本，并增加可验证的提交协议后，才适合讨论公开榜单。

## 项目局限

**LeAgent 已实现的当前边界：**只有 `repository_lookup`、`targeted_edit`、`failing_test_fix` 三个
小 task；它们便于教学和端到端测试，却不能代表复杂生产代码库。清单见
[`src/le_agent_coding/data/benchmark_tasks`](../../src/le_agent_coding/data/benchmark_tasks)。

**LeAgent 已实现的当前边界：**runner 串行执行；没有 task 并发、provider rate-limit 协调、Docker、
VM、网络隔离或 OS 级资源限制。临时 workspace 只是运行间隔离，不是安全边界。证据见
[`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)。

**LeAgent 已实现的当前边界：**reward 是 binary，指标只有成功率、时长、工具调用/错误、provider
上报 usage 与 `pass@k`；没有 `pass^k`、置信区间、方差分解或显著性分析。证据见
[`src/le_agent_coding/benchmark/metrics.py`](../../src/le_agent_coding/benchmark/metrics.py)。

**LeAgent 已实现的当前边界：**fake 脚本带 task-specific solution；real 模式会产生外部 API 成本，
并受 provider 随机性、服务抖动、模型版本和凭据配置影响。seed 被记录，但不保证 provider
采样完全可复现。证据见 [`src/le_agent_coding/benchmark/fake.py`](../../src/le_agent_coding/benchmark/fake.py)
和 [`src/le_agent_coding/cli.py`](../../src/le_agent_coding/cli.py)。

**LeAgent 已实现的当前边界：**没有 user simulator、dual-control、half/full-duplex 双方编排、
τ²-bench adapter、SWE-bench 兼容层、trajectory Web viewer 或 leaderboard 上传。当前公开模型见
[`src/le_agent_coding/benchmark/models.py`](../../src/le_agent_coding/benchmark/models.py)。

**未来工作：**优先增加 task 数量与版本化，再加安全执行后端和有限并发；有足够 trial 后加入
不确定性报告；只有客服评测成为明确目标时，才设计独立 τ²-bench adapter。

## 弱回答

### 弱回答一：“fake 跑出 100%，证明我们的模型三个任务全会。”

**LeAgent 已实现的正确说法：**fake 不是被测模型，而是带 task solution 的确定性脚本。100% 只能
证明当前 fixture、真实工具循环、trajectory、grader 和报告管线能一起工作。模型能力必须看
real provider 的独立 trial，并同时披露 provider/model、成本与样本规模。证据见
[`src/le_agent_coding/benchmark/fake.py`](../../src/le_agent_coding/benchmark/fake.py)。

### 弱回答二：“τ²-bench 会检查 agent 是否严格复现 reference action。”

**τ²-bench 中实现的正确说法：**默认 DB evaluator 重放 reference actions 得到 gold 终态，agent
走不同但等价的路径仍可通过。仅当 `ACTION` 明确进入 `reward_basis` 时才要求匹配该路径。

### 弱回答三：“有临时目录，所以可以安全运行网上下载的任意 task。”

**LeAgent 已实现的正确说法：**临时目录隔离副本和清理生命周期，不限制宿主 shell 的绝对路径、
父路径、网络或资源。当前只能运行项目内可信 task。证据见
[`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)。

### 弱回答四：“pass@k 越低代表 agent 越稳定。”

**LeAgent 已实现的正确说法：**`pass@k` 是至少一次成功的概率，通常随 k 增加而不下降；要表达
连续 k 次全部成功的一致性，应讨论 τ-bench 的 `pass^k`，而 LeAgent 尚未实现该指标。证据见
[`src/le_agent_coding/benchmark/metrics.py`](../../src/le_agent_coding/benchmark/metrics.py)。

### 弱回答五：“LeAgent 已经复现 τ²-bench。”

**LeAgent 已实现的正确说法：**LeAgent 只借鉴 evaluation 的概念分层。当前 benchmark 是单 agent 的
mini coding task，没有 user simulator、共享双侧状态或 dual-control，运行命令和结果 schema
也与 τ²-bench 不同。证据见
[`src/le_agent_coding/benchmark/runner.py`](../../src/le_agent_coding/benchmark/runner.py)。
