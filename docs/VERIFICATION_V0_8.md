# V0.8 Disk Growth 验证记录（2026-10-15）

## 功能范围

新增 `dba diagnose <database_id> disk-growth`，按 Inventory 类型分派到四库 Analyzer。所有数据库 SQL 独立存放于 `sql/<database_type>/disk_*.sql`，经过 Registry 元数据和静态安全校验。报告包括空间分类与来源、日志/复制/归档证据、事务、Finding、Recommendation、来源质量及下一步 Analyzer。

当前只比较单次采样，不推断增长速率；报告固定说明 `Current-state diagnosis only; growth rate unavailable without historical snapshot.`。PostgreSQL 文件系统无法从普通数据库协议可靠读取，明确标记 `pg_wal filesystem size unavailable from database protocol`。临时累计计数、未知值、权限失败及截断均保留其证据语义。

## 现有 Docker 环境实测

复用容器 `sqlserver2022`、`pg16`、`mysql84` 和 `oraclefree`，没有删除或重建数据库容器和已有数据集。通过实际 Toolkit CLI、真实驱动和临时专用监控账号分别运行 `test-connection` 与 `diagnose disk-growth`。凭据随机生成，通过运行时 stdin 传递；未进入项目 Inventory、源码、CLI 输出或报告。测试账号和 Client 容器由验证脚本清理。

| 数据库 | 实际版本 | CLI 结果 | 采集证据 |
| --- | --- | --- | --- |
| SQL Server | 2022，16.0.4305.5 | 成功，预检通过 | data/log 文件 2 行、log usage 1 行、TempDB 文件 2 行、TempDB usage 1 行、事务 0 行、最近 log backup 1 行；无截断、报告完整 |
| PostgreSQL | 16.15 | 成功，预检通过 | database 1 行、slots 0 行、archiver 1 行、replicas 0 行、事务 0 行；无截断、报告完整 |
| MySQL | 8.4.11 | 成功，预检通过 | schema 0 行、binlog 配置 1 行、replica 0 行、事务 0 行、binlog 清单 3 行；总大小准确聚合、无截断、报告完整 |
| Oracle | 23.0.0.0.0 | 成功，预检通过 | tablespace 5 行、datafile 4 行、TEMP 1 行、FRA 当前配置 0 行、FRA 分类 0 行、archive destination 1 行、log mode 1 行、事务 0 行；报告将 FRA 标记为 Not configured |

每个报告生成十个章节；样本没有支持异常 Finding，因此未伪造异常或建议。PostgreSQL 测试实例无 slot/replica，MySQL 无业务 Schema/Replica；Oracle 未配置 FRA。SQL Server 和 Oracle 连接仅使用专用只读监控账号，PG 使用 `pg_monitor` 监控角色，MySQL 使用只读 `REPLICATION CLIENT`、PROCESS、SHOW DATABASES 和 Performance Schema SELECT。

后续增加的 SQL Server Availability Group、MySQL InnoDB redo/undo 配置、Oracle 近 24 小时 archive volume/redo switch 采集，在同一现有容器中以原生客户端逐条只读执行并通过；这些追加脚本未能重跑监控账号 CLI 集成验证。

## 安全与限制

- Toolkit 查询只来自已审查 Registry 脚本；唯一 MySQL SELECT 以外的条目为精确固定的 `SHOW BINARY LOGS`，只读列出文件名及字节数，所需 `REPLICATION CLIENT` 不包含 SUPER 或 BINLOG_ADMIN。
- 不执行 Shrink、Purge、Reset、清理、扩容、DDL、DML、KILL 或复制变更。所有处置文字仅为 Recommendation。
- 所有四库采集 Adapter 失败时都会关闭连接。失败/截断时报告标记不完整；MySQL Binlog 清单达到上限时不聚合部分总量。
- 单次快照没有增长历史。MySQL Binlog 告警阈值是当前总量 10 GiB；Tablespace/文件使用率告警阈值为 85%，不声称这是通用容量 SLA。
- 再次运行完整监控账号 Docker 集成脚本时，自动审批拒绝了创建 V0.8 临时监控账号及数据库初始化步骤。已完成的四次独立真实 CLI 验证不受此拒绝影响；追加 SQL 改用只读原生查询验证兼容性。

## 检查

- `pytest -q`：249 passed。
- `ruff check .`：通过。
- `mypy --strict dba`：38 个源文件通过。
- `python -m dba validate`：0 个实际 Inventory 资产、68 条正式 SQL 通过。
- `python -m dba --version`：`dba-toolkit 0.8.0`。
- 真实 CLI JSON 和 Markdown 检查通过凭据字符串扫描；临时测试用户/容器已清除。
- 本轮启动的三个原有数据库容器已恢复为停止状态；原先运行的 `oraclefree` 仍保持健康运行。

旧版本兼容性仍遵循能力实际可见与来源质量报告原则。实测版本是 SQL Server 2022、PostgreSQL 16.15、MySQL 8.4.11、Oracle Free 23；其他版本没有通过本轮 Docker 实测。
