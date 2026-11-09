# DBA Toolkit 项目记忆与会话恢复指南
## Project Memory & Session Handover

> 本文用于帮助新的 ChatGPT/Codex 会话恢复项目背景、架构、安全边界和关键历史决策。它是摘要，不是任务状态台账，也不能替代源代码、正式规划和验收证据。
>
> 最近整理：2026-11-09。整理时所在分支：feature/top-sql-readable-report-20261108。每次恢复工作必须重新检查实际分支、HEAD 和工作区，不能把本文件中的时间点当作当前仓库状态。

---

# 1. 项目是什么

DBA Toolkit 是面向 DBA 的跨数据库、离线、只读诊断和性能分析工具。目标是在数据库变慢、业务阻塞、事务未提交、资源竞争、日志或临时文件增长、复制异常等场景中，帮助 DBA 快速采集证据、关联身份、分析数据库原生机制并生成可复核报告。

核心原则：

**工具负责采集、关联、分析和说明；DBA 负责判断、沟通和执行。**

它不是自动运维或自动修复平台。生产诊断只读；不自动 KILL、取消请求、修改数据/DDL/参数/权限、重启、切换或修复数据库。

# 2. 产品范围与当前版本

## 2.1 数据库产品目标

| 数据库 | 正式产品目标 | 当前定位 |
|---|---|---|
| MySQL | 5.7、8.0+ | 当前主线 |
| PostgreSQL | 14+ | 当前主线；PG12/13 不属于当前正式目标 |
| SQL Server | 2008 R2+ | 当前主线；旧版 DMV、Showplan、ODBC/TLS 需独立验证 |
| Oracle | 11gR2+ | 当前主线；视图、字段、权限和计划语义按版本验证 |
| MongoDB | 4.0+ | 长期目标，当前不宣称已有诊断实现 |
| Redis | 4.0+ | 长期目标，当前不宣称已有诊断实现 |

版本目标不是功能支持承诺。TARGET、IMPLEMENTED、自动化覆盖和指定版本真实 E2E 必须分开描述。不能从版本范围中的“+”推导未来版本自动支持。

## 2.2 版本与发布

- 当前产品版本为 0.9.1。
- 0.9.1 GitHub Release 已发布；具体包、哈希和发布验证见 Acceptance §60。
- 正式 1.0 仍需按实际功能、版本矩阵、离线部署、升级/回滚和 E2E 证据另行审核；不能因为规划文档创建就宣布 1.0。
- 不擅自创建新 Tag、Release、版本号或合并 main。

# 3. 用户的核心产品取向

- 生产数据完整性和可用性优先于诊断速度或功能覆盖率。
- 不熟悉、缺少充分验证或在测试环境无法模拟生产并发的变更路径，应先保留为待验证/待维护窗口事项，不为追求便利而引入不确定风险。
- 诊断结论必须说明证据、范围、时间、完整性和限制；证据不足时宁可 UNKNOWN / PARTIAL / INSUFFICIENT，也不能编造根因。
- 默认低开销、一次性、单实例、串行、有界采集；不擅自引入常驻 Agent、后台轮询或双采样。
- CLI 用于快速操作，HTML 是主要人工分析报告，JSON 是结构化接口，JSONL 是内部运行审计。
- 主要面向 Windows 运维机、离线网络和堡垒机后的数据库环境；Linux 保留原生运行目标，但必须独立验证。
- 中文结论优先，数据库原生术语、SQL、错误码、视图字段和等待事件保留原文并可解释。

# 4. 安全边界与生产操作规则

## 4.1 生产诊断

- 只连接用户通过 Inventory 明确选择的一个目标实例；不得扫描 Inventory 后逐一连接，也不得连接发现的复制节点/对端。
- 生产只使用专用最小权限监控账号；不得使用 sa、root、SYS、SYSTEM、postgres 超级用户或其他最高权限账号。
- SQL 只能来自经过审查的 SQL Registry；Registry SQL 必须 READ_ONLY、reviewed、设置 timeout_seconds 和 max_rows。
- 采集必须有超时、行数、结果大小、SQL 文本长度和清理边界。
- 权限由 DBA 按 docs/DATABASE_PERMISSIONS.md 提前准备。Toolkit 不运行 GRANT/REVOKE、不生成授权 SQL、不做运行时逐功能权限 readiness 推断。
- 数据库拒绝受审查的只读 SQL 时，保留脱敏后的原始厂商错误，提示检查权限文档；不能把权限不可见伪装为空数据或健康。
- 任何 KILL、cancel、terminate 或修复语句只允许在证据充分时作为纯文本供人工审核，Toolkit 永远不执行。缺少根会话、身份冲突、循环、截断或关键证据失败时不得生成参考终止命令。
- SQL 文本、执行计划、客户端、主机和对象信息可能包含内部敏感数据；报告和 SQLPLAN 必须按内部敏感资料管理。

## 4.2 凭据

- 密码、Token、密文、Credential Store、真实 Inventory 不得进入 Git、提示词、CLI 参数、环境变量、日志、报告或测试产物。
- Inventory 只保存 credential_alias，不保存 password/pwd/secret/token。
- 凭据优先由 OS Credential Manager / Keyring 提供；JIT 输入必须隐藏并按用户选择处理。
- 加密 Store 解锁失败、损坏或 ACL 不安全时 fail closed，不静默回退。
- 不把测试管理员账号转借给 Toolkit。测试驱动与 Toolkit 监控账号严格分离。
- 不输出凭据文件内容、密码、密文或原始配置对象。

## 4.3 本地测试实验室

用户已授权在其明确指定的本机 Docker 隔离实验室中进行有界测试数据准备、DDL/DML、索引/统计信息测试和负载测试。该授权仅限核实身份后的指定测试实例，不延伸到生产或其他 Inventory。

- 先确认目标容器、数据库、测试资产、环境标签、SQL、对象、时长/并发、资源预算和清理路径。
- 测试驱动可在本地安全读取用户指定的凭据配置；不得打印、复制、修改或上传该配置。
- 对本地配置只允许使用 dba.lab_config 的静态 AST 读取器和固定 allowlist；禁止 import/exec/eval 或打印原始配置。
- 测试驱动可使用获准的高权限身份；Toolkit 诊断仍只用独立监控账号。
- 不删除 Docker 数据卷或宿主机备份目录；高资源、实例重启、备份删除和不可逆操作不属于常规授权。

# 5. 执行环境与真实 E2E 限制

## 5.1 Windows 本地 E2E

四数据库真实连接和 E2E 必须从获准的 Windows 宿主机上下文执行，使用 Windows PowerShell 和项目虚拟环境。

Codex 管理的 PowerShell 或隔离容器即使显示 Windows、机器名或相同路径，也不等于已取得用户 Windows 主机资源访问权。隔离环境中的 127.0.0.1 也不代表 Windows 宿主机。

每次真实连接前确认：

1. 实际 OS/进程上下文及是否为获准 Windows 宿主机。
2. 工作目录、Python、CLI 的实际路径。
3. 唯一目标 Inventory 的 host/port/database 和 environment=test。
4. 当前凭据会话/OS Credential Provider 可用。
5. 只访问用户指定资产，不扫描 Inventory、不猜测端口或凭据。

如果无法确认获准 Windows 上下文，必须在数据库连接前停止，提供用户可执行的 PowerShell 命令并等待结果。不得反复尝试同一隔离限制下的连接，也不得把 Fake/Mock 测试称为真实 E2E。

E2E 证据必须明确标注 WINDOWS_LOCAL_REAL_E2E、ISOLATED_OFFLINE_TEST 或 NOT_EXECUTED，并注明具体版本、资产、场景、账号边界和清理情况。

## 5.2 本地实验室概况

已建立 MySQL、PostgreSQL、SQL Server、Oracle 四数据库 Docker 测试实验室，并有 Chinook、Sakila、Pagila、AdventureWorks、HR、CO 等样例库。不同容器版本、端口、实例身份和当前运行状态必须在每次测试前重新核实。

实验室凭据配置和真实 Inventory 保存在用户本机，不在本仓库记载具体路径、主机地址、用户名或密码。

## 5.3 压测约束

已有四库并发负载脚本，SQL 注释标识为 DBA_TOOLKIT_LOAD。脚本通过函数转换、JOIN、自连接、聚合和排序构造压力；具体执行计划由优化器决定，必须用实际计划核实，不能保证每次都全表扫描。

先做低并发、短时冒烟测试，再逐库执行正式压测。默认建议 100 会话、45 秒；脚本上限 300 只是允许值，不是推荐值。默认一次只压测一个数据库，不得同时启动四库高并发，除非用户另行批准并确认资源预算。

# 6. 当前架构与实现约束

## 6.1 现有架构

沿用现有代码，不为专项功能另建一套基础设施：

- Inventory / Connection Profile：明确目标资产和非敏感连接参数。
- Credential Provider / Resolver / Session：安全获取凭据。
- Version-Aware Profile：版本能力路由，未知能力 fail closed。
- Reviewed SQL Registry：固定、只读、有界的 SQL 来源。
- Preflight：身份、版本、必要安全检查。
- Adapter / Collector：执行受审查 SQL，返回时间、行数、截断、错误和来源。
- Analyzer / Incident / Findings：表达现象、证据和不确定性。
- ReportModel / Presenter / Renderer：统一 CLI、JSON、HTML 输出。
- Runtime Manifest / Wheelhouse / Portable Update：离线运行时、驱动、升级和回滚。

新增数据库原生 Analyzer 可以有自己的规则和结果，但必须复用现有 Profile、Registry、Collector、Incident、ReportModel 和 Renderer，不得复制 Credential、Adapter、Permission 或报告框架。

## 6.2 报告

- HTML 自包含、离线可打开、内联 CSS、无外部资源。
- 第一层给 DBA 中文结论、影响、证据等级和下一步安全检查。
- 第二层展示 SQL、请求/会话、事务、等待、阻塞、计划和资源关联。
- 第三层保留数据库原生身份、来源、版本、单位、采样时间、完整性、权限和错误详情。
- HTML 不应出现原始 dict/list/Python repr 或未经整理的 Collector payload。
- 表格使用场景固定字段，明确总数/显示数/省略数。
- 计划 XML 不默认嵌入 HTML；SQL Server 可将 .sqlplan 作为独立敏感附件。
- CLI、JSON、HTML 必须基于同一诊断结果，结论一致。
- 不在 HTML 模板里实现诊断或根因推断。

## 6.3 Top SQL 身份

- SQL Server 当前请求（Slow）与缓存累计 Top SQL 是不同采集语义，不能为了展示方便合并 Collector 或 SQL。
- SQL 文本相似、query_hash 单独相同都不足以证明身份关联。
- SQL Server Top SQL 与 Slow 关联必须使用完整可用的原生语句身份（SQL handle、statement offsets、database ID 等）和计划身份（plan handle、query plan hash、generation 等），并保留 MATCHED / UNMATCHED / IDENTITY_INCOMPLETE 等状态。
- PostgreSQL、MySQL、Oracle 保留各自原生身份语义，不能强制跨库字段一致。

# 7. 四数据库原生诊断方向

这些是产品规划候选能力，不代表已实现或已验收。每次只选一个有界场景，先审官方文档、版本路由、权限、采集成本和反向证据。

- Oracle：Child Cursor、Bind Mismatch、Bind Peeking、Adaptive Cursor Sharing、Hard Parse、Library Cache/Mutex、ITL、Undo/Redo、Temp、FRA、RAC。
- PostgreSQL：HOT、Bloat、Autovacuum、长事务/xmin、XID Wraparound、WAL/Replication Slot、LWLock、Temp/Spill、Generic/Custom Plan、TOAST。
- SQL Server：Parameter Sniffing/PSP、Plan Cache、Statistics/Cardinality、TempDB PFS/GAM/SGAM 与元数据竞争、Memory Grant/Spill、Latch/Spinlock、Scheduler、Transaction Log、Version Store/ADR、AG。
- MySQL：Adaptive Hash Index、Doublewrite、Change Buffer、Buffer Pool、Redo/Checkpoint、Undo/Purge、MDL/Record/Gap/Next-Key Lock、热点页、Binlog/Group Commit、复制和临时文件。

专项诊断必须区分机制、观察信号和实际根因。例如 AHI 启用不等于 AHI 争用；Dead Tuple 多不等于严重 Bloat；PAGELATCH 不等于 PAGEIOLATCH；高 Memory Grant 不等于主机内存耗尽；Child Cursor 多不等于共享池故障。

# 8. 重要历史问题与开发陷阱

- SQL Server Slow：当前请求证据与累计 Top SQL 分开；会话字段优先来自正确 DMV；client_interface_name 来自 sys.dm_exec_sessions，网络地址来自 sys.dm_exec_connections。曾出现 SQL Server 2022 错误 207 / SQLSTATE 42S22，已在 Acceptance §66 记录修复与离线验证。
- SQL Server WAITFOR：只有 command 和 wait_type 都为 WAITFOR，且有充分证据确认无阻塞、无未结束事务，才排除异常等待/长运行发现。若阻塞或事务证据缺失、失败或截断，必须保留不完整状态；若混合快照另有真实异常，不能因 WAITFOR 而掩盖它。具体回归见 Acceptance §67。
- SQL Server Slow 的多 DMV 顺序采集不是原子快照；必须保留每个采样的成功、失败、截断和窗口。
- MySQL Driver Provenance：曾观察到导入模块版本 2.2.8 与 distribution metadata 1.1.3 不一致。对应 Backlog MYSQL-DRIVER-PROVENANCE-001；先在干净支持环境复现并查包来源，不要盲目重装或改版本。
- MySQL 流式游标：截断/异常时直接关闭未缓冲游标可能触发剩余结果 drain；必要时先关闭连接，再关闭游标，防止清理操作读取整批数据。
- PostgreSQL：累计计数器不能直接当速率；需要显式有界区间差值并检查重置、身份和时间窗口。普通 VACUUM 不保证文件缩小。
- Oracle：Child Cursor、绑定变量和计划必须结合版本、SQL_ID、CHILD_NUMBER、PLAN_HASH_VALUE、共享失败原因和真实执行证据，不能仅凭 VERSION_COUNT 或等待事件下结论。
- SQL Registry 静态检查只是附加防线，不是权限控制或安全证明；真实边界仍是专用账号权限、版本路由、受审 SQL 和有界执行。
- 真实报告可能包含业务 SQL 和敏感对象名，不要把用户提供的 HTML/JSON/SQLPLAN 复制进 Git。

# 9. 规划和任务状态的唯一来源

必须按以下职责读取，不得混用：

| 文档 | 职责 | 是否为状态来源 |
|---|---|---|
| docs/PROJECT_PLAN.md | 产品目标、范围、能力地图、长期规划 | 否 |
| docs/TODO.md | 人类可读的当前待办摘要与入口 | 否，镜像/索引 |
| docs/roadmap/DBA_TOOLKIT_V0.9.1_ROADMAP.md | Phase 边界、依赖、进入/退出条件 | 否 |
| docs/roadmap/DBA_TOOLKIT_V0.9.1_BACKLOG.md | 正式任务 ID、状态和唯一任务台账 | **是，唯一来源** |
| docs/roadmap/DBA_TOOLKIT_V0.9.1_PROGRESS.md | 当前任务、下一步、等待用户动作和会话恢复指针 | 当前指针 |
| docs/roadmap/DBA_TOOLKIT_V0.9.1_ACCEPTANCE.md | 测试、实机、版本、报告、验收证据 | 证据来源 |
| docs/PROJECT_RULES.md | 长期技术、安全、环境和操作约束 | 约束来源 |
| 根目录 AGENTS.md | Codex/Agent 工作流程和仓库级指令 | 执行约束 |

任务状态词汇以 Backlog 为准。TODO 只显示便于操作的摘要；如与 Backlog 不一致，Backlog 胜出，并应在下次文档维护时修正 TODO。

# 10. 当前恢复状态快照（2026-11-09）

> 以下仅是本次整理时的状态快照。开始新工作前必须重新读取 Backlog/Progress/Acceptance。

- 当前阶段：Phase 5 — Unified HTML Incident Workbench。
- 当前任务：V091-P5-WORKBENCH-001，Backlog 为 IN_PROGRESS。
- 最新记录：Acceptance §67，SQL Server Slow WAITFOR 分类离线回归。
- WAITFOR 规则离线测试：修改前聚焦基线 181 passed；修改后聚焦 200 passed；完整 pytest 1390 passed、3 skipped；Ruff、严格 MyPy、Registry validation、diff check 通过。
- WAITFOR 新规则的 Windows 本地真实 E2E：NOT_EXECUTED。用户 review 并另行授权后，才可在已核实的 Windows 宿主机和 SQL Server 2022 测试资产复测 standalone WAITFOR 与混合阻塞场景。
- P5 尚未完成：四数据库真实报告生成，以及真实报告 JSON/HTML parity 仍待验证。合成离线 parity 不能替代真实报告验收。
- V091-P2-TOPSQL-INTEGRATION-001 已由用户接受为 DONE，仅限 Acceptance §63 所记录范围；不要重复开发该集成，也不要由此推断所有版本已验证。
- PORTABLE-UPDATE-001 为 E2E_PENDING：跨版本 Update 验收仍未完成。
- OFFLINE-RUNTIME-001 为 E2E_PENDING：真正无 Python 主机及物理断网验收仍未执行。
- MYSQL-DRIVER-PROVENANCE-001、旧版本兼容、Linux 驱动/TLS、实验室 SQL Server TLS、MongoDB/Redis、pt-query-digest/pgBadger 等保留在 Backlog。
- Phase 2 Top SQL Delta 不应在当前 P5 尚未完成时擅自启动；Resource Context Contract 需要单独授权。

# 11. 新会话恢复流程

1. 阅读根目录 AGENTS.md 和 docs/PROJECT_RULES.md。
2. 阅读 docs/PROJECT_PLAN.md、本文件和 docs/TODO.md。
3. 阅读当前 Roadmap、Backlog、Progress、Acceptance；以 Backlog 读取正式状态，以 Progress 读取当前指针，以 Acceptance 核对证据。
4. 检查 git status --short、git branch --show-current、git log -1 --oneline；记录 HEAD 和工作区，不覆盖或清理用户文件。
5. 明确用户当前要求和已授权范围；只继续当前明确批准的任务，不因看到 Backlog 候选项就自动开工。
6. 若需要真实数据库 E2E，先完成 Windows 宿主机、唯一测试资产、环境标签和凭据会话 preflight；无法确认则停止并给用户 PowerShell 命令。
7. 一次只处理一个有界任务。先离线测试，再在获准条件下进行真实 E2E。
8. 任务状态只改 Backlog；证据只写 Acceptance；当前指针只改 Progress；待办摘要同步更新 TODO。
9. 修改后检查 diff、测试、静态检查和敏感文件；不要使用 git add . 或 git add -A。
10. 没有用户明确授权，不提交、push、合并 main、创建 tag 或发布 Release。阶段完成、范围扩大和新任务启动均需用户审核。

# 12. 更新规则

- 每次完成一个正式任务：先更新 Backlog 状态，再补 Acceptance 证据，然后更新 Progress 和 TODO。
- 每次发现新的用户决策、长期约束或重要故障教训：更新 PROJECT_MEMORY；不得将临时猜测写成已确认事实。
- 产品范围变化：更新 PROJECT_PLAN，并按需调整 Roadmap/Backlog。
- 只在 TODO 摘要中保留当前行动、关键等待项和少量长期入口；完整任务清单仍在 Backlog。
- 记录事实时注明来源、日期、数据库版本和证据等级。
- 保留历史记录；不要为了让文档看起来简洁而删除仍有追溯价值的旧证据。
- 不将真实 Inventory、凭据配置、密码、日志、报告、SQLPLAN、压测输出和本地运行时写入本仓库。

项目记忆文档结束。
