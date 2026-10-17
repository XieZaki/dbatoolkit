# V0.3 SQL Server Slow Diagnose 验证记录

日期：2026-10-13（Asia/Shanghai）。本轮目标是首轮只读慢诊断，未进入执行计划分析。

## 环境及隔离边界

在用户已授权的全新 Docker 测试容器上实测 SQL Server 2022 RTM-CU27，16.0.4295.3，Developer Edition。Linux 客户端使用 Python 3.12.15、pyodbc 5.3.0、ODBC Driver 18.7.1.1。监控账号 toolkit_monitor 的 is_sysadmin=0；Toolkit 命令仅使用交互式内存凭据和受审查只读 Registry SQL。

仅 tests/integration 的显式 `-Slow` 模式用管理员初始化空容器，用独立 toolkit_workload 测试账号操作 dbo.ToolkitSlowFixture。测试工作线程执行 UPDATE 构造真实锁阻塞，最终 ROLLBACK；不属于 Toolkit 功能。测试后确认测试事务数=0、Toolkit 残留会话数=0、测试表数值恢复为 0、测试对象已删除，测试容器和隔离网络已删除。未连接生产实例、未扫描 Inventory、未执行 KILL。

脱敏汇总位于 `.tmp/docker-slow-verification.json`。该文件仅保留测试摘要、根会话证据和采集状态，不含临时密码；包含测试 SQL 内容，按内部运维报告管理。

## 五个真实场景

| 场景 | 结果 | 主要证据 |
| --- | --- | --- |
| 当前活跃 SQL | 通过 | 安全 WAITFOR 批次在 dm_exec_requests 可见；其状态是 suspended，因此 waiting=1、running=0；不冒充 CPU 运行中的 SQL。 |
| 长请求 | 通过 | long_running_seconds=1，真实请求超过阈值，产生 LONG_RUNNING_SQL。 |
| 普通阻塞 | 通过 | 测试事务在 id=1 上保持锁，另一会话等待；生成 BLOCKING、ROOT_BLOCKER，根会话仍有当前请求。 |
| Sleeping + 未提交事务 | 通过 | 根会话无活动请求、状态 sleeping、open_transaction_count>0、事务 DMV 状态 active 和真实事务年龄、Last SQL 为测试 UPDATE，另有被阻塞请求；生成 IDLE_TRANSACTION_BLOCKER。 |
| 多层阻塞 | 通过 | A 阻塞 B，B 阻塞 C；实际图包含两条连续边，根会话受影响会话数=2。 |

测试中根会话和链的 session_id 是动态值，不能当成生产示例。实际结果使用过 83 → 84 → 70；报告中保存当前采样值。每个场景的四类采集（Active Requests、Active Sessions、Transactions、Connections）均成功，未截断；Markdown 十个部分生成成功，测试密码不在捕获终端输出或报告中。

## 逻辑与安全检查

- Blocking Tree 使用正数 blocking_session_id 构图并按入度求根；多层、多个根、循环、重复父节点、特殊负数所有者及缺失根会话由单元测试覆盖。循环不会进入递归死循环；特殊 -2/-3/-4/-5 显示原因，不生成 KILL 引用。
- 只有根会话真实存在且 Active Requests 采集未截断时才生成 `KILL <session_id>;` ReferenceCommand 文本。ReferenceCommand 无 execute 方法；CLI 无 `--execute`、`--force` 或 kill 执行命令。报告明确写明 Toolkit 未执行任何 KILL 操作。
- Idle Transaction 必须同时有 sleeping、无当前活动请求、open_transaction_count>0、事务 DMV 关联的未结束事务和实际正数阻塞边；只凭会话事务计数不会生成该结论。采集截断时不作 Idle Transaction 判定。
- 序列 DMV 采样不是原子快照。Root SQL/Last SQL 仅作上下文，不证明哪条语句最初获取锁。事务和来源采集失败可生成标明 incomplete 的部分报告并以退出码 1 返回，失败连接关闭。NULL 客户端 IP、最近 SQL、旧版缺失字段显示 unavailable，不伪造值。
- 借助已有版本及会话列能力检测路径，针对 SQL Server 2008 R2+ 保持兼容目标；本轮新 SQL 仅使用该版本已有的 sys.dm_tran_session_transactions、sys.dm_tran_active_transactions、sys.dm_exec_connections 和 sys.dm_exec_sql_text。2008 R2 仍无真实实例验收，驱动 TLS 互通亦未验证。

质量检查：pytest **158 passed**；Ruff、strict mypy、`dba validate`、CLI 启动与 Docker 五场景通过；离线 editable 安装后 pip 元数据和 dba 入口均为 0.3.0。`dba validate` 检查 10 份正式只读 SQL。Windows 运维机仍需按已批准的离线流程安装 pyodbc/ODBC Driver 18，不能以 Linux 容器实测代替 Windows 实机验收。

DMV 语义依据：[dm_exec_requests 的特殊 blocker 值](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-requests-transact-sql)、[事务和会话的关联](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-tran-session-transactions-transact-sql)、[事务状态](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-tran-active-transactions-transact-sql)、[最近 SQL handle](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-connections-transact-sql)。


