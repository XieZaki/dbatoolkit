# P0 应急诊断简要说明

在人工指定的数据库实例上运行：

```powershell
.\.venv\Scripts\dba.exe diagnose <database_id> slow
.\.venv\Scripts\dba.exe diagnose <database_id> disk-growth
```

默认终端先显示中文应急初报，再继续补充采集并生成 `reports/` 下的自包含中文 HTML。`--json` 只在采集结束后输出一个 JSON 对象，不混入人类摘要。

Slow 初报优先呈现会话、运行请求、阻塞关系和事务证据，并列出最多五个优先核查会话。候选是人工调查顺序，不是执行建议。执行任何人工处置前，应重新核对数据库要求的会话标识、登录时间、用户、主机、应用、事务及 SQL 上下文。引用的 KILL/cancel/terminate 命令仅为文本；Toolkit 不执行。终止会话可能触发回滚、额外 I/O、日志/Undo 压力和应用错误，不能保证立即恢复。

Disk-growth 汇总本次可见的数据文件、事务日志、WAL、Binlog、Redo/Archive/FRA、TEMP 等资源类别和保留线索。没有历史快照时增长率为“无法确定”；一次快照不能证明特定会话是增长原因，也不会给出 Disk-growth Kill 候选。

应急初报与最终报告分别标注核心证据和完整 Profile 证据。查询失败、截断和未采集是不同状态；未采集的补充证据不代表失败。多个串行 SQL 组成 `BEST_EFFORT` 快照，不是原子快照，采集期间会话可能消失或身份可能变化。报告时间窗区分采集结束与 HTML 写入完成。

可在 runtime settings 中配置慢 SQL 阈值：

```yaml
diagnostics:
  slow:
    threshold_seconds: 10
```

值必须为 1 到 86400 的整数，布尔值不接受；未设置时默认 10 秒。
