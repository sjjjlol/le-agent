# Memory 与 Compaction

这里的“memory”遵循 pi 的核心分层：**Session 是持久真源，AgentState 是可重建运行态**。它不是向量检索、embedding 或跨会话用户画像。

每个 Session 是 JSONL：第一行是 header，之后每一行都是不可变 entry。普通 entry 的 `parent_id` 指向当时的 leaf；`leaf` entry 只移动当前指针，因此分支、回退和恢复都不改写既有历史。崩溃造成的最后一条半写入 JSON 会被忽略，之前的完整 entry 保留。

构建 Context 时，系统从当前 leaf 沿 `parent_id` 回溯，并且只投影该分支：

1. 找到最后一个 compaction entry；
2. 注入它的 summary 和 retained tail；
3. 追加此后当前分支上的 message 与 branch summary；
4. 转换为 provider 所需历史。

Compaction 本身也只是追加一个 entry，包含 `summary`、`first_kept_entry_id`、`retained_tail` 和压缩前 token 估计，绝不删除旧对话。默认预留 `16384` tokens、保留最近 `20000` tokens；摘要器收到前一个摘要，以便进行增量总结。Harness 在模型窗口接近耗尽时自动压缩，手动 `/compact` 也走同一语义。
