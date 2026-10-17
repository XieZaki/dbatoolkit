# dba-toolkit 0.8.3.5

企业内部运维机器上的跨数据库只读诊断基础框架。**工具负责查清楚，DBA 负责做决定。**
V0.7 保留 SQL Server、PostgreSQL 和 MySQL 诊断，并新增 Oracle 12c+ 连接、基础采集和 Slow Diagnose。运行不依赖 Internet。
V0.7.1 已在隔离 Oracle Free 23 容器使用 python-oracledb Thin 及专用监控账号完成真实 CLI 端到端验证；结果见 [验证记录](docs/VERIFICATION_V0_7_1.md)。
V0.8 增加四库 `diagnose <database_id> disk-growth` 当前空间与日志保留诊断，Docker 实测结果见 [V0.8 验证记录](docs/VERIFICATION_V0_8.md)。
V0.8.1 增加四库统一 Monitor Permission Registry、权限状态与只生成不执行的 Grant/Revoke 建议，详见 [监控权限说明](docs/MONITOR_PERMISSIONS.md)。
V0.8.2 增加 Windows/Linux 可重复离线部署、六库 Runtime Manifest、SQL Server ODBC 18→17 回退、`doctor` 和 `runtime-info`。部署和升级流程见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。
V0.8.2.1 增加实例级非敏感 Connection Profile 解析及 OS Credential Store 命令，凭据缺失时回退为仅本次运行的交互输入。
V0.8.3 增加 `dba check` / `dba status`、基于 Inventory 的 Runtime Doctor、人类可读默认输出和统一 JSON Presenter；结构化运行日志改为有界文件输出。
V0.8.3.2 将详细诊断报告统一为自包含离线 HTML；终端保持事故摘要，`--json` 保持机器可读，运行日志仍为 JSONL。阻塞处置命令仅在证据完整时作为人工审核文本显示，Toolkit 永不执行。
V0.8.3.3 将 Human HTML 收敛为有预算的 DBA 分析报告：恢复动作前置、事故相关会话与请求分表、显式报告省略数量；计划 XML 单独保存为受 `.gitignore` 保护的 `.sqlplan`。新增 Windows/Linux upgrade 流程和只扫描 Git-tracked 文件的敏感数据检查。本版本不会自动 push 或创建 release tag。
V0.8.3.5 在 V0.8.3.4 的加密 Credential Store 上增加当前 OS 用户短期 Credential Session。使用 `dba credentials unlock` 后，在默认 30 分钟绝对 TTL 内可复用解密凭据；`status` 查看、`lock` 立即清除 Agent 内存。Windows 使用当前 SID ACL 的 Named Pipe，Linux 使用有 peer UID 验证的私有 Unix Socket。秘密不写磁盘、环境变量或日志。安全设计与限制见 [Credential Security](docs/CREDENTIAL_SECURITY.md)。

Python `>=3.11,<3.14`，当前主要验证版本为 3.12。从项目根目录执行：

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/dba list
.venv/Scripts/dba check <database_id>
.venv/Scripts/dba status <database_id>
.venv/Scripts/dba diagnose <database_id> slow
.venv/Scripts/dba diagnose <database_id> disk-growth
```

默认输出面向 DBA 阅读。自动化需要结构化结果时，在命令后添加 `--json`。高级部署与排查命令见 `dba -h`，包括 `show`、`validate`、`doctor`、`runtime-info`、`credentials`、`test-connection`、`permissions` 和 `collect`。

`dba check <database_id>` 分别显示 Account Security 与各 Feature 的 Permission Readiness。权限不足不等于账号过权：例如 MySQL 只有全局只读 `SELECT` 时可为 `NON_PRIVILEGED`，同时 `slow` 因缺少 `PROCESS` 而为 `MISSING`。使用 `dba permissions <database_id> --feature slow` 查看 Registry 需求；诊断缺少 required grant 时返回具体原因和仅供人工审核的参考 GRANT，Toolkit 永不执行授权。

`diagnose` 默认提供事故处置摘要；Human HTML 是有行数和体积预算的 DBA 分析报告，不是原始采集数据仓库。`--json` 提供机器可读诊断数据，运行日志使用 JSONL。报告名为 `reports/<database_id>_<scenario>_<本地时间>.html`，同一秒冲突时追加 `_02` 等后缀。明确诊断 SQL Server 执行计划且 XML 可用时，会在同目录生成配对 `.sqlplan`；HTML 不嵌入原始 XML。Reference Kill/Cancel/Terminate 命令只有在阻塞根会话、受影响会话和采样完整性均有证据时才会显示；命令始终要求 DBA 人工审核，Toolkit 不执行。

SQL Server 真实连接安装 `python -m pip install -e ".[sqlserver]"`，并通过企业批准渠道安装 Microsoft ODBC Driver 18（优先）或 Driver 17（兼容回退）。PostgreSQL、MySQL、Oracle、MongoDB、Redis 分别使用 `postgresql`、`mysql`、`oracle`、`mongodb`、`redis` extra；`all-db` 安装六库 Python 驱动。Oracle 固定默认 Thin Mode，无需 Instant Client。完整离线流程见 [部署文档](docs/DEPLOYMENT.md)。

多运维机首次部署、升级、回滚和 Git 敏感数据检查见 [Repository & Release 流程](docs/REPOSITORY.md)。Windows/Linux 升级脚本只使用本地 wheelhouse，不拉取代码、不改本地 Inventory 或 Credential Store。

将 Inventory 中的目标实例地址、端口、database、credential_alias 配置正确，并明确设置 `enabled: true`。各库禁用示例位于 `examples/inventory/`，不会进入默认生产 Inventory。真实 Inventory 仅放在本机 `config/inventory/` 并由 Git 忽略。密码不能写入 Inventory。直接运行 `check`、`status`、`test-connection`、`collect` 或 `diagnose`；当别名在可用凭据库中确实不存在时，Toolkit 会隐藏输入数据库账号密码，并询问是否安全保存。高级用户可预配置：首选 Windows Credential Manager / Linux 安全 Keyring；无桌面或 OS Store 不可用时，先显式运行 `dba credentials init --backend encrypted` 创建 AES-256-GCM + Argon2id 用户级加密 Store。`dba credentials set` 仍可用于批量预配置和轮换。Toolkit 没有密码命令行参数。详见 [Credential Security](docs/CREDENTIAL_SECURITY.md)。

## Inventory 文件组织

默认读取 `config/inventory/` 下的全部递归 YAML；当该目录不存在或没有 YAML 时，回退旧版 `config/inventory.yaml`。二者不会合并。`--inventory` 也可显式指定其他目录或旧单文件。真实资产应放入本地 `config/inventory/<数据库类型>/<环境>.yaml`；可从 `examples/inventory/sqlserver.example.yaml` 等禁用示例复制后编辑。

每个文件可有独立的 `defaults`，仅合并到该文件的 `databases` 条目；条目显式字段优先，不跨文件继承。`id` 必须逐条提供。`group` 是可选字符串，`tags` 是可选字符串列表；标签去首尾空白并去重。`list --type/--env/--group/--tag` 可组合，条件之间按 AND 过滤。跨文件重复 ID 会同时报告两个来源文件；来源路径只用于校验和调试，不进入默认 CLI 资产输出。仍然只存 `credential_alias`，禁止密码和令牌。

```powershell
.venv/Scripts/dba --settings config/runtime.yaml test-connection my-sqlserver
.venv/Scripts/dba --settings config/runtime.yaml permissions my-sqlserver
.venv/Scripts/dba --settings config/runtime.yaml permissions my-sqlserver --feature slow
.venv/Scripts/dba --settings config/runtime.yaml permissions my-sqlserver --feature slow --generate-grant
.venv/Scripts/dba --settings config/runtime.yaml permissions my-sqlserver --all-features --generate-revoke
.venv/Scripts/dba --settings config/runtime.yaml collect my-sqlserver server-info
.venv/Scripts/dba --settings config/runtime.yaml collect my-sqlserver database-status
.venv/Scripts/dba --settings config/runtime.yaml collect my-sqlserver active-sessions
.venv/Scripts/dba --settings config/runtime.yaml collect my-sqlserver active-requests
.venv/Scripts/dba --settings config/runtime.yaml collect my-sqlserver long-running-requests
```

V0.2.1 实机验证前先运行 `dba driver-info`，显示 Python 架构、pyodbc 版本和 ODBC Driver 名称，不读取 Inventory、不获取凭据、不连接数据库。缺少依赖时返回 1，且仅提示企业离线安装需求。`collect <id> long-running` 是 `long-running-requests` 的兼容别名。只对人工指定的一个 database_id 验证，不扫描 Inventory。

每条命令按 Inventory 的 `credential_alias` 从安全存储读取凭据，必要时对当前运行隐藏交互输入，然后连接、完成预检、采集并关闭连接。当前支持 SQL Server、PostgreSQL、MySQL 和 Oracle；其他数据库返回 `Not implemented`。默认输出 DBA 可读摘要；在命令后添加 `--json` 获取脱敏结构化结果。SQL 执行审计写入 `logs/dba.jsonl`，不混入正常终端输出，也不包含 SQL 文本或原始驱动异常。
`permissions` 汇总四库已实现 Feature 的权限状态。生成 Grant/Revoke 时只读取 Inventory 与 Credential Provider，不连接数据库；SQL 仅打印为 DBA 人工审核文本，Toolkit 不执行。权限矩阵、Feature 状态语义及版本限制见 [docs/MONITOR_PERMISSIONS.md](docs/MONITOR_PERMISSIONS.md)。
SQL 执行审计包含脚本名称、SHA-256 摘要、有效超时、行数上限、返回行数、截断标志、结果和错误类别。Ctrl+C 中断也关闭已取得的连接；CLI 返回 130。
`--settings` 可省略，此时使用内置安全默认值；指定后严格读取该文件，拼写错误或密码等未知字段会被拒绝。

SQL Server 连接失败会报告实际选用的 ODBC Driver，以及生效的 `Encrypt` / `TrustServerCertificate`（均不含凭据）。Toolkit 不会在失败后静默降低 TLS。隔离测试实例若经批准必须使用 `Encrypt=no`，应在该实例的 Inventory `connection` 中显式设置，不要修改共享生产配置。

连接参数现在可逐实例放在 Inventory 的 `connection` 中；优先级为实例配置、数据库类型 runtime defaults、代码安全默认值。`dba show` 与 `test-connection` 会显示最终非敏感配置。SQL Server Docker 测试条目已有显式 `Encrypt=no` 和 `TrustServerCertificate=yes`，可以直接测试。账号凭据优先保存在 Windows Credential Manager / Linux 安全 Keyring；无桌面服务器可显式初始化 AES-GCM + Argon2id encrypted store，或仅对当前命令隐藏交互输入。细节见 [Credential Security](docs/CREDENTIAL_SECURITY.md)。

`config/runtime.yaml` 分别包含 `sqlserver`、`postgresql`、`mysql` 与 `oracle` 配置。SQL Server 默认 connect_timeout=5、query_timeout=10、encrypt=true、trust_server_certificate=false；PostgreSQL 默认 connect_timeout=5、query_timeout=10、sslmode=verify-full；MySQL 默认 connect_timeout=5、query_timeout=10、utf8mb4、启用 SSL 并校验证书与主机名。TLS 参数允许显式覆盖，生产推荐验证证书。PostgreSQL 连接启动时设置只读事务和 statement_timeout，取运行上限与受审查 SQL metadata 中最小超时；不会发送改变会话状态的初始化 SQL。

CollectorResult 包含 database_id、collector_name、started_at、finished_at、elapsed_ms、rows、row_count、truncated、success、error_type、error_message。时间使用 UTC，行内时间保留服务器值。达到 max_rows 时即标记 truncated=true，包括恰好等于上限的情况；长请求筛选保留原采集的截断标志。活动请求按耗时降序采集，SQL 文本为批次文本，最多 16384 字符，超限时另有 sql_text_truncated 标志。活动会话包括 sleeping 会话并排除工具自身；客户端 IP 在不可获取时为空。

## 监控账号预检及安全边界

预检验证实际读取基础实例、数据库状态、会话和请求的能力，并检查跨会话可见性。旧版本至 SQL Server 2019 使用 VIEW SERVER STATE；2022+ 使用 VIEW SERVER PERFORMANCE STATE，数据库列表要求 VIEW ANY DATABASE。失败消息指出缺少的能力，只提供人工审核建议，绝不执行 GRANT。

SQL 兼容目标扩展至 2008 R2（10.50）及以上，但尚未完成该版本实机验证。预检读取 ProductVersion 并检查会话 DMV 的实际列能力：现代实现使用 sys.dm_exec_sessions.database_id/open_transaction_count；缺列时改用受审查的 active_sessions_legacy。旧版从 sys.sysprocesses.dbid/open_tran 获取真实值并带来源列，其进程统计语义不等同于新版会话统计；不伪造 0 或数据库名称。元数据因会话变化或权限不可见时返回数据库原始 NULL，并带可能原因说明。列能力信息缺失时拒绝继续。旧服务器与 Driver 18 的 TLS、补丁和证书互通仍须在目标环境验证，不能从 SQL 静态兼容推断连接兼容。

同时拒绝 sa 等账号、sysadmin/db_owner 以及服务器和当前数据库（含可见用户对象/Schema）上的非监控有效权限；对象权限枚举也受查询超时保护。权限信息不完整、达到行数上限或探测失败时拒绝继续。该检查不能证明账号在其他数据库没有写权限；仍须 DBA 保证整个实例使用专用最小权限监控账号。数据库状态列表受元数据可见性限制，监控账号可能看不到离线数据库，不为此要求更高权限。
预检还保守检查当前登录/用户令牌的显式授权（包含角色），拒绝非监控权限及可转授权权限。对象级 EXECUTE GRANT 若明确被 DENY 覆盖，则按实际权限消除误报；无法确认时仍拒绝，转授权权限也仍拒绝。两项列加密密钥定义查看权限属于只读元数据权限。生产账号的权限配置由 DBA 人工完成。

V0.2.1 验证记录见 [docs/VERIFICATION_V0_2_1.md](docs/VERIFICATION_V0_2_1.md)。用户明确授权的空 Docker 测试环境流程见 [docs/DOCKER_VERIFICATION_PLAN.md](docs/DOCKER_VERIFICATION_PLAN.md)，显式运行 `tests/integration/run_docker_verification.ps1`；普通 pytest 不连接数据库。初始化例外仅用于新建空测试容器，Toolkit 始终使用专用监控账号和受审查只读 SQL。

连接使用 autocommit，不发送修改会话状态的初始化 SQL。执行结果只使用 fetchmany(max_rows)，不用 fetchall；每条查询关闭游标。异常关闭连接，若驱动处于事务模式则先尝试 rollback；关闭失败会报告安全错误，需人工核查会话。
Adapter 实例只供串行使用。无任意 SQL CLI、无 KILL/变更执行。SQL 文本可能含业务数据，报告仍按内部敏感数据管理；工具屏蔽连接密码及可识别的凭据字段，不宣称识别任意无标记业务秘密。

## SQL Server Slow Diagnose

```powershell
.venv/Scripts/dba --settings config/runtime.yaml diagnose my-sqlserver slow
.venv/Scripts/dba --settings config/runtime.yaml diagnose my-sqlserver slow --report reports/incident.html
.venv/Scripts/dba --settings config/runtime.yaml diagnose my-sqlserver disk-growth
```

默认输出 DBA 可读 Console，并生成带数据库 ID、场景和本地时间戳的 HTML；`--report` 指定新的 HTML 文件，已存在文件不会覆盖。自动化读取使用 `--json`，旧版 `--format json` 仍兼容。其他数据库明确返回 Not implemented。

报告包含并发分类、按 elapsed_ms 排序的当前请求 Top N、长请求、当前等待、多层 Blocking Tree、Root Blocker 来源和事务信息、Current/Last SQL、Findings、建议及仅供人工审核的 ReferenceCommand。默认 slow_top_n=10、long_running_seconds=60、high_concurrency_threshold=50，可通过运行配置覆盖。HIGH_CONCURRENCY 按 active requests 判断，connected sessions 另列；running 表示状态 running，等待和阻塞统计允许重叠。

复用 Active Requests/Sessions；增加 transactions 和 connections 两份受审查只读 SQL。事务年龄在服务器端计算，避免客户端时区差异。最近 SQL 使用 most_recent_sql_handle；NULL 明确显示 unavailable，最近批次不证明该 SQL 获取了当前锁。Idle Transaction 必须有 sleeping 根会话、无活动请求、open_transaction_count>0 和 DMV 关联的未结束事务（state 2/4/5，type 1/2/4）才报告。没有深度 CPU、IO 或执行计划根因推断。

DMV 顺序采集不是原子快照；所有采集窗口、失败和截断记录在 JSON details.samples 中。截断时计数是观察下限，根节点可能不完整。非关键事务/连接采集失败保留已采集结果并标记 incomplete；错误连接仍关闭，不继续使用，命令返回 1。主采集失败则停止。多连接/MARS 保留最近 SQL 候选列表，不伪造唯一来源。环和负数等待所有者单独报告；Blocking Tree 用迭代遍历避免递归溢出。

只生成 `KILL <session_id>;` 文本；无执行、确认执行、--execute、--force 或 kill 子命令。生成参考语句前必须有实际根会话记录，DBA 手工执行前仍须重新核对会话身份和回滚影响。报告明确标注 Toolkit 未执行任何 KILL 操作。

V0.3 测试记录见 [docs/VERIFICATION_V0_3.md](docs/VERIFICATION_V0_3.md)。显式 Docker 集成运行 `tests/integration/run_docker_verification.ps1 -Slow`；仅在新建隔离空容器用独立测试账号创建事务场景，最终 ROLLBACK、清理测试对象和容器，不属于 Toolkit 执行能力。

## PostgreSQL Slow Diagnose

```powershell
.venv/Scripts/dba --settings config/runtime.yaml test-connection my-postgresql
.venv/Scripts/dba --settings config/runtime.yaml collect my-postgresql server-info
.venv/Scripts/dba --settings config/runtime.yaml collect my-postgresql database-status
.venv/Scripts/dba --settings config/runtime.yaml collect my-postgresql active-sessions
.venv/Scripts/dba --settings config/runtime.yaml collect my-postgresql long-running
.venv/Scripts/dba --settings config/runtime.yaml diagnose my-postgresql slow
```

PostgreSQL 诊断连接使用 psycopg 3、专用监控账号、autocommit、只读事务及服务端 statement_timeout。预检拒绝超级用户和具创建数据库/角色、复制或绕过行级安全属性的账号，要求有效的跨会话统计与设置读取能力；可由 DBA 人工审核 `pg_monitor` 内置角色，Toolkit 不授予权限。`pg_stat_activity`、`pg_locks`、`pg_stat_database`、复制/归档视图和必要设置通过已审查只读 SQL 探测。空复制视图不代表权限缺失。

Slow Diagnose 对 `pg_stat_activity` 客户端连接做有界采样并排除自身 PID；活动 SQL 时长由服务器 `clock_timestamp()` 计算。报告区分总连接、活动查询、等待、被阻塞和 idle in transaction，会列出当前活动 SQL Top N、达到阈值的长 SQL、多层阻塞图、Root Blocker 来源与事务证据。非 active 会话的 `query` 是最后执行的查询，不能当作当前查询；`client_hostname` 为空时明确标记不可得。采样不是原子快照，截断或失败会标记不完整。配置项包括 `long_running_seconds`、`slow_top_n`、`high_concurrency_threshold` 和 `idle_transaction_seconds`。

Root Blocker 存在且会话与阻塞采样完整时，报告可生成 `SELECT pg_cancel_backend(pid);`、`SELECT pg_terminate_backend(pid);` **纯文本参考**；cancel 只尝试取消当前查询，idle in transaction 可能没有当前查询。Toolkit 不执行这些函数、不自动处理事务，也不运行 EXPLAIN ANALYZE。PostgreSQL 12+ 是设计目标，旧版本、不同部署的 SSL/监控权限仍需按目标环境验收。隔离 Docker 集成命令：`pwsh -File tests/integration/run_postgresql_verification.ps1`。

## MySQL Slow Diagnose

```powershell
.venv/Scripts/dba --settings config/runtime.yaml test-connection my-mysql
.venv/Scripts/dba --settings config/runtime.yaml collect my-mysql server-info
.venv/Scripts/dba --settings config/runtime.yaml collect my-mysql database-status
.venv/Scripts/dba --settings config/runtime.yaml collect my-mysql active-sessions
.venv/Scripts/dba --settings config/runtime.yaml collect my-mysql active-requests
.venv/Scripts/dba --settings config/runtime.yaml collect my-mysql long-running
.venv/Scripts/dba --settings config/runtime.yaml diagnose my-mysql slow
```

MySQL 8.0 使用 PyMySQL、专用监控账号和只读 Registry SQL。默认校验证书及主机名；内网 CA 可通过 `mysql.ssl_ca` 指定，隔离测试容器可显式设置 `mysql.ssl_enabled: false`。连接和读取超时有界、autocommit=true、禁用 local infile；使用流式游标和 `max_rows`，达到上限会标记截断并关闭连接。PyMySQL `read_timeout` 限制等待网络响应的时间，不能保证整批流式结果的绝对墙钟时长。

监控账号预检要求 MySQL 8.0、启用 Performance Schema、无活动角色和非监控全局/Schema/表/列权限，并实际探测统计表、事务和锁视图。通常需 DBA 人工审核全局 `PROCESS`、`SHOW DATABASES`，以及必要对象的 `SELECT`；Toolkit 不执行 GRANT。预检无法穷尽所有动态权限、存储程序或其他隐含写能力，生产仍需 DBA 保证账号最小权限。某些视图只显示监控账号有权看到的对象，报告不会补造缺失值。

活动语句优先读取 `events_statements_current` 与当前等待；等待 consumer 关闭时使用受审查的无等待查询，语句 consumer 关闭时使用线程查询降级，并把缺失字段与不完整结论明确列出。长 SQL 时长在服务器端获取，Sleep + 未结束 InnoDB 事务必须有 `innodb_trx` 证据；阻塞图来自 `data_lock_waits`/`data_locks`。Root Blocker 的 `KILL QUERY <id>;` 与 `KILL <id>;` 仅为人工审核文本；Sleep 会话通常没有当前查询可供 KILL QUERY 取消。Toolkit 不执行 KILL，也不把当前进程信息当作可靠的“上一条 SQL”。MySQL 8.0.46 已通过隔离 Docker 验证；其他版本、TLS 与监控权限须按目标环境验收。集成验证：`pwsh -File tests/integration/run_mysql_verification.ps1`；记录见 [docs/VERIFICATION_V0_6.md](docs/VERIFICATION_V0_6.md)。

## SQL Server Execution Plan

```powershell
.venv/Scripts/dba --settings config/runtime.yaml diagnose my-sqlserver plan --session 172
.venv/Scripts/dba --settings config/runtime.yaml diagnose my-sqlserver plan --session 172 --format json
```

命令只对指定会话查找当前请求的执行计划；当前请求已结束时，仅尝试通过仍在线的会话及其最近 SQL handle 查找缓存计划。会话已断开、缓存已清除或权限不足时会明确报告不可用。计划为编译/缓存计划时通常没有 ActualRows，因此不推断实际行数、基数偏差、真实 Spill 或高代价 Nested Loops。采集不是原子快照，可能随会话和缓存变化。

Plan 诊断写入同一命名规则的单个 HTML 文件；可用时将脱敏的执行计划 XML 放在折叠区域中。执行计划仍可能包含业务 SQL、对象名及未标记的敏感字面量，须按内部敏感数据管理。SQL Registry 限制只读脚本；Toolkit 不执行任何索引、统计信息、计划或数据变更。Missing Index 仅作证据，不生成 DDL。

配置允许 `plan_cardinality_ratio_warning`、`plan_cardinality_ratio_critical`、`plan_top_operators`、`plan_max_xml_size` 和 `plan_large_scan_rows`。默认 XML 上限 1 MiB，超过上限显式标记不可用。Slow 报告仅给长请求显示下一步 plan 命令提示，不自动抓取所有计划。显式 Docker 验证命令是 `pwsh -File tests/integration/run_docker_verification.ps1 -Plan`；记录见 [docs/VERIFICATION_V0_4.md](docs/VERIFICATION_V0_4.md)。

SQL Server 2008 R2 compatibility is designed but not yet validated on a real instance. 当前仅在 SQL Server 2022 实测；旧版本的 DMV、Showplan XML、ODBC Driver 18 TLS 互通仍需目标环境验收。

Linux 使用 `.venv/bin/python` / `.venv/bin/dba`。也可 `python -m dba`。
默认配置相对于工作目录；从其他目录运行时显式指定：
`dba --inventory /path/inventory.yaml --sql-root /path/sql validate`。
`validate` 成功返回 0，配置/脚本失败返回 1，CLI 参数错误返回 2。

## 离线部署

在与目标 OS、Python 版本、架构匹配的已批准联网准备机生成六库 wheelhouse：

```text
python -m pip wheel ".[all-db]" --wheel-dir deployment/wheelhouse
```

通过企业批准渠道传输项目与 wheelhouse，在目标机器创建全新 venv：

```text
.\deployment\windows\bootstrap.ps1 -Offline
./deployment/linux/bootstrap.sh --offline
```

配置与 SQL 是外置运维资产，不打进 wheel；部署时必须同时复制。SQL Server ODBC 18/17 是仍需系统管理员安装的 native dependency。运行不访问 Internet，不复制旧 `.venv` 作为部署方式。

## 模块

- `inventory.py`：严格 YAML、重复字段/ID、类型、必填及未知字段检查；过滤和查询。
- `credentials.py`：凭据接口、交互式 Provider、仅内存 Mock、生产拒绝 Mock。
- `registry.py` / `security.py`：外置 SQL+同名 YAML 元数据、审查标志、只读风险、限额、保守危险词检查。
- `adapters.py` / `collectors.py`：统一 Adapter/CollectorResult 接口、独立 NativeTool。
- `sqlserver.py` / `preflight.py` / `sqlserver_collectors.py`：SQL Server 连接、权限预检、五类基础 Collector。
- `postgresql.py` / `postgresql_collectors.py` / `postgresql_slow.py`：PostgreSQL 只读连接、监控预检、基础采集及 Slow Diagnose。
- `mysql.py` / `mysql_collectors.py` / `mysql_slow.py`：MySQL 8.0 只读连接、监控预检、基础采集及 Slow Diagnose。
- `oracle.py` / `oracle_collectors.py` / `oracle_slow.py`：Oracle 12c+ Thin 只读连接、监控预检、基础采集及 Slow Diagnose。
- `analyzers.py`：按数据库/场景分发及十二类 Analyzer、数据库专属日志扩展点。
- `core.py` / `reporters.py`：Finding/Severity/Evidence/Recommendation/纯文本参考命令与控制台呈现；默认诊断详情由 `dba/reporting/` 统一生成 HTML。
- `logging.py`：结构化日志与递归敏感字段脱敏；不输出任意消息及异常堆栈。

`examples/inventory/` 使用 RFC 5737 文档示例地址并禁用。正式 SQL 均经过静态检查和源码审查；reviewed 不代表已在生产实测，部署前仍需 DBA 按目标 SQL Server 版本审查。
库存未知字段一律拒绝，密码字段包括嵌套结构无法进入有效配置。
静态检查故意保守，会拒绝注释/字符串中的危险词；它不能发现所有数据库函数的副作用，也不能代替监控账号授权。
正式执行必须是同一 Registry 签发的对象，执行前重新校验磁盘 SQL 和元数据；手工构造对象、其他 Registry 对象、变更过的文件和未审查脚本均拒绝。

## 开发检查

```text
python -m pytest -q
python -m ruff check .
python -m mypy dba
python -m dba --version
python -m dba validate
```

长期限制与数据库专属扩展需求见 [docs/PROJECT_RULES.md](docs/PROJECT_RULES.md)。

普通测试使用 Fake Driver，覆盖连接/查询超时、权限不足、清理失败、受控执行、截断、脱敏和 CLI；不会读取真实凭据或连接数据库。已在隔离 SQL Server 2022/Linux 环境完成真实验证；Windows 和旧版目标环境仍须单独验收，不能以 Mock 测试代替。

V0.2.1 连接基础实测见 [docs/VERIFICATION_V0_2_1.md](docs/VERIFICATION_V0_2_1.md)，V0.3 Slow Diagnose 实测见 [docs/VERIFICATION_V0_3.md](docs/VERIFICATION_V0_3.md)。只在人工指定的实例上验证；凭据通过安全存储或本机隐藏输入提供，不通过聊天、参数或日志传递。

实现依据：[pyodbc 连接及查询超时](https://github.com/mkleehammer/pyodbc/wiki/Connection)、[SQL Server 会话 DMV 权限](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-sessions-transact-sql)、[有效权限函数](https://learn.microsoft.com/en-us/sql/relational-databases/system-functions/sys-fn-my-permissions-transact-sql)、[ODBC TLS 参数](https://learn.microsoft.com/en-us/sql/connect/odbc/dsn-connection-string-attribute)。

## Oracle 12c+ Slow Diagnose (V0.7)

使用 python-oracledb Thin 模式；在离线准备机为目标系统/Python 制作 `".[oracle]"` wheelhouse，运维机执行 `python -m pip install --no-index --find-links wheelhouse "dba-toolkit[oracle]"`。Inventory 中仅配置 `service_name` 或 `sid` 其一，以及 `credential_alias`，不保存密码。默认 connect_timeout=5 秒、call_timeout=10 秒；每条 SQL 实际使用 metadata 与运行配置中较小的 call_timeout。该超时是驱动调用/网络往返限制，不保证整个诊断流程的绝对耗时上限。

```powershell
dba --settings config/runtime.yaml test-connection my-oracle
dba --settings config/runtime.yaml collect my-oracle active-sessions
dba --settings config/runtime.yaml collect my-oracle active-requests
dba --settings config/runtime.yaml collect my-oracle long-running
dba --settings config/runtime.yaml diagnose my-oracle slow
```

Oracle 监控账号需专用非 SYS/SYSTEM 账号，直接授予 CREATE SESSION，并由 DBA 人工审核后直接授予 SYS.V_$INSTANCE、V_$DATABASE、V_$SESSION、V_$SQL、V_$TRANSACTION 的 SELECT；V_$LOCK、V_$LOCKED_OBJECT、V_$PROCESS、V_$SESSION_WAIT 为可选证据视图。缺少可选锁视图时只使用 V$SESSION 的阻塞关系并在报告标记证据缺失；不会自动 GRANT。账号不得有角色、写权限对象授权或自有对象。预检是本次连接的保守检查，不是整个数据库授权模型的完整证明。

当前执行时长依据 SQL_EXEC_START；V$SQL elapsed/cpu/read 字段明确标为跨执行累计值。Idle Transaction 要求 INACTIVE 会话与 V$TRANSACTION 真实关联。报告列出当前/上次 SQL、来源字段、锁链、多层根阻塞者、异常和采样质量；无法可靠取得客户端 IP 时明确显示 unavailable。ReferenceCommand 仅为纯文本，Toolkit **不会执行 KILL SESSION**。

V0.7 只采集当前实例的 V$ 视图；RAC 模式可检测并标记跨实例快照缺失，不生成不完整 RAC 图的 KILL 参考命令。Oracle 11g 未验证。2026-10-15 在隔离 Oracle Free 23.0.0.0.0 容器中，使用临时最小权限监控账号验证了全部新增只读 SQL；本机缺少 python-oracledb 离线包，因此真实 Python 连接及 CLI 集成仍待验证。细节见 [docs/VERIFICATION_V0_7.md](docs/VERIFICATION_V0_7.md)。

