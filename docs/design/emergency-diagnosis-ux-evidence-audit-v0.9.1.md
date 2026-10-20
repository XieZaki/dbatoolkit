# V0.9.1 紧急诊断 UX 与证据审计

> **版本范围决策（2026-10-19）：** 本文列出的 PostgreSQL 13–18 和其他旧版本矩阵描述审计时的代码/Profile/证据，不代表当前正式产品目标。当前关系型目标为 MySQL 5.7/8.0+、Oracle 11gR2+、PostgreSQL 14+、SQL Server 2008 R2+；PG13 兼容代码保留但不构成正式支持声明。MongoDB/Redis 4.0+ 是长期目标，尚无诊断能力声明。产品目标与实现、验证状态分开管理，详见 Roadmap 与 Acceptance。

审计范围：当前仓库的 CLI、Inventory/版本检测/Profile Resolver、SQL Registry、权限契约、Slow 与 Disk-Growth 采集/分析、终端/HTML/JSON 呈现、报告写入和相关测试。结论只针对源码定义及现有测试；Profile 存在不代表该版本已在真实实例验证。本轮没有连接数据库，没有修改生产代码、SQL、Inventory 或数据库。

状态标记：**IMPLEMENTED** 表示代码和测试有直接证据；**PARTIAL** 表示存在实现但未满足应急目标或证据不完整；**MISSING** 表示没有发现对应实现/测试。

## 1. Executive Summary

当前版本已经具备只读、版本路由、必需/可选证据、根阻塞图、长事务/Idle Transaction 判断、文本型人工参考命令、自包含有界 HTML 和 JSON 诊断结构。Slow 的核心分析能识别根阻塞，也能在有事务 DMV/视图佐证时识别空闲持有事务；Disk-Growth 能区分多种存储来源、列出保留因素和显式说明历史速率缺失。

但当前 `dba diagnose <id> <scenario>` 是同步串行路径，CLI 在采集、分析、HTML 构建和写盘之后才输出 incident summary；控制台和 HTML 的主要标签/说明为英文。Slow 只有一个最大根阻塞候选加按耗时排列的 SQL，没有跨根阻塞、Idle Transaction 和长 SQL 的统一 TOP 3～5 排序。ReferenceCommand 是受限生成的文本而非身份验证后的安全执行令牌，进程标识复用风险仍存在。Disk-Growth 目前主要是**当前容量/保留状态诊断**，不是通用增长率诊断，也没有普遍把磁盘/日志来源归因到某个会话。

**结论：APPROVE_WITH_CHANGES。Can implementation start: YES。** 建议先实现一个同步、单连接、顺序分阶段的 P0：核心应急证据先生成中文终端摘要；随后采集不重复的补充证据、生成中文离线 HTML，并在最后输出报告路径。JSON 保持一次性、字段稳定的完整结构。没有必要引入并行采集、队列、后台服务或第二套 Profile Framework。

## 2. Product Positioning

| 能力 | 状态 | 源码/测试依据 |
|---|---|---|
| 单实例只读诊断，不执行人工处置 | IMPLEMENTED | `docs/PROJECT_RULES.md`；`dba/core.py::ReferenceCommand`；`tests/test_slow.py::test_analyzer_dispatch_and_unsupported`、`tests/test_incident.py::test_recovery_action_is_text_only_and_json_is_redacted` |
| Slow / Disk-Growth 作为诊断场景 | IMPLEMENTED | `dba/cli.py` 的 `diagnose` 参数与分派；`dba/slow.py::diagnose_slow`、`dba/disk_growth.py::diagnose_disk_growth` |
| 紧急响应优先、先输出再深挖 | PARTIAL | 诊断结果有 summary/model，但 CLI 在 `dba/cli.py` 约 1549 行起先构造并写 HTML，随后才打印；没有阶段输出 |
| 一次性现场、无历史数据库/后台服务 | IMPLEMENTED | 报告在 CLI 本地写入；`docs/PROJECT_RULES.md` 明确禁止历史库与监控服务；未发现历史存储组件 |

定位应继续是“固定现场、提供证据和人工核查方向”，而不是自动恢复、自动优化或保证原子一致性的监控产品。

## 3. Existing Implementation Inventory

| 子系统/能力 | 状态 | 实际实现与边界 |
|---|---|---|
| Inventory 选择单实例与连接前检查 | IMPLEMENTED | `dba/cli.py::main` 读取 Inventory、Credential、Adapter；诊断使用当前实例，不扫描所有资产 |
| 版本检测 | IMPLEMENTED | `dba/versioning.py::detect_database_version` 经 Registry 执行固定 `version_detect`；要求单行且拒绝未识别/不匹配版本的静默回退 |
| 原子诊断 Profile | IMPLEMENTED | `dba/versioning.py::DiagnosticProfileResolver` 将 SQL、权限、parser 和版本路由绑定；`dba/slow_profiles.py::validate_slow_profiles`、`dba/disk_growth_profiles.py::validate_disk_profiles` 做一致性检查 |
| SQL Registry 与权限 | IMPLEMENTED | 正式 SQL 通过 `dba/registry.py::SqlScriptRegistry` 授权；Profile 带 permission profile，CLI 在诊断前评估权限并生成只读建议文本 |
| Slow 多库采集与分析 | IMPLEMENTED | `dba/slow.py`、`dba/mysql_slow.py`、`dba/postgresql_slow.py`、`dba/oracle_slow.py`；必需/可选角色见 `dba/slow_profiles.py` |
| Disk-Growth 多库采集与分析 | IMPLEMENTED | `dba/disk_growth.py::_collect_disk_growth` 按 Profile 顺序执行 Registry SQL；角色见 `dba/disk_growth_profiles.py` |
| 应急终端摘要 | PARTIAL | `dba/incident.py::build_incident_summary` 会找最大根阻塞并构造 impact/timing/source；`dba/presentation.py::render_incident_summary` 以英文展示且只列单个 root candidate |
| HTML/JSON | PARTIAL | HTML 是共享、自包含、转义、有界报告；栏目英文。JSON 是稳定英文键的结构化 CLI 输出，但没有与 HTML 配对写入的 JSON 文件能力 |
| 会话身份再验证 | MISSING | 查询可采集部分创建时间/序列号，但生成参考命令前没有第二次查询校验身份；也没有可执行前置校验机制 |
| TOP 3～5 跨类型应急候选 | MISSING | 没有统一的跨类别候选评分/排序器；SQL Top-N 只按运行时长 |
| 终端分阶段输出 | MISSING | CLI 单次最终输出，没有阶段/增量事件呈现 |

## 4. Slow Emergency Workflow

### 当前路径和采集顺序

CLI 在权限/版本验证后调用对应数据库的 `diagnose_slow`。SQL Server 依次执行 `ActiveRequestsCollector`、`ActiveSessionsCollector`、`TransactionsCollector`、`ConnectionsCollector`；MySQL 为 sessions、requests、transactions、blocking；PostgreSQL 为 sessions、long-running、transactions、blocking；Oracle 为 sessions、requests、transactions、blocking、previous SQL。每个执行器通常串行使用诊断连接。SQL Server/MySQL/PostgreSQL 的必需采集失败会终止该诊断；Oracle 同样依其 Profile required contract。遇到失败后停止继续使用已关闭的连接，因此后续补充证据不会自动接续。

SQL Registry 证据 ID：

- SQL Server：`active_sessions`/`active_sessions_legacy`、`active_requests`、`transactions`、`connections`。
- MySQL：`active_sessions`/`active_sessions_legacy`、`active_requests`/`active_requests_legacy`、`transactions`、`blocking`/`blocking_legacy`。
- PostgreSQL：`active_sessions`、`long_running`、`transactions`、`blocking`。
- Oracle：`active_sessions`、`active_requests`、`transactions`、`blocking_basic`（能力允许时可路由更完整 `blocking`）、`previous_sql`。

依据：`dba/slow_profiles.py::SLOW_SQL_PROFILES`、`SLOW_REQUIRED_COLLECTORS`、`SLOW_OPTIONAL_COLLECTORS` 和各库 `diagnose_slow`。当前 required/optional 是完整诊断证据合同，不是“已输出应急初报/深度分析”的阶段合同。

### 根阻塞、会话与 SQL

| 数据库 | 根阻塞证据 | Idle/Open Transaction 证据 | 限制 |
|---|---|---|---|
| SQL Server | `dba/slow.py::blocking_graph` 基于 `active_requests.blocking_session_id` 建图；`descendants` 计算影响数；`build_slow_report` 从 active_sessions 关联根会话、requests、transactions、connections | `status=sleeping`、无 active request、`open_transaction_count>0` 且 `transactions` 返回符合状态的未结束 DMV 事务时产生 `IDLE_TRANSACTION_BLOCKER` | 当前 SQL/上一批只作上下文，源码明确声明不能证明是哪条语句获得锁；需要多个串行 DMV 采样，非原子 |
| MySQL | `blocking` SQL/性能视图输出等待关系，`blocking_graph` 计算 roots/children | `active_sessions` 同 `information_schema.innodb_trx` 按 connection id 关联；Sleep 只有 transaction row 证据时才列为 Idle Transaction | 缺少 last completed SQL；历史 SQL 明确 unavailable；线程/连接标识会复用 |
| PostgreSQL | `blocking`/sessions blockers，`blocking_graph` 建图 | `pg_stat_activity` 的 `xact_start` 与 `transactions` 中 PID/xact_start 作为事务证据，可区分 `idle in transaction` | `long_running` 等是独立串行读取；PID 会复用，需用 backend_start 再确认；权限可隐藏其他用户查询 |
| Oracle | `blocking_basic` 使用 V$SESSION blocker 字段，具备 lock capability 时增加锁证据；以 `(inst_id,sid)` 建图 | V$TRANSACTION 与 V$SESSION 按会话地址关联，要求 INACTIVE、事务存在且有 start time | RAC 全局可见性不完整时结论降级；previous SQL 可能因 cursor aged out/权限/查询失败缺失；V$SESSION 无可靠 client IP |

代码依据：上述 slow analyzer 的 `build_slow_report`、`blocking_graph`、`IDLE_TRANSACTION` finding；SQL 依据 `sql/<db>/active_sessions.sql`、`active_requests.sql`、`transactions.sql`、`blocking*.sql`。现有测试包括 `tests/test_slow.py::test_cycles_deep_chain_multiple_parents`、`test_idle_transaction_evidence_source_age_and_last_sql`、`test_idle_requires_unfinished_dmv_transaction`、`test_truncated_requests_do_not_claim_idle_or_offer_kill`，以及 `tests/test_slow_profiles.py::test_optional_evidence_failure_downgrades_visibility_not_required_success`。

## 5. Disk-Growth Emergency Workflow

`dba/disk_growth_profiles.py` 为每个版本绑定 required/optional Registry ID；`dba/disk_growth.py::_collect_disk_growth` 顺序采集，required 失败后停止。大多数驱动在单条 SQL 失败后会关闭连接，当前除 Oracle 外不会在 optional 失败后重连继续后面的查询。报告有 `required_evidence_complete`、`optional_evidence_status`、`data_visibility`、`not_applicable`、unavailable 列表。

当前来源覆盖（代码定义，不等于真实全版本验证）：

| 数据库 | 当前可见空间/保留证据 | 不应声称的能力 |
|---|---|---|
| SQL Server | `disk_files` 文件/数据与日志分布、大小/已用、增长配置；`disk_log` 或旧版 `disk_log_legacy` 的 log-used 与 `log_reuse_wait_desc`；`disk_tempdb`、`disk_temp_usage`；可选 `disk_log_backup`、`disk_ag`、`disk_transactions` | 当前未实现历史文件大小差分；`ACTIVE_TRANSACTION` 是保留原因，不等于已严格证明某个具体会话是唯一原因；TempDB 汇总未关联具体消费者 |
| PostgreSQL | `disk_database` 数据库大小和累计 temp counters；`disk_relations`；`disk_slots`/`disk_slots_17_plus` retained WAL estimate；`disk_archiver`、`disk_replicas`、`disk_transactions` | `WAL_NOTE` 明确服务器文件系统 `pg_wal` 大小无法从当前数据库协议获取；temp_bytes/temp_files 是累计量，不是当前 temp 占用或速率；inactive slot 不对应活动会话 |
| MySQL | `disk_schemas`、`disk_tables`、`disk_tablespaces`；`disk_binlog_config`（5.7 走 legacy）、`disk_binlogs` 文件列表/大小、`disk_replica`、`disk_innodb`、`disk_transactions` | Binlog 合计是当前清单总量，非增长速率；`relay_log_size` 明确 unavailable；表空间 DATA_FREE 不证明操作系统空间可回收；无临时表消费者 Collector |
| Oracle | `disk_tablespaces`、`disk_files`、`disk_temp`；`disk_fra`/`disk_fra_usage`、`disk_archive`、`disk_archive_volume`、`disk_redo`、`disk_transactions` | Archive 24 小时 bytes/switches 是时间窗统计而不是会话级归因；FRA/表空间高占用不能证明最近增速或唯一来源 |

依据：`dba/disk_growth_profiles.py::DISK_GROWTH_SQL_PROFILES`；SQL Registry 文件名与 `dba/disk_growth.py::build_disk_report` 各数据库分支。`HISTORY_NOTE` 明确“Current-state diagnosis only; growth rate unavailable without historical snapshot”；`tests/test_disk_growth.py` 的 `test_sqlserver_log_reuse_and_transaction`、`test_postgresql_slot_archiver_and_unknown_wal_size`、`test_mysql_binlog_total_requires_complete_file_listing`、`test_oracle_archive_volume_and_sqlserver_tempdb_findings` 验证了这些语义。

## 6. Current Evidence Coverage

| 证据 | Slow | Disk-Growth | 状态/限制 |
|---|---|---|---|
| 活动会话/来源账号、主机、应用 | 四库会话 SQL 采集；HTML 有 Session Identity 表 | 不属于 Disk 核心采集合同 | Slow IMPLEMENTED；诊断和来源串行时刻不同 |
| 当前活动 SQL/请求 | SQL Server/MySQL/Oracle 明确请求 SQL；PG session query/long-running | 不调用 Slow collector | Slow IMPLEMENTED/PARTIAL；SQL 有长度上限/权限可见性边界 |
| 阻塞关系/根节点/受影响数 | 四库各自图算法 | 不直接收集 blocker graph | Slow IMPLEMENTED；截断/循环/特殊 blocker 会降级 |
| 长事务和空闲事务 | 四库不同证据路径 | 各库 `disk_transactions` 可选 | Slow IMPLEMENTED/PARTIAL；不同数据库语义/字段不等价 |
| 空间/日志/归档/WAL/Temp | 不适用 | 有当前状态和可选来源查询 | Disk IMPLEMENTED/PARTIAL；多项是总量或累计值，不是速率 |
| 单个采样开始/结束时间 | Slow `samples` 保留 CollectorResult started_at/finished_at | Disk `samples` 当前仅 source、row_count、success、truncated、error_type | Slow IMPLEMENTED；Disk PARTIAL |
| 整体 capture start/end、原子快照标识 | 无 | 无 | MISSING；必须明确 Best-Effort Snapshot |
| Profile/version/权限可见性 | CLI JSON 含 resolved profile/permission readiness；Slow details 有 version/quality | Disk details 含 version/profile/evidence status | PARTIAL；HTML metadata 只呈现版本/场景等通用上下文，缺少完整 capture window/profile summary |

## 7. Root Blocker and Transaction Analysis

Slow 已能给出一条最主要 root candidate：`build_incident_summary` 按 `blocked_count`/`blocked_session_count` 取阻塞影响最大的根会话，并整理登录、host、IP、program、事务年龄。SQL Server `build_slow_report` 通过 requests 的阻塞边构图；Oracle 还用会话状态和事务视图识别 INACTIVE 事务；MySQL 与 PostgreSQL 也有事务佐证的 idle 分支。

它没有实现所要求的 TOP 3～5 统一核查对象排序。数据库 analyzer 的 `top_requests`/`top_queries` 是按耗时排序的 SQL；`roots` 可多条，但终端仅显示最大 root；Idle Transaction findings 与非阻塞长 SQL 没有共同排序和“入选原因”字段。因此，当前回答“最值得先看的会话”是有一个根阻塞候选，但不是多因素、可审计的候选队列。

不要用最长 SQL 替代根阻塞：当前 `incident.py` 的 primary 排序优先 BLOCKING，其后才是 LONG_RUNNING_SQL；这点方向正确。需要保留不同类型 finding 的证据及来源，不应把 blocked waiter 当 root。

## 8. Disk Retention and Growth Attribution

当前报告可以关联部分事务上下文，但不能普遍证明“该会话导致磁盘增长”：

| 因素 | 可关联到会话/事务？ | 审计判断 |
|---|---|---|
| SQL Server `log_reuse_wait_desc=ACTIVE_TRANSACTION` | 可从独立 `disk_transactions` 获得 session_id/transaction_id/begin time 等；Disk finding 当前只突出 transaction id/age，未将 host/program/current SQL 和 root cause 建立严格归因关系 | 可定位调查对象，不能仅凭两个相邻快照证明某事务是唯一 log reuse owner |
| PostgreSQL inactive replication slot/WAL estimate | 主要关联 slot_name/type/active/retained estimate；可另有长事务 PID/query | slot 不是会话；inactive slot 常无活动 owner，当前不能连成“特定 SQL 导致 retained WAL” |
| MySQL binlog 当前总量 | transactions 有 trx/thread/user/host/query 字段，binlog 清单是另一查询 | 不能证明某事务产生了某段 binlog 或当前增长速率；relay log size 明确 unavailable |
| Oracle FRA、archive、redo、tablespace、TEMP | transaction query 可带 SID/SERIAL#/SQL_ID；archive/FRA 采集另源 | 可提供相关事务候选，不能证明它造成 FRA/archive 的特定占用；TEMP 使用仅汇总 |

`dba/disk_growth.py::build_disk_report` 的日志 finding 大多直接使用资源/保留 SQL 结果，long transaction finding 有时只保留事务 ID/PID/年龄；不与 Slow 的当前会话/阻塞 Profile 联合采集。报告应明确“关联线索/候选，不是因果证明”。不要生成 Disk-Growth 终止候选。

## 9. Triage Candidate Ranking

建议只增加轻量呈现排序，而非规则引擎。候选顺序：

1. **确认根阻塞**：证据完整的 root，按受影响会话数、阻塞等待时长排序。
2. **确认的 Idle/Open Transaction**：有关联事务 ID/start/age；受阻会话多者优先。
3. **长时间等待/请求**：按时长、等待状态展示，但标为被阻塞者/请求，不自动升级为根因。
4. **非阻塞长 SQL**：仅作核查候选；不提供会话终止命令。

每一项包含 identity tuple、事务/SQL 身份、user/host/application、状态、采集时间、影响值、入选原因、数据完整度；缺值用稳定 `N/A`/JSON null，而非猜测。排名要体现证据质量，遇到 incomplete/truncated 时弱化结论并禁止参考命令。当前只可显示最大 root 和耗时 SQL 列表，TOP 3～5 及解释字段为 MISSING。

## 10. Safe Manual Intervention

项目安全边界已阻止 Toolkit 执行 KILL/terminate/cancel。`dba/core.py::ReferenceCommand` 只有文本；各 analyzer 在有根节点、影响会话、完整图等条件下添加参考命令；`incident.py` 在证据不完整时不把命令放入 recovery actions；`presentation.py` 显示人工审核和 Toolkit 未执行。`tests/test_slow.py::test_truncated_requests_do_not_claim_idle_or_offer_kill`、`tests/test_incident.py::test_incomplete_evidence_never_surfaces_termination_command` 有直接回归证据。

现有命令的限制：SQL Server `KILL <session_id>`；PostgreSQL `pg_cancel_backend(pid)`/`pg_terminate_backend(pid)`；MySQL `KILL QUERY id`/`KILL id`；Oracle `ALTER SYSTEM KILL SESSION 'sid,serial#[, @inst_id]'`（仍是纯文本）。Oracle SID+SERIAL#(+INST_ID) 比单数字标识更强，但仍可能在采样后变化；其他三库命令尤其依赖单个复用型 ID。命令不包含可原子验证身份的条件，也不会在执行瞬间重新查询。故**适合作为人工核查参考，不应标成“可直接安全执行”或“身份已实时确认”**。

所有终止类参考均应继续附：目标身份、完整事务/回滚未知性、受影响数、来源应用、采样时刻、再次核对步骤、`Toolkit Executed: NO`。终止可能触发 rollback、额外 I/O、日志/undo 占用及应用错误；`rollback_duration` 当前固定 UNKNOWN，不能承诺立即恢复。Disk-Growth 应只建议联系应用/DBA查明保留因素，不因大事务而直接建议 KILL。

## 11. Session Identity Verification

| 数据库 | 当前可获取身份线索 | 当前命令是否绑定/复核该身份 | 复核建议 |
|---|---|---|---|
| SQL Server | `active_sessions` 有 `session_id`、login_name、host、program、`login_time`；事务有 transaction_id | 否。参考文本只有 `KILL sid`；`login_time` 未用于实时二次确认 | 终止前重新查询 session_id 并核对 login_time、login/host/program、当前请求及事务 ID/start；任一不符则放弃旧命令 |
| PostgreSQL | `pg_stat_activity` 有 pid、`backend_start`、`xact_start`、user/app/address | 否。参考文本只用 pid | 再查询 pid + backend_start + xact_start + user/db/app；匹配后由 DBA 决定是否手工执行 |
| MySQL | connection/processlist id、thread_id、user/host/db、事务 trx id/start | 否。参考文本只用 connection id | 再查 id + user/host/db + trx_id/start；Sleep/无事务变化时禁止旧候选直接执行 |
| Oracle | SID、SERIAL#、INST_ID、username、machine/program、SQL_ID、transaction start | 命令使用 SID+SERIAL#，RAC 可含实例号，但没有刷新验证 | 再查询 SID+SERIAL#+INST_ID+username+transaction/SQL_ID；变化或 RAC 可见性不完整则不使用旧参考 |

SQL Server/PID/session ID 复用是**真实设计风险**；当前测试没有模拟采集期间会话结束及新会话复用 ID。可在下一步增加人工步骤/核对 SQL 建议；若实现工具内二次查询，须独立只读、仍无自动执行并要求完整身份 tuple 匹配。

## 12. Capture Timing and Consistency

Slow `samples` 记录各 Collector 的 started_at、finished_at、elapsed/row count/truncated/success/error_type（依各 analyzer 构造）；Disk `samples` 未记录采集时间。所有 Collector 是顺序 SQL，不是跨 DMV/视图同一事务快照。SQL Server quality 文案明确 sequential DMV samples non-atomic；Oracle RAC 另标 global snapshot 不可用；数据库间的对象创建/结束和会话 ID 复用都可能造成错配。

P0 应把 `capture_started_at`（首条应急查询前）、`capture_finished_at`（最后一条采集结束）和 `snapshot_consistency=BEST_EFFORT` 放进 JSON/report metadata；每个 evidence group 继续带自己的时间。Disk 沿用 CollectorResult 原有时间字段，不另建历史库。不要声称跨证据源原子快照。

## 13. CLI UX

当前 `dba diagnose <database_id> slow|disk-growth` 兼容且拥有一次性 JSON 输出；问题是同步命令等分析和 HTML 完成后才显示终端结果。具体在 `dba/cli.py` 诊断分派后构造 incident/model、`render_bounded`、`report_path.open`，随后才调用 `render_incident_summary`。

建议保持命令名/场景不变，默认成为 triage 流程，并提供可选 `--mode full` 保留/明确旧的全量行为：

- **默认 triage**：安全预检、版本/Profile 检验后，采集少量应急核心 SQL（会话、活动请求/阻塞边、事务核验所需的关键行）；立即渲染中文初报并 `flush`；随后继续尚未重复的补充 SQL 和 HTML。
- **`--mode full`**：先执行同一 triage 序列并立即输出初报，再采集该 Profile 的其余可选证据，最后保存 HTML。可先实现只对 console 默认分阶段；JSON 维持单个完整文档输出，避免 JSONL/多文档破坏调用方。
- 两种模式共用一个 `ResolvedDiagnosticProfile`、权限 assessment 和一次连接。CollectorResult 被保留并传入一个分析结果；已运行的 SQL 不重复。Profile 的 required/optional 意义应保持 completeness 契约；可在现有 Profile binding/SQL roles 上增加阶段标识，不建立第二套版本/Profile 注册表。
- 若应急核心缺失，输出“证据不足/无法判定”，不要把 UNKNOWN 渲染成“无问题”；可选查询失败不应抹掉已打印初报，最终标为 PARTIAL 并保留 HTML 报告路径。

不建议先做异步并行；同步串行但先输出首批证据，已足以解决用户等待完全报告的问题。

## 14. Chinese HTML Report

现有 HTML 已有可复用架构：`dba/reporting/model.py::ReportModel`、共享 `build_report_model`、`HtmlReportRenderer`、内嵌 CSS、`html.escape` 和 `redact`；`HumanReportPolicy` 为行数/SQL/字节数设预算并说明省略；`tests/test_html_reports.py` 覆盖注入转义、固定列、预算和保留关键 section。HTML 是 UTF-8、自包含、离线可打开，原始 SQL/数据库值保留。

中文化状态为 **PARTIAL/MISSING**：`report.html` 是 `<html lang="en">`；`HtmlReportRenderer.render` 的 Incident Summary、Status、Primary Problem、Observed Now、Data Completeness 等为英文；`dba/reporting/presenters.py` 的 section/table 标题如 “Root Cause Candidate”、“Session Identity”、“Blocking Analysis”、“Transaction Analysis”、“Database Space”、“Transaction Log”、“Diagnostic Context”、“Findings”、“Collection Quality”均是英文。`dba/presentation.py::render_incident_summary` 也使用英文 label。动态 finding title、severity、reason 部分中英文混合。

建议统一改共享 Presentation 层映射和模板语言为简体中文，技术原值（SQL、SQL_ID、Wait Event、视图名、错误码）保持原文，可加中文解释；JSON key、SQL Registry ID、parser 字段、内部 enum 不翻译。栏目围绕 slow 与 disk-growth 分别选择，避免空洞或无关栏目。HTML 首页只放状态/影响/头部候选/完整性/人工核查建议，详细 sessions、SQL 和事务展开在后续章节。

## 15. JSON Evidence Contract

JSON 使用英文结构字段，CLI 对 diagnose 返回 report、incident_summary、report_path、version_profile/permission_readiness 等结构。`dba/incident.py::IncidentSummary` 保持结构化数据；`--json` 不经过人类展示模板，因此可作为稳定自动化接口。`tests/test_slow.py::test_cli_diagnose`、`tests/test_disk_growth.py::test_cli_oracle_disk_growth` 覆盖 JSON CLI 流程。

JSON 可通过 shell/调用方捕获以事后查看，但当前没有和 HTML 同时写出 JSON artifact 的产品接口；HTML 默认写入本地报告目录。建议不擅自加入多文档流式 JSON：初报/最终状态可在 console 分阶段显示，最终 `--json` 一次输出完整 evidence，并以向后兼容方式添加 `capture_window`、`evidence_completeness` 等字段。保存 JSON 文件若未来增加，应明确 redaction、权限和敏感文件忽略策略。

## 16. Permission and Version-Aware Integration

`dba/versioning.py::CANONICAL_VERSION_FAMILIES` 定义 SQL Server 2008 R2/2012/2014/2016/2017/2019/2022/2025，MySQL 5.7/8.0/8.4，PostgreSQL 13–18，Oracle 11gR2/12c/18c/19c/21c/23ai/26ai。Slow 与 Disk 当前 Profile 路由覆盖：

| 数据库 | Slow/Disk 源码路由状态 | 注意 |
|---|---|---|
| SQL Server | 2008 R2、2012、2014–2019、2022 有 reviewed routes；2025 为 UNKNOWN | 2008 R2/早期版本采用 legacy SQL；2022 权限合同分支；compatibility target 不等于全版本真实验证 |
| MySQL | 5.7、8.0（`pre_8022`/`8022_plus`）、8.4 | 旧版语句与锁视图按变体路由 |
| PostgreSQL | 13–18；17+ slot SQL 独立 | 查询权限与统计可见性必须保留为 readiness/visibility |
| Oracle | 11gR2、12c 12.1/12.2+、18c、19c、21c、23ai、26ai | optional V$ 视图缺失时可降级；RAC 全局快照不完整不可给强结论 |

这表示**代码中有 reviewed Profile**，不是每个版本的实机兼容证明。`tests/test_versioning.py::test_profile_mismatch_unknown_and_unsupported_never_fallback`、`tests/test_slow_profiles.py::test_sqlserver_2025_is_unknown_and_auto_detected_permission_is_unknown`、`tests/test_disk_growth.py::test_disk_growth_mismatch_and_unknown_fail_closed` 覆盖 fail-closed 路由。诊断前 CLI 进行版本/Profile/权限检查；不应为速度绕过 common preflight、Credential、权限或版本验证，也不扩大为管理员账号。

## 17. Performance and Resource Budget

没有仓库证据表明已系统测量四库的 triage latency、完整采集时长和 HTML 构建时长；不能宣称固定 5 秒。`HumanReportPolicy` 对 HTML 有行/字符/byte 上限；SQL Registry 有单条 timeout/max_rows，但整条诊断流程没有端到端墙钟预算。

建议在本地四库安全环境按诊断类型记录 `T0` 命令开始、`T1` 首批核心结果打印、`T2` 最后 Collector 结束、`T3` HTML 写入完成。每个版本/profile 重复多轮，分别报 P50/P95、失败率、连接/SQL 数、truncated 比例；不记录 SQL/凭据。性能门槛应是相对当前基线改善和核心 evidence 完整度，不是鼓励跳过安全检查。同步复用单连接与已有结果，不重复采集、不并发压库。

## 18. Missing Evidence Semantics

现有组件已有部分语义：Disk 有 `not_applicable`（如 binlog disabled、PG 无 slots、Oracle 无 FRA/noarchivelog）、`UNAVAILABLE`、required/optional missing、`data_visibility`；Slow 有 collector success/truncated、incomplete/partial、Graph anomalies；版本 Profile 有 UNKNOWN/UNSUPPORTED。仍缺一个一致的证据状态枚举覆盖 `NOT_SUPPORTED`、`NOT_CONFIGURED`、`PERMISSION_MISSING`、`QUERY_FAILED`、`QUERY_EMPTY`、`NOT_OBSERVED`、`UNKNOWN`。

建议在展示层映射，不改原始 JSON status 语义：成功零行是“本次查询未观察到行/可能未配置”，仅在 SQL 语义能够证实时标成 NOT_CONFIGURED；失败不能翻译成空结果；权限缺失保留 privilege/impact/suggested grant；unsupported 与 unknown 分开。无活动请求只有在 required 查询成功且未截断时才能说“本次未观察到”，不能推导不存在历史/潜在问题。

## 19. Test Gap Analysis

已有测试并非空白：`tests/test_slow.py` 覆盖 graph/cycle/special blockers、sleep transaction、事务状态验证、可选失败、truncated 不给 KILL、敏感值 redaction；`tests/test_incident.py` 覆盖 root summary、incomplete 不展示命令、纯长 SQL无终止建议；`tests/test_disk_growth.py` 覆盖版本错配、 required/optional、zero transaction、not configured、各库存储 findings；`tests/test_html_reports.py` 覆盖 HTML escape、预算和高会话数。

尚未发现以下明确用例（本轮未修改测试）：

| 场景 | 当前覆盖判断 | 缺口 |
|---|---|---|
| root 是 Sleeping 且被多个会话阻塞 | PARTIAL | SQL Server/MySQL/Oracle 有相关 idle/transaction 用例，但不足以证明四库统一输出排序、来源与事务 tuple |
| 长事务无当前 SQL | PARTIAL | 有 Idle Transaction/transaction rows；缺少四库统一 expected `N/A` 和候选排序断言 |
| 多根阻塞/多条链候选 TOP 5 | PARTIAL | 图算法测 multiple parents/deep chain；无“多个 root + idle tx + 长 SQL”的排序验收 |
| 环状/异常 blocker | IMPLEMENTED | `test_cycles_deep_chain_multiple_parents`、`test_special_or_invalid_blocker` |
| session ID/PID 复用 | MISSING | 无身份 tuple 变化、旧 command 抑制/再验证测试 |
| 采集期间会话结束/新会话复用 | MISSING | 缺 fake sequence 证明 join 不会误配/不得提供旧引用 |
| Required/Optional 查询失败 | IMPLEMENTED/PARTIAL | Slow 有 required/optional 语义；Disk 有 `test_missing_source_stops_after_failure`、`test_optional_collector_failure_keeps_core_diagnosis_complete`；缺分阶段输出下的阶段结果保持 |
| 成功零行与失败区分 | PARTIAL | Disk 有 PG no slots、Oracle FRA empty、Oracle zero transactions；缺统一所有 Collector 的 status mapping |
| 版本不匹配/未知 | IMPLEMENTED | `test_profile_mismatch_unknown_and_unsupported_never_fallback`、disk mismatch tests |
| 权限不足影响和继续不相关采集 | PARTIAL | 有权限 registry/preflight 测试；当前必需失败会退出、optional 失败多会停止后续，所以不保证继续收集其他独立项 |
| 输出脱敏 | IMPLEMENTED | `test_partial_snapshot_and_sensitive_report`、Oracle disk report redaction、HTML escaping tests |
| ReferenceCommand 仅展示且 incomplete 隐藏 | IMPLEMENTED | `test_recovery_action_is_text_only_and_json_is_redacted`、`test_incomplete_evidence_never_surfaces_termination_command` |
| Disk-Growth 无法归因具体 session | PARTIAL | history/unavailable semantics 有测试；缺明确断言不基于 disk finding 产生/暗示 KILL 候选 |
| triage 在 full query/HTML 前显示 | MISSING | 无时间顺序或输出阶段的 CLI 集成测试 |
| 全中文 HTML/console 和 native values 保留 | MISSING | 当前 HTML 测试断言英文栏位，未验收简体中文/UTF-8 和 JSON key 不变 |
| Best-effort capture window 与时延分解 | MISSING | 无命令阶段 latency 指标/报告窗口测试 |

## 20. Design Alternatives

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| A. 保持统一全量采集后输出 | 改动小；现有 `DiagnosticReport`/报告路径不变 | 无法满足先交付根阻塞/高占用线索，慢的可选 SQL/HTML 阻塞初报 | 作为 `--mode full` 的兼容路径可保留，不作为默认应急 UX |
| B. 同步分阶段：核心采集→输出摘要→补充采集→HTML | 符合应急目标；复用 resolver、连接、CollectorResult、Presenter；易观测和测试 | 需要拆分 orchestration、初报/最终呈现状态，重新设计 evidence stage contract | **推荐** |
| C. 并发/异步采集 | 理论上多 SQL 延迟可重叠 | 驱动连接/超时/关闭语义、负载峰值、异常聚合和证据时间不一致更复杂 | P0 不采用 |

## 21. Proposed Minimal Implementation

建议按顺序实施，修改面控制在约 6–9 个生产模块加测试/文档；如现有 Profile 类型需表达阶段，尽量扩展现有 Profile binding/slot metadata，而非建平行框架。

1. **一个 orchestration 入口**：让 CLI 分派得到已解析 Profile 后按 stage 运行 Collector；保留现有 Adapter/Registry 安全授权、timeout、max_rows 和 preflight。
2. **核心证据先行**：Slow 每库至少包含会话 + 活动请求/阻塞边；要宣称 Idle Transaction，必须将事务查询纳入应急 evidence contract。Disk 先采 required 核心空间与主要 log/WAL/FRA/binlog retention query；对必须关系到应急判断的交易/事务证据明确标成 core 或未采，不要以 unavailable 值冒充完整。
3. **Presenter 分两次渲染同一 domain result**：先输出短中文 triage（root、受影响数、最长已确认事务、top 请求或主要空间/保留线索、完整性/下一步）；补采完成后更新最终摘要和报告路径。
4. **保持已采数据**：full stage 只运行未执行的 slot，不重复 profile SQL；沿用单连接/串行。失败后只在 Adapter 明确支持安全 reconnect 时续采；否则就把剩余 slots 标 unavailable。
5. **身份/命令安全**：在最终结果标注 snapshot 时刻和“人工重新验证身份”；基于前述身份 tuple 定义复查说明。P0 先不把旧命令宣称为可直接执行；对不完整 evidence 继续抑制 command。
6. **统一中文 Presentation 映射**：改 label/summary/template，不改 domain finding IDs、JSON keys、SQL IDs/native data。
7. **有界报告与 JSON 不变**：继续用现有 ReportModel/HumanReportPolicy。Console 允许阶段输出；JSON 只输出一次最终对象。HTML 最后生成，报告说明 total/displayed/omitted 和 evidence quality。

新增 SQL 应尽量少：首先评估现有查询输出是否能形成 identity tuple 和 blocker impact。确有缺口时才为版本 Profile 增加最小只读 identity refresh SQL（应在人工处置说明中使用，不做自动 kill 前查询）。优先补采集时间、版本/权限可见性、现有事务关联和 `temp consumer` 这种直接回答事故问题的证据；暂不加 QPS/TPS、历史采样、完整 plan、全库大表明细或磁盘文件系统探测工具。

## 22. Acceptance Criteria

- 默认 console 命令在核心采集结束后、任何可选采集或 HTML 构建前打印简体中文初报，并明确时间、数据库版本/Profile、风险、影响数、首要核查对象和完整度。
- 初报不因 optional SQL、HTML renderer、磁盘写入等待；失败应返回已有初报并补充明确失败/报告未生成状态。
- core evidence 失败时显示 UNKNOWN/INCOMPLETE，不显示健康或“没有阻塞”。Required/Optional/permission/unsupported/not-configured 不混为一个状态。
- Slow 优先识别 root/confirmed Idle Transaction，再列非 root 长 SQL；提供最多 3–5 个候选和 reason，禁止以 SQL duration 独立等价为根因。
- Disk 分类至少显示已有实现可证实的 TRANSACTION_LOG、WAL、BINLOG、REDO/ARCHIVE/FRA、UNDO、TEMP、DATAFILE/TABLE_INDEX；明确“current-state vs growth rate”和可/不可会话归因。
- Session ID reuse 风险及人工复核身份 tuple 对每个数据库说明。任何采样不完整、身份缺失或版本不支持时不呈现可直接执行的终止命令。
- HTML 为 UTF-8、自包含、中文栏目、转义、安全 redaction、有界且披露遗漏；native SQL/SQL_ID/Wait Event/error code 原样保留。
- JSON schema 的既有英文 keys 保持；新增窗口/阶段字段为向后兼容；stdout 不是多个拼接 JSON 对象。
- 同一 Profile/resolver/权限/SQL Registry 路径；无任意 SQL、GRANT、KILL、DDL/DML、并发框架或历史库。
- 测试初报早于 optional collection/HTML，证明不会重复查询，覆盖零行/失败/截断/权限缺失/身份复用/四库 stage ordering、中文和 JSON 稳定性。
- 在本地安全四库环境分别测 T0–T3；报告 P50/P95 和完整性，不固定生产秒数门槛。

## 23. Risks and Blockers

**实现前置阻塞：无。** 可以在现有只读架构内开始同步分阶段设计。开工前实现设计需定下默认 triage 的具体 required evidence contract，以及哪些查询属于核心还是补充；这属于实现决策，不需要 DBA 授权。

主要风险：

1. 把“核心 SQL 成功”误称“诊断完整”；阶段 completeness 必须和完整 Profile completeness 分开表达，但不能新建互相冲突的 Profile 系统。
2. Slow 与 Disk 目前多为非原子串行采样；初报更早意味着证据窗口更短，报告必须标明 best effort 和各 Collector 时间。
3. 连接在 query failure 后可能关闭，optional 续采可能需要 adapter-specific reconnect；首版可安全停止并显示未采项，避免复杂恢复逻辑。
4. 查询/会话文本敏感且 HTML 作为本地文件保存；沿用 redaction、escaped rendering、文件忽略和预算策略。
5. 终止参考语句仍存在 ID 复用的人工操作风险。没有实时 revalidation 的历史命令不应叫 safe-to-execute；较强策略需增加数据库验证步骤，但永远不自动执行。
6. SQL Server 2025 在当前 Slow/Disk routes 为 UNKNOWN；不能为应急 UX 套用 SQL Server 2022 Profile。其余 version route 的测试是 mock/static，不能据此宣称所有版本实机兼容。

## 24. Final Recommendation

**APPROVE_WITH_CHANGES** — 批准进入 P0 实施，范围限定为同步分阶段采集/初报、Slow 根因候选呈现、Disk 证据分类与归因边界、身份复核提示、简体中文 console/HTML、capture/completeness 字段和针对性测试。默认初报必须以证据完整度约束结论；全量 HTML 留在第二阶段。异步采集、历史平台、QPS/TPS、自动终止、完整计划采集和自动优化延期。

**Can implementation start: YES。** 当前没有必须 DBA 人工授权、系统安装或外部环境状态才能开始的阻塞项。实现仍须保持所有诊断 SQL reviewed/read-only、权限和版本 fail-closed，不得通过管理员权限或自动数据库变更换取速度。

## 三十五. 必须回答的问题

1. **现有 Slow 能不能快速找出根阻塞？** 能识别；`blocking_graph`、受影响 descendants 和 `build_incident_summary` 最大 root 有源码与测试依据。但输出前要等全部已安排的串行 Collector 和 HTML；终端只列一个 root。
2. **能不能找到 Sleeping/Idle 状态下持有事务的会话？** 能，在 SQL Server/MySQL/Oracle 有事务视图关联（PG 的 idle-in-transaction 亦检查 `xact_start`）；不会只凭 idle/sleep 状态判定。证据缺失则不应当作已确认。
3. **能不能找到当前最值得 DBA 核查的 3～5 个会话？** 不能完整做到。当前最多挑最大影响 root，SQL top-N 按耗时；没有 root/idle transaction/blocked wait/long SQL 的统一 TOP 3～5 排名及理由。
4. **终端是不是必须等所有查询执行完成才显示结果？** 是。当前先采集/分析/构建并写 HTML，再调用 `render_incident_summary`。
5. **当前 Disk-Growth 能识别哪些空间来源？** SQL Server data/log/tempdb/log reuse/backup/AG；PostgreSQL database/relation/WAL slot/archive/replica/transactions/temp 累计计数；MySQL schema/table/tablespace/binlog/replica/innodb/transactions；Oracle tablespace/datafile/temp/FRA/archive/redo/transactions。
6. **哪些因素能够关联会话/事务？** SQL Server active transaction 可拿到 session/transaction ID；PG 长事务有 PID/query/start；MySQL InnoDB transaction 带 connection/user/host/query；Oracle transaction 带 SID/SERIAL#/SQL_ID。它们是独立采样的关联线索，不等于因果证明。
7. **哪些无法关联，为什么？** PG inactive slot、binlog/WAL/FRA/archive/redo总量、TempDB/TEMP 聚合和 database/table size 通常没有拥有者 ID；磁盘分析也不调用统一 Slow 阻塞/会话关联。无法归因是数据源语义和当前模块隔离所致。
8. **现有人工终止命令是否可靠？** 仅作为受限人工参考；不自动执行且不完整图时会抑制。它们未绑定二次身份检查，因此不能保证采样后仍指向同一会话。
9. **是否存在会话 ID 复用风险？** 是。SQL Server/MySQL/PG 的数字 ID 会复用；Oracle SID 也可能复用，SID+SERIAL#+INST_ID 较强但仍需重新确认。当前没有工具内复核。
10. **当前报告哪些内容需要中文化？** Console 和 HTML 的摘要、状态、严重性解释、章节/列标题、采集质量、缺失证据、处置说明、大小裁剪提示，以及 `lang="zh-CN"`。SQL、SQL_ID、Wait Event、错误码、视图名和 JSON keys 保留原文/英文。
11. **JSON 和 HTML 是否能够用于事后查看？** HTML 本地落盘、自包含、离线可读，且有界；JSON 可从 CLI 捕获并保留英文结构化字段，但当前没有自动保存配对 JSON 文件。
12. **最小修改多少代码才能达到 P0？** 约 6–9 个生产模块加测试和文档：CLI/orchestrator、现有 profile/slot metadata、共享终端 presenter、report presenter/renderer、capture/completeness 模型、少数必要 SQL。优先复用 Collector/ReportModel，具体文件数在实现勘察时校准，不新增服务/框架。
13. **哪些额外采集 SQL 确实值得增加？** 先检查现有字段；优先补齐用来复核 identity tuple 的只读查询/字段、缺少的 collector timing。如果 TEMP 消费者无法由现有证据得到，可补受版本路由、低成本且有权限契约的 session-to-temp 使用明细。避免重复抓取已有 SQL、plan XML、历史速率或无关大明细。
14. **哪些功能应该明确延期？** 异步并发、后台/历史采样、QPS/TPS 趋势、自动 TOP 3 execution plan、自动 KILL/取消、扩权/自动修复、Web UI 和跨采样归因引擎。
15. **如何证明应急输出确实更快？** 为本地安全实例记录 T0 命令启动、T1 初报打印、T2 最后一条 Collector 完成、T3 HTML 写完；比较当前与分阶段实现的 P50/P95 T1、完整耗时、核心证据完整率、truncated/失败率，并用测试断言初报发生在 optional 查询和 HTML 之前、且同一 SQL 不重复运行。
