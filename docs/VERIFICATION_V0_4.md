# V0.4 SQL Server Execution Plan 验证

2026-10-13 在一次性隔离 Docker SQL Server 2022 Developer 容器验证，版本 `16.0.4295.3`；客户端为 Python 3.12.15、pyodbc 5.3.0、Microsoft ODBC Driver 18 for SQL Server。Windows 本机未作为实机连接环境。监控账号 `toolkit_monitor` 非 sysadmin，Toolkit 只用该账号执行 Registry 中已审查的只读查询。管理员与工作负载账号仅用于用户授权的全新空测试容器初始化，密码仅经 stdin/内存传递。

运行 `pwsh -File tests/integration/run_docker_verification.ps1 -Plan` 成功。测试表 1000 行；五个查询都通过 `dba diagnose isolated-sqlserver plan --session <id>` 获取缓存计划，并生成脱敏 XML 和 Markdown：Clustered Index Scan、Index Seek、Nested Loops、Hash Match、Sort 均在真实 Showplan XML 中出现。Key Lookup 在本次小数据/优化器选择下未稳定出现，解析器和分析器的逻辑算子识别由单元测试覆盖；Estimated/Actual row mismatch、Missing Index、各类 Warning 也以受控合成 Showplan XML 测试，不宣称真实容器复现。实际缓存计划缺少 ActualRows，因此没有生成基数偏差、高代价 Nested Loops 或真实 Spill 的结论。

本轮新增 `plan_request`、`plan_cached` 两个只读受审查 SQL，按绑定 session ID 查询，限制 XML 大小并标记超限；缓存降级只关联仍在线会话的最近 SQL handle，目标断开或计划不存在时明确报告不可用。XML 使用解析器提取节点和证据、处理未知节点/缺失字段，保存前对可识别凭据标记脱敏。报告中的 Missing Index 只是证据，不生成变更命令。SQL 采集、连接、预检沿用 V0.3 安全边界。

集成输出 `.tmp/docker-plan-verification.json` 是不含密码的结果摘要。验证显示 `remaining_toolkit_sessions=0`、`test_objects_removed=true`，一次性容器及网络已停止并移除。离线/生产安装流程未改变。

SQL Server 2008 R2+ 是设计目标，目前只验证 2022。计划 SQL 使用老版已有的 DMV/DMF，而旧版实际列、Showplan XML 形态、监控权限与 Driver 18 TLS 兼容仍需真实旧实例验收。`sys.dm_exec_query_plan` 提供缓存的编译计划，通常没有运行时 ActualRows；不能从它推断真实行数。[Microsoft 文档](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-exec-query-plan-transact-sql)、[查询统计 DMV 文档](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-query-stats-transact-sql)。
