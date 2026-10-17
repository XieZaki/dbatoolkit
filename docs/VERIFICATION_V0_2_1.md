# V0.2.1 验证记录

验证日期：2026-10-12 至 2026-10-13（Asia/Shanghai）。最终隔离实测报告：`.tmp/docker-verification.json`；此前冷启动超时记录保留在 `.tmp/docker-verification-cold-timeout.json`。

## 实际环境与安全边界

用户明确批准仅在全新隔离空容器中进行管理员初始化。唯一目标为 isolated-sqlserver：SQL Server 2022 RTM-CU27（KB5104824），16.0.4295.3，Developer Edition，Ubuntu 22.04.5。没有生产连接或 Inventory 扫描。

Docker 引擎恢复后为 29.8.2，重新拉取官方 SQL Server 镜像成功，digest 为 sha256:4402d880dd4c34bfa7d8705e56a86cd6c88da80a1f6bbbe741f999e76264a090。独立 Linux 客户端源码只读挂载，Python 3.12.15、pyodbc 5.3.0、Microsoft ODBC Driver 18.7.1.1（驱动返回 18.07.0001）。使用加密 TCP，TrustServerCertificate=true 仅用于空容器自签证书，不代表生产证书验证通过。

初始化脚本在空容器创建 toolkit_monitor/master 用户、DENY EXECUTE，并授予 VIEW SERVER PERFORMANCE STATE、VIEW ANY DATABASE、VIEW ANY DEFINITION。管理员仅用于用户批准的初始化例外；Toolkit 始终使用专用监控账号，不执行权限变更。实际 CLI 使用 PromptCredentialProvider，经 PTY 隐藏输入获取内存凭据；密码没有写入参数、Inventory 或文件。

Windows 本机为 Python 3.12.14 x64，未安装 pyodbc 和 ODBC Driver 18，没有自动安装。Linux 验证不替代 Windows 实机验收。

## 最终真实结果

以下采集耗时为 Collector 本身；总耗时包含启动、连接、预检。所有命令退出码为 0，所有常规采集 truncated=false。

| 命令 | 成功 | 行数 | 采集耗时 ms | 总耗时 ms |
| --- | --- | --- | --- | --- |
| test-connection | 是，首次成功 | 不适用 | 不适用 | 944.91 |
| server-info | 是 | 1 | 17.28 | 681.13 |
| database-status | 是 | 4 | 16.98 | 685.97 |
| active-sessions | 是 | 1 | 17.18 | 681.98 |
| active-requests | 是 | 0 | 19.37 | 690.88 |
| long-running | 是 | 0 | 37.17 | 685.93 |

实际 cursor.description 确认 Sessions 和 Requests 全部必需列存在。Sessions 的真实观察会话包含客户端 IP、数据库、时间及事务数；缺失原因辅助列为 NULL 表示该行字段可得。Server Info 的 InstanceName 为 NULL，因为使用默认实例。空实例没有其他用户正在执行的请求，因此 Requests/Long Running 为 0 行；不伪造 SQL text 或正数长请求。真实业务 SQL 文本与正数长请求未覆盖，阈值筛选由 Fake 测试覆盖。

Monitor Preflight 全部通过；is_sysadmin=0、is_db_owner=0、VIEW SERVER PERFORMANCE STATE=1、VIEW ANY DATABASE=1、VIEW SERVER STATE=0（2022 使用前者）。非允许权限列表为空；本次采集没有缺失权限，无自动 GRANT。

## 查询、清理及日志

- 复制受控 Registry 到客户端临时目录，只将 database-status 元数据 max_rows 改为 1。实际 4 个数据库返回 1 行，success=true、truncated=true；未改变正式限额。
- 实际驱动 query timeout 为 10 秒，autocommit=true。10-12 冷启动普通只读权限查询曾约 10.59 秒触发 QueryTimeout；最终轮首次连接成功，未触发测试脚本的单次显式重试。Toolkit 没有自动重试。
- 正常 CLI 完成后、实际连接后的 Python KeyboardInterrupt、受审查无效列 SELECT 查询失败后，服务器上 dba-toolkit 会话数均为 0。
- 真实失败返回受控 QueryFailed/固定脱敏消息，不透传驱动原始异常。
- 实际临时密码不在捕获终端输出或汇总报告中。审计只含脚本名、摘要、限额、行数、耗时及结果，无 SQL 正文或完整带密码连接字符串；人工复核安全字段输出。
- 未运行 WAITFOR 或高负载 SQL。原生 ODBC 阻塞期间 Ctrl+C 的及时性没有真实验证；Python 中断清理真实通过，CLI 中断和超时清理另有 Fake 回归覆盖。
- 最终流程停止并删除仅本次创建的两个容器及隔离网络，保留镜像缓存和脱敏报告。没有修改 Windows 驱动、生产数据或数据库参数。

## 发现及必要修复

- monitor_permissions UNION 曾出现 SQL Server 468 排序规则冲突：权限名称统一 COLLATE DATABASE_DEFAULT，不修改数据库排序规则。
- server_info 的 current_time 别名曾出现 156 语法错误：改为 [current_time]，输出字段保持不变。
- 两项 VIEW ANY COLUMN ENCRYPTION KEY DEFINITION / VIEW ANY COLUMN MASTER KEY DEFINITION 纳入只读元数据权限。
- public 对象 EXECUTE GRANT 被数据库 DENY 覆盖时曾误报：仅在 HAS_PERMS_BY_NAME 明确为 0 时排除；有效 EXECUTE、转授权、NULL/不确定结果仍拒绝。
- 对新版 Sessions database_id/open_transaction_count 增加 ProductVersion/实际列能力检测及受审查 legacy 查询，旧版读取 sys.sysprocesses.dbid/open_tran 并注明来源。2008 R2 路径静态检查及 Fake 测试通过，但没有旧版真实实例，TLS/驱动互通尚未实测。
- 增加安全 driver-info、long-running 别名、显式隔离验证脚本和清理保护。初始化此前有超时失败，原因未确认；最终本轮首次初始化成功，不宣称已确定并修复历史根因。

## 质量检查与结论

pytest 121 passed；Ruff 通过；mypy 严格检查通过（20 个源码文件）；dba validate 通过（4 个禁用示例、8 个受审查 SQL）；安装入口 dba --version=0.2.1、CLI help 启动正常；PowerShell 验证脚本语法检查通过。普通测试不依赖真实数据库。

SQL Server 2022/Linux 的 V0.2.1 基础连接与采集验证通过，具备后续 V0.3 开发基础。Windows 目标机依赖/真实连接、旧版实例及原生阻塞 Ctrl+C 仍须环境验收，不能宣称所有目标平台兼容。未新增 Analyzer、Slow Diagnose、Blocking Tree 或 Web UI，本轮完成后停止。
