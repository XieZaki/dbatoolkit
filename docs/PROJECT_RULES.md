# 长期项目约束

- 项目是内部 DBA 只读采集、检查、关联、诊断、报告工具；工具查清楚，DBA 做决定。
- 网络：DBA → 堡垒机 → 运维机器 → 生产数据库。只从运维机器主动连接数据库；不依赖生产服务器反向连接。
- 生产无 Internet。安装依赖必须提前在匹配目标 OS/Python 的联网机器准备离线 wheelhouse；禁止运行时下载。
- 优先 SQL Server、PostgreSQL、MySQL、Oracle；Redis/MongoDB 后续扩展。
- 当前 V0.8 在 V0.7.1 基础上增加 SQL Server、PostgreSQL、MySQL、Oracle 的 disk-growth 当前状态诊断；各库只连接用户指定实例，不扫描 Inventory 执行诊断，不自动安装驱动。
- 默认资产目录是 config/inventory/，有 YAML 时优先于旧 config/inventory.yaml；演示资产只在 examples/inventory/，不得混入生产资产池。跨文件 ID 必须唯一，来源路径仅用于校验/调试。
- 永远不得自动执行 KILL、terminate/cancel、任何数据/DDL/权限变更、重启、切换、提升、扩容、参数修改、索引变更或强制计划。
- 不提供 --execute、--force 或其他执行参考命令/绕过只读限制的接口。
- ReferenceCommand 只保存文本，永远无 execute 方法；DBA 在原生客户端人工判断与执行。
- PostgreSQL pg_cancel_backend/pg_terminate_backend 只可作为已确认 Root Blocker 的纯文本 ReferenceCommand；Toolkit 不执行函数，也不自动生成 SQL 变更语句。循环、不完整或截断采样不生成参考命令。
- MySQL KILL QUERY/KILL 只可作为已确认 Root Blocker 的纯文本 ReferenceCommand；Toolkit 不执行。Sleep + 未结束事务必须有关联 InnoDB 事务证据，不能仅凭 Sleep 判断。循环、不完整或截断采样不生成参考命令。
- KILL 只能由已知正整数根会话生成参考文本；缺失根会话不生成命令。循环、特殊负数 blocker、采样截断和缺失数据必须显式报告。Sleeping 未提交事务结论必须关联事务 DMV，不能只用 open_transaction_count。
- 仅显式调用的隔离 Docker 集成测试可按用户授权创建测试对象、运行测试事务并 ROLLBACK/清理；这些脚本位于 tests/integration，不能通过 Toolkit 或正式 Registry 执行。诊断账号始终只读，禁止 KILL。
- 用户确认的本地 Docker 实验室是有界测试资源：仅限已核实身份的指定本机实例和样例库，不能据数据库名相同扩展到其他 Inventory、主机或服务。用户已授权在这些隔离实验室中由独立测试驱动进行有界的管理员级准备、DDL/DML、负载、参数/账号测试与清理；每次运行前仍须审阅脚本实际连接目标、SQL、对象、权限、时长/并发/资源上限和清理/恢复路径。优先复用已有样例数据和脚本；常规实验室操作不需重复申请，但高资源、实例重启、数据卷/备份删除或不可逆操作不在常规授权内。不得删除 Docker 数据卷或宿主机备份目录。
- 测试驱动与产品诊断身份必须分离。可从用户指定的本地测试驱动配置在本地进程内读取已确认实验室的测试凭据；不得输出凭据、写入提示词/命令行/环境变量/日志/报告/仓库，或复制、修改、删除该配置。测试驱动管理员权限绝不转借给 Toolkit；Toolkit 仍使用独立最小权限监控账号、Reviewed SQL Registry、Preflight、超时/行数边界和只读执行路径。
- 检查或汇总本地实验室 Python 配置时，禁止 import/exec/eval 或打印原始配置；只可通过 `dba.lab_config` 的静态 AST 读取器读取固定 allowlist 中的 `DATABASES.<mysql|pg|oracle|sqlserver>.{host,port,database,user}`。普通配置对象不包含 `password`；只有连接专用接口可返回脱敏 `Secret`，并仅在建立数据库连接时显式传给连接回调。调用方不能传入字段名扩展 allowlist。Allowlist 必须依据已确认的消费者字段合同；测试仅使用合成临时配置，源配置不得复制、修改或持久化。
- 仅针对用户确认且身份核实的本机 Docker SQL Server 实验室，允许保留实例级 `Encrypt=no` / `TrustServerCertificate=yes` 临时例外以推进核心诊断验收。该例外不得改变产品/生产 TLS 默认值、不得复制到生产 Inventory，也不得描述为符合生产 TLS 标准；实验室 TLS 证书加固单独跟踪。凭据加密、最小权限和 Toolkit 只读边界不受此例外影响。
- Inventory 仅存 credential_alias，不保存 password/pwd/secret/token。日志禁止凭据与带密码连接串。
- 生产只使用专用最小权限监控账号；禁止 sa/root/SYS/SYSTEM/postgres 超级用户及其他最高权限账号。
- Credential Provider 优先从 Windows Credential Manager 或 Linux 安全系统 Keyring/Secret Service 读取 credential_alias；已验证不存在时允许统一 CredentialResolver 做 JIT 隐藏输入并明确征求安全保存同意。拒绝保存只限本次进程使用；不得自动初始化 Encrypted Store。现有加密 Store 解锁、认证、格式或权限失败必须 fail closed，不能回退到 JIT。
- Credential Store 优先使用 OS 安全密钥环；允许明确初始化的 AES-256-GCM + Argon2id 用户级加密 Store；禁止明文/自制加密持久化和 CLI 密码参数。`credentials list/show/test/delete/set` 不显示密码、hash 或密文；删除和覆盖必须交互确认。生产拒绝 Mock，包括开发中创建后传给生产 Adapter 的情况。
- Credential Store 可继续扩展企业 Vault/PAM Provider；适配器不直接依赖凭据存储实现。
- Credential session data must remain in process memory only and must never be persisted to disk, environment variables, registry, shell history, logs, or reports.
- Credential session IPC must be restricted to the current OS user and must fail closed when peer identity cannot be verified.
- Master passwords must not be retained after encrypted credential store unlock. Credential session expiration must be absolute and bounded.
- Credential Session Agent may retain a derived data key only in process memory; it must never retain the master password or persist the key. The legacy JSON encrypted-file backend is read-only: GET and metadata reads require server-side target Store binding, and initialize/SAVE/SET/DELETE/rekey are rejected at the Provider and Session boundaries. Migration may read the old Store but must leave it unchanged. The explicitly selected SQLite backend retains its bounded session only with server-side file/Store binding and fresh authenticated reads on each operation; lock, expiry and authentication failure clear it. SQLite is never selected by `auto`, and no real Store is migrated automatically.
- Inventory `connection` 仅允许按数据库类型审核过的非敏感连接参数。统一 Resolver 顺序为 instance.connection > 对应数据库 runtime settings > 代码安全默认值；未知项和任意层级的敏感字段必须由 validate 拒绝。
- SQL 独立存储；正式加载必须 READ_ONLY 且 reviewed=true，设置 timeout_seconds/max_rows。
- MySQL Registry 的 SELECT 以外只读语句采用封闭精确白名单：`SHOW BINARY LOGS`、`SHOW GRANTS`、`SHOW SLAVE STATUS`、`SHOW REPLICA STATUS`。每条必须是独立审核的固定 Registry 脚本；禁止扩大为任意 SHOW。复制状态查询按版本路由，需要专用监控账号具备只读 `REPLICATION CLIENT`，不得使用 SUPER/BINLOG_ADMIN。
- 静态 SQL 检查只是附加保护，不是权限控制或安全证明。审查函数副作用，最终边界是数据库监控账号权限。
- Adapter 只接受自身 Registry 签发并在执行前复核内容/元数据未变更的已审查脚本。禁止任意 SQL 字符串执行接口。
- SQL Server 使用 pyodbc，Microsoft ODBC Driver 18 优先、17 兼容回退；默认连接超时 5 秒、显式加密并验证证书；autocommit=true，不执行初始化修改语句。每次执行前为新游标设置有效超时=min(metadata.timeout_seconds, diagnostics.diagnostic_sql_timeout_seconds[, 已配置的 query_timeout])（C-11）。
- PostgreSQL 使用 psycopg 3；默认 SSL verify-full、连接超时 5 秒。连接启动参数启用只读事务和有界 statement_timeout，诊断连接 autocommit，断开时关闭；无任意 SQL、修改会话状态的初始化 SQL 或后台取消动作。按 C-12，首版不发送协议级 CancelRequest：超时依赖服务端 `statement_timeout`，另设客户端网络等待上限；超时后关闭连接，不复用。监控账号需要跨会话统计与设置读取能力，不能是超级用户或具管理属性的账号。
- MySQL 使用 PyMySQL 1.1 系列；默认 SSL 验证证书和主机名、连接超时 5 秒，autocommit、禁用 local infile。生产只允许专用监控账号；连接安全检查确认身份、Performance Schema 是否启用及已知危险/可写授权与活动角色，不判断单项诊断是否具备所需读取权限。Collector 被数据库拒绝时保留脱敏后的数据库错误并提示查看部署授权文档。流式游标有界提取，截断时关闭连接，避免游标关闭时遍历全部剩余行；read_timeout 是网络读取限制，不等于整批查询绝对墙钟上限。按 C-13：只读 SELECT 优先使用原生 `max_execution_time` SESSION 设置（5.7.8+）；无法使用该机制的 SHOW 脚本，仅允许经独立控制连接对本次运行实际记录的目标连接执行 `KILL QUERY <CONNECTION_ID>`。这是用户明确批准的唯一 KILL 例外，限制见下方「MySQL KILL QUERY 例外」。
- Database permissions are deployment preparation documented only in `docs/DATABASE_PERMISSIONS.md`; README links to that guide. Do not maintain a Permission Registry, per-feature readiness/missing-permission calculation, or GRANT/REVOKE generator. When a database denies a reviewed read, retain the sanitized vendor error and point the operator to the guide. Keep version routing, read-only Registry enforcement, and any separately justified dangerous-account safety checks; never execute GRANT/REVOKE or account-management SQL.
- Oracle 使用 python-oracledb Thin；只接受 service_name 或 SID 其一，账号必须专用且仅持必要直接只读授权。账号安全检查拒绝 SYS/SYSTEM、已授角色、非监控系统权限、写权限对象授权和自有对象；具体 V_$ / DBA 视图可见性由所选只读查询的实际结果判定，可选证据缺失时按场景降级。Toolkit 不执行权限变更；call_timeout 限制驱动调用而非完整流程墙钟。Oracle KILL SESSION 仅为已确认单实例完整根会话的纯文本 ReferenceCommand；RAC 跨实例快照缺失时不生成。任何 Oracle 诊断 SQL 均来自 Registry。
- fetchmany(max_rows) 有界读取，达到上限就标记 truncated=true（表示可能截断，不额外读取整批结果）；请求 SQL 文本也有独立长度上限和标志。
- 连接时执行身份、版本、只读配置/能力及必要的危险账号安全检查；不逐项预检诊断读取权限。数据库拒绝受审查的只读 SQL 时，报告保留脱敏后的数据库错误并提示查看 `docs/DATABASE_PERMISSIONS.md`。只读 Registry 执行、版本路由及查询边界仍是强制安全检查。
- 不将 readonly 驱动提示、静态检查或预检宣称为所有数据库写权限的完整证明。当前不支持 Azure SQL Database 的不同权限模型；SQL 兼容目标为 2008 R2+，2022+ 使用 VIEW SERVER PERFORMANCE STATE。目标不等于已通过实机验证。
- 旧版会话字段必须通过实际列能力检测选择受审查的兼容 SQL。sys.sysprocesses.dbid/open_tran 返回真实进程数据并注明来源，不能伪装为新版会话统计或编造缺失值。缺失列能力信息拒绝继续。Driver 18 与老服务器的 TLS/补丁互通须另行实测。
- 查询失败关闭游标和连接；连接非 autocommit 时尝试 rollback，失败仍尝试 close。驱动异常仅映射为受控错误类别/消息，禁止透传原始异常或连接串。
- NativeTool 与 SQL Collector 分离。未来 mysqlbinlog 仅使用远程协议及固定审查参数；禁止任意 shell 和数据库服务器破坏性操作。
- Analyzer 按数据库类型和场景注册。disk-growth 可按空间来源调用数据库专属分析器，禁止把统一规则硬套不同数据库。
- 预留 slow、blocking、long-transaction、disk-growth、log-growth、replication-lag、space、backup、connection、high-cpu、io、temp-growth。
- SQL Server 日志：log_reuse_wait_desc、使用率、恢复模式、最老/长事务、备份、AG、复制、CDC、VLF、增长配置及突发事务。
- PostgreSQL WAL：生成速率、pg_wal、LSN、slot/restart_lsn/confirmed_flush_lsn、retained WAL、流复制、归档、checkpoint、长事务、逻辑复制及写入压力。
- MySQL 空间：Binlog、Relay Log、Redo、Undo、临时/慢/普通日志、InnoDB；未来 mysqlbinlog 按库/表/操作/事务/时间分析。
- Oracle：Redo、Archive、FRA、Flashback、切换频率、归档目标、Data Guard 传输/应用延迟、RMAN、长事务及批量写入。
- 空间来源需区分数据文件、各类事务日志、Temp/TempDB/Oracle TEMP、Backup、Audit、Trace、Dump、普通日志。
- 不擅自增加 Web UI、微服务、Docker、CI/CD、无需求依赖或完整诊断。普通测试使用 Fake Driver，不连接任何真实数据库。

- 新增 Collector、Analyzer 或 Registry SQL 引入系统视图、DMV、数据库角色或权限时，必须核对并更新 `docs/DATABASE_PERMISSIONS.md` 中的部署授权说明及相关 SQL/版本路由测试。不得新增运行时 Permission Registry、逐 Feature 权限预检或 GRANT/REVOKE 生成器。
- Runtime 依赖以 deployment/runtime-manifest.yaml、pyproject extras 和 requirements lock 输入共同维护；生产 bootstrap 仅从本地 wheelhouse 使用 --no-index 安装，不自动访问互联网、执行 MSI/RPM/DEB、调用系统包管理器、修改 PATH/注册表或删除已有 .venv。
- SQL Server ODBC 选择顺序固定为 Driver 18 优先、Driver 17 兼容回退；连接必须显式设置 Encrypt 与 TrustServerCertificate。Oracle 固定默认 Thin Mode，不把 Instant Client 作为必需依赖。



### 诊断模块独立执行与故障隔离原则

1. DBA Toolkit 的各个诊断功能模块必须具备独立的执行、异常捕获、状态记录和降级能力。
2. 单个诊断模块执行失败，不得直接导致整个 Slow、Blocking、Incident 或其他诊断报告无法生成。
3. 当某个模块发生权限不足、SQL 执行失败、超时、版本不兼容、数据不可见或其他异常时：
   - 捕获该模块的异常。
   - 记录模块名称、执行对象、数据库原始错误代码、错误状态及原始错误信息。
   - 将该模块标记为相应的受限、失败或不可用状态。
   - 继续执行其他不依赖该模块的诊断功能。
   - 正常生成 HTML、JSON 和 CLI 诊断结果。
4. 对于权限不足的情况，必须明确展示「功能受限」，并尽可能展示数据库返回的原始错误，例如权限拒绝、对象访问失败及对应错误代码。
5. 不得把不同类型的失败统一显示成「不支持」或「未采集」。必须区分：
   - 权限不足：功能受限。
   - 数据库执行错误：采集失败。
   - 执行超时：执行超时。
   - 数据库版本不支持：版本不支持。
   - 元数据不可见：对象信息无法解析。
   - 尚未实现：待开发。
   - 未执行：未采集或未执行。
6. 数据库原始错误信息应尽可能完整保留，包括原生错误代码、SQLSTATE、错误消息和可用的状态信息。展示前必须脱敏密码、凭据、连接字符串、令牌、敏感参数和其他机密信息。不得将脱敏后的信息描述为未经处理的原始错误。
7. 不允许通过空值、0、正常状态或默认成功结果掩盖模块执行失败。
8. 某个模块失败时，只影响该模块的数据完整性和诊断结论。其他模块的有效证据必须继续保留。
9. 如果模块之间存在依赖关系，下游模块应标记为依赖失败或未执行，并说明原因；不应因此中止其他独立模块。
10. 如果数据库整体连接失败，只要本地报告生成链路仍可运行，也应尽可能生成连接失败诊断报告，说明连接错误，并将无法执行的模块标记为未执行，而不是直接退出且不产生任何报告。
11. 模块级错误必须在 HTML、JSON 和 CLI 中保持一致。整体报告可以标记为 PARTIAL，但不能因为单个可选模块失败而丢失其他有效诊断结果。
12. 不得为了恢复功能而自动扩大数据库权限、切换高权限账号或绕过安全限制。需要新增只读权限时，按数据库权限管理规则申请，并同步更新 `docs/DATABASE_PERMISSIONS.md`。
13. 新增诊断模块时，必须同时增加成功、权限不足、SQL 执行失败、超时和部分数据不可用等异常场景测试。
14. 不允许使用宽泛的异常捕获后静默忽略错误。所有异常必须有明确的状态、原因和可追踪记录。





## Windows 本地真实 E2E 执行约束

- SQL Server、PostgreSQL、MySQL、Oracle 的真实连接和 E2E 只能从获准的 Windows 宿主机上下文执行，使用 Windows PowerShell 和项目 `.venv`。终端显示 PowerShell 或路径看似相同，不足以证明进程能访问 Windows 主机资源。
- 每次真实连接前，确认实际操作系统/进程上下文、工作目录、Python 与 CLI 可执行文件、唯一目标 Inventory 的 host/port/`environment=test`，以及现有凭据会话可用。只测试用户指定的资产，不扫描 Inventory、不猜测目标。
- 严禁在 Codex 隔离环境执行真实数据库 E2E；隔离环境的 `127.0.0.1` 不代表 Windows 宿主机。不得把隔离环境中的 Credential Manager、命名管道或 Docker 端口拒绝解释为数据库环境故障。
- 若工具无法取得获准的 Windows 本地执行上下文，必须在访问数据库前停止，提供用户可在 Windows PowerShell 执行的完整命令并等待结果。不得重复运行已证明被同一隔离限制阻断的连接，不得读取/绕过凭据文件、改用另一套数据库或把 Fake/Mock 测试称为真实 E2E。
- E2E 报告注明 `WINDOWS_LOCAL_REAL_E2E`、`ISOLATED_OFFLINE_TEST` 或 `NOT_EXECUTED`；这些仅为证据来源标签，不是产品枚举。单元测试与静态检查只能作为离线证据。
- 导出的诊断 JSON 中将 `report.instance.credential_alias` 脱敏为 `[REDACTED]`；内部凭据解析仍保留真实 alias。不得输出用户名、密码或其他凭据值。



### 全局诊断 SQL 执行超时与故障隔离原则

1. 所有数据库诊断 SQL 必须遵守统一的全局执行超时配置。
2. 默认配置：
   - 参数：`diagnostic_sql_timeout_seconds`
   - 默认值：30 秒。
   - 适用范围：SQL Server、Oracle、MySQL、PostgreSQL 的所有诊断 SQL。
   - 必须通过项目现有的统一配置机制管理，不得在各 Collector 中重复定义超时值。
3. 超时范围包括 SQL 执行及结果获取阶段。连接超时与 SQL 执行超时分别管理。
4. 单条诊断 SQL 达到超时限制时：
   - 必须调用对应数据库驱动或数据库原生的查询取消机制。
   - 停止当前诊断模块的剩余依赖步骤。
   - 将当前模块标记为 TIMEOUT。
   - 记录 SQL 脚本名称、模块名称、实际耗时、超时阈值、执行阶段和原始错误。
   - 在确认取消完成及连接状态正常之前，不得复用该连接。
   - 取消成功且连接状态安全后，继续执行下一个独立诊断模块。
5. 不允许仅通过 Python Future、线程等待超时或异步任务超时来代替数据库端查询取消。必须验证数据库端实际停止执行诊断 SQL。
6. 如果查询取消无法确认：
   - 丢弃或关闭当前连接。
   - 不得将其重新放入可复用连接池。
   - 只能通过经过验证的独立连接路径继续其他模块；否则停止后续数据库采集，但仍应尽可能生成已有数据的报告。
7. 查询超时仅允许取消 Toolkit 自己发起的诊断 SQL，不得 KILL、CANCEL、TERMINATE 或影响任何业务会话。
8. 不同数据库驱动必须分别实现并验证超时和取消机制。不能将 Socket Read Timeout 等同于数据库端查询取消。
9. 超时信息必须在 HTML、JSON、CLI 中一致展示，包括：
   - 数据库类型及版本；
   - 诊断模块；
   - SQL 脚本相对路径；
   - 实际执行耗时；
   - 配置超时值；
   - Execute / Fetch 等执行阶段；
   - 数据库原生错误代码和 SQLSTATE（如果有）；
   - 原始错误信息（敏感信息脱敏）；
   - 查询取消是否成功。
10. 必须区分 TIMEOUT、权限不足、SQL 执行失败、版本不支持和未采集，不得混淆。
11. 一个诊断 SQL 超时不得导致整个诊断报告无法生成，也不得丢弃其他模块已经取得的有效数据。
12. 新增或修改 Collector 时，必须覆盖正常执行、超过超时限制、查询取消成功、查询取消失败、后续模块继续执行和报告正常生成等测试场景。
13. 如果某项功能确实需要超过全局默认超时，必须先进行性能评估，说明原因并经用户批准，不允许模块自行延长超时。
14. 每次新增或调整全局超时机制，必须同步更新项目配置说明、使用文档和测试。
15. 全局参数调整不代表允许无限制延长诊断 SQL 执行时间。

#### 设计原则（2026-11-12 架构审查确认，设计见 `docs/design/diagnostic-sql-timeout-and-fault-isolation-v0.9.1.md`）

16. 生效超时只由 `dba/sql_timeout.py` 的共享函数确定（C-11，用户 2026-11-13 批准）：`effective = min(Registry 脚本 timeout_seconds, diagnostics.diagnostic_sql_timeout_seconds)`。全局值默认 30 秒、范围 1..60，只能在设置文件的 `diagnostics:` 段配置，不能在引擎段或 Inventory 中重定义。引擎 `query_timeout`（Oracle `call_timeout`）和 Inventory `connection.query_timeout` 保留为可选的更严格上限，默认不设，只能缩短不能延长。审计日志必须分别记录实际下发值（`timeout_seconds`）和单脚本策略值；PostgreSQL `statement_timeout` 与 MySQL `read_timeout` 是连接级值，在单 SQL Deadline 实现前不能代表单脚本限制。
17. 单条 SQL 的期限覆盖 Execute 和全部 Fetch，使用同一个绝对 Deadline（`SqlDeadline`）。驱动按单次调用或单次往返计时的超时，不等于单条 SQL 的总期限；Fetch 循环必须检查同一期限，不得在每次 Fetch 时重新获得完整超时。
18. 数据库端取消要求（C-12、C-13 于 2026-11-13 批准）：
   - SQL Server：ODBC 查询超时，由驱动在本连接发送 Attention。
   - PostgreSQL：服务端 `statement_timeout`，加客户端网络等待保护；首版不发送协议级 CancelRequest。区分服务端确认取消（`57014`）与客户端取消状态未知。
   - MySQL：`read_timeout` 只是 Socket 读取上限，不是取消。只读 SELECT 优先使用 `max_execution_time` SESSION 设置（超时返回 `3024`）；SHOW 脚本按下方受限 `KILL QUERY` 例外处理。驱动和版本差异必须先验证，不得假设所有版本一致。
   - Oracle：`call_timeout` 触发驱动 break，必须区分连接仍可用（`DPY-4024`）与连接失效（`DPY-4011` / session dead）。
19. 超时结果必须区分五种情形：客户端等待超时、驱动执行超时、数据库端确认停止、取消失败、连接失效或状态未知。只有数据库返回的取消错误（如 PostgreSQL `57014`、MySQL `3024`、Oracle `ORA-01013`）或测试中的独立观测，才能记为数据库端确认停止。
20. 任何超时后都关闭当前连接，不做「验证后复用」，不重试同一条 SQL。后续独立模块只经完整连接与 Preflight 新建连接；新建失败则停止后续数据库采集，并用已有证据生成报告。
21. 单模块失败不影响独立模块：超时只终止该模块及其依赖步骤；依赖步骤显式标记依赖失败或未执行；报告整体可为 PARTIAL。保留现有分阶段采集结构。
22. 超时证据作为增量字段加入现有模块错误结构，HTML、JSON、CLI 从同一份数据生成，不另建平行错误模型。

#### MySQL KILL QUERY 例外（C-13，用户 2026-11-13 明确批准）

这是取消 Toolkit 自身诊断 SQL 的特殊能力，不代表允许一般性的 KILL 操作。权限说明见 `docs/DATABASE_PERMISSIONS.md`。

23. 目标连接执行诊断 SQL 前，必须取得该连接真实的 `CONNECTION_ID()`，并在内存中保存它与本次诊断任务、连接对象的对应关系。
24. 只能对本次运行实际记录的连接 ID 发送取消；不得根据用户、主机、SQL 文本或进程列表猜测，严禁扫描其他会话 ID。目标身份无法确认时禁止发送取消。
25. 取消必须经独立控制连接发出，该连接使用同一受控 Toolkit 监控账号，并走完整的连接与 Preflight 路径。
26. 只允许 `KILL QUERY <已记录的整数 ID>`。严禁 `KILL CONNECTION` 或不带 `QUERY` 的 `KILL`；严禁取消业务连接、其他 Toolkit 任务或人工会话。
27. 取消失败或无法确认时，关闭目标连接并记录真实取消状态（含数据库错误码），不得记为已取消。
28. 不得为取消能力申请 `CONNECTION_ADMIN`、`SUPER`、`SYSTEM_USER` 或其他管理权限；现有 MySQL 账号安全检查继续拒绝这些权限。

## CLI presentation rules

- Default CLI output is written for a human DBA. JSON is opt-in with `--json`; retain structured domain data for automation.
- Structured SQL execution logs are internal output and must not dominate normal terminal output. Keep them in a protected log sink and continue redacting sensitive fields.
- New features expose reusable domain results through the common presentation layer. Adapters and analyzers must not print directly to the terminal or build command-specific console output.
- New diagnosis scenarios extend `dba diagnose <database_id> <scenario>` unless a documented architectural reason requires another command.
- Human-facing errors explain the failure, impact, and next safe action when known. Access-denied errors retain sanitized vendor evidence and point to `docs/DATABASE_PERMISSIONS.md`; do not infer a missing grant that was not established by the failed query.
- `dba check` and `dba status` compose existing runtime, credential, identity/version/account-safety preflight, and collector capabilities; they must not duplicate database-specific analyzer logic.
- Human diagnose output is an incident-response summary, not a full evidence dump.
- Default terminal output should prioritize impact, duration, root-cause candidate, source, recovery action, and termination risk.
- Selected SQL, related sessions, Top-N analysis and evidence needed for DBA decisions belong in self-contained offline HTML reports; `--json` remains the structured machine-readable format within the collectors' own limits.
- Reference termination commands may be displayed only when evidence is sufficient and must never be executed by Toolkit.
- Any displayed termination command must include reason, transaction/rollback risk, and explicit `Toolkit Executed: NO`; missing, failed or truncated evidence suppresses the command.
- Non-blocking slow SQL does not produce a session termination command by default; unknown rollback duration stays `UNKNOWN`.
- Human diagnostic reports must be rendered as self-contained offline HTML with inline CSS and no external resources.
- Raw dictionaries, lists, JSON dumps, internal Python representations and collector payloads must not appear in human-facing reports.
- Report filenames use the database ID, scenario and local timestamp, with a numeric collision suffix instead of random UUIDs.
- Analyzer and Adapter code must not construct HTML; use the shared ReportModel and renderer/template layer.
- Escape all database-derived text before placing it in HTML. Long SQL and full unrelated session evidence stay collapsed.
- Human HTML reports are bounded analysis artifacts, not raw collector dumps; `--json` is the structured diagnostic interface and JSONL is for Toolkit runtime events.
- Human report tables use scenario-defined field sets, never dynamic unions of Collector fields. Session identity and request runtime are separate; unavailable-field explanations belong in Collection Quality.
- Every human-report omission is disclosed with total, displayed and omitted counts. A report-level `HumanReportPolicy` owns row, SQL text, appendix and byte budgets; limits do not change Collector or Registry limits.
- Immediate Recovery follows Incident Summary. Root blocker identity and critical paths, data completeness and safety statements must survive report-size reduction.
- Default human HTML does not embed raw execution-plan XML. An available SQL Server plan may be saved as a paired `.sqlplan` artifact under the local reports path; treat it as sensitive and ignore it in Git.
- Findings reference evidence sections instead of copying full evidence. Keep shared ReportModel/Renderer and split scenario presentation/database evidence normalization rather than creating per-database HTML renderers.
- Real Inventory files are local site configuration and must not be committed. Examples use disabled assets and reserved example addresses/hostnames.
- Credentials, reports, logs, SQLPLAN artifacts, local runtime environments, and secret-store exports must never be committed. `.gitignore` does not remove tracked or historical sensitive data.
- Repository updates and upgrade scripts must preserve local Inventory and OS credential storage; scripts never rewrite Inventory, read credential values, delete reports, or modify databases.
- Secret scanning checks Git-tracked files only and must not print candidate secret values. If the project is not inside a Git worktree, report that the tracking audit could not run.
- Plaintext credentials must never be stored in Inventory, source code, repository, logs, reports, temporary files, or runtime configuration.
- Encrypted credential stores must never be committed to Git, even when encrypted; disclosure enables offline password guessing.
- Credential encryption keys and master passwords must never be stored beside encrypted credential stores or on disk.
- Credential providers fail closed on authentication failure, corruption, unsupported cryptographic formats, and unsafe permissions; an existing store that fails to unlock must not fall back silently.
- Database adapters consume resolved credentials and do not implement credential-storage-specific logic.
- Debug and exception output follow the same secret-redaction rules as normal output.
- Replication diagnosis is observational only and reads metadata from the single Inventory-selected instance; it must not connect to discovered peers.
- Slow diagnosis resolves SQL, parser contract, support state, runtime variant, and collector slots atomically through `DiagnosticProfileResolver`. Slow SQL changes must update version/profile metadata, the deployment permission guide where needed, and consistency tests; no Collector may branch on server version.
- DBA Toolkit must never automatically promote, fail over, restart, reset, reconfigure, resynchronize, or repair database replication.
- Replication lag must not be converted between bytes and time unless supported by explicit reliable rate evidence; a single snapshot cannot establish a trend.
