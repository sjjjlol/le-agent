# 工具与安全边界

- `read` 只能读取 workspace 和已发现的 Skill 目录。
- `write`、`edit` 在解析符号链接后仍必须位于 workspace 内，阻止 `..` 与 symlink 逃逸。
- `edit` 要求精确旧文本；多个匹配必须显式 `replace_all=true`。
- `bash` 在 workspace 中运行，默认 120 秒超时，UI 显示截断但完整输出仍在 tool result 中。
- `readonly` 只允许 read；`confirm` 对编辑和每条 bash 请求审批；`trust` 自动放行。

Session 恢复不会重放未完成的工具调用：只有完整 `message_end` 边界才会被 Harness 追加到持久日志。
