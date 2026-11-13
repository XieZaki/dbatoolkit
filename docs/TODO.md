# DBA Toolkit TODO
## 当前待办与行动入口

> 更新日期：2026-11-12
>
> **状态权威来源：** [V0.9.1 Backlog](roadmap/DBA_TOOLKIT_V0.9.1_BACKLOG.md)。本文件是面向日常工作的精简待办视图，不创建第二套任务状态。若本文件与 Backlog 不一致，以 Backlog 为准，并在下次维护时同步。
>
> **当前开发指针：** [Progress](roadmap/DBA_TOOLKIT_V0.9.1_PROGRESS.md)。
> **验收证据：** [Acceptance](roadmap/DBA_TOOLKIT_V0.9.1_ACCEPTANCE.md)。

---

# 1. 当前优先事项

## 当前重点 — 继续完成 Phase 5 HTML Incident Workbench

**正式任务：** V091-P5-WORKBENCH-001
**Backlog 状态：** IN_PROGRESS
**当前阶段：** Phase 5

### 待办

- [ ] 依 Acceptance §75 在授权 Windows 宿主机执行 A 空闲基线、B 高并发、C 超过 10 秒的业务 SQL 与独立 WAITFOR、D 混合阻塞及回滚解除，并保留同次真实 JSON/HTML 和验证记录。Codex 管理进程在宿主机门禁处停止，实机场景仍为 `NOT_EXECUTED`。
- [ ] B 场景核对三类 SQL 原始模板与采集文本、语句级分组、累计 CPU/逻辑读和 RUNNABLE 数。实际 DMV 偏移及长文本边界仍待稳定构造并核验，32 KiB 与多一个码元已通过离线回归。

- [ ] 审阅七区块离线样例 `reports/slow_workbench_synthetic_sqlserver_20261110.html`，按 Acceptance §69 在获准 Windows 宿主机重采混合 WAITFOR 并复核 HTML 和 JSON。
- [ ] 审阅用户提供的最新 SQL Server 2022 Slow HTML 报告：sqlserver2022_slow_20261109_211354.html。检查结论是否清晰、证据是否完整、字段是否容易理解；不要将真实报告或其中的 SQL/业务信息提交到仓库。
- [ ] 对照 Acceptance §67，审阅 WAITFOR 分类离线证据，确认正常独立 WAITFOR、持有未提交事务、存在阻塞、混合异常和证据不足场景的结论边界。
- [ ] 用户审阅并另行授权后，在获准的 Windows 宿主机和已确认的 SQL Server 2022 测试资产上，复测 standalone WAITFOR 与混合请求/阻塞场景。若 Windows 宿主机 preflight 不通过，停止连接并提供用户可执行的 PowerShell 命令。
- [ ] 完成 SQL Server、PostgreSQL、MySQL、Oracle 四数据库真实报告生成。
- [ ] 对真实生成的 JSON 与 HTML 报告逐项核对结论、身份、SQL、请求/会话、事务、等待、阻塞、资源指标、错误和完整性状态的一致性。
- [ ] 将实际运行的数据库版本、资产、命令、结果、限制和清理情况写入 Acceptance；更新 Backlog 状态和 Progress 指针。
- [ ] 用户审核后再决定是否关闭 P5 或进入下一阶段。

### 已有证据与边界

- Acceptance §55：HTML inline interaction 离线回归。
- Acceptance §56：本地 Chrome 窄视口视觉验收。
- Acceptance §57：四数据库合成 JSON/HTML parity。
- Acceptance §66：SQL Server Slow session query 与数据库原始错误传播离线证据。
- Acceptance §67：SQL Server WAITFOR 分类离线证据；聚焦测试 200 passed，完整 pytest 1390 passed、3 skipped；Ruff、严格 MyPy、Registry validation、diff check 通过。
- WAITFOR 新分类的 Windows 本地真实 E2E 仍为 NOT_EXECUTED。
- 四数据库真实报告生成和真实报告 JSON/HTML parity 仍未完成。合成 fixture 不可替代真实报告证据。

---

# 2. 已完成但需保留边界的工作

以下任务不应重复开发；若发现新问题，使用新的 Bug ID 或任务 ID，并保留原任务范围。

| 任务 | Backlog 状态 | 备注 |
|---|---|---|
| V091-P2-TOPSQL-INTEGRATION-001 | DONE | 用户接受的范围见 Acceptance §63；不代表其他数据库/版本全部验证 |
| SQL-REGISTRY-SAFETY-001 | VERIFIED | 静态安全审计及自动化证据见 Acceptance §58；不等于真实数据库 E2E |
| BUG-WINDOWS-CI-FAILURES-001 | VERIFIED | Windows Actions 修复证据见 Acceptance §47、§60 |
| V091-P5-WORKBENCH-001 的离线 WAITFOR 分类 | 进行中任务中的离线检查点 | 不等于 P5 整体完成或真实 E2E 通过 |

---

# 3. 等待真实环境验收的事项

这些任务已有实现或包级验证，但仍有明确的真实环境验收缺口。不要把它们标记为已完成。

## PORTABLE-UPDATE-001 — E2E_PENDING

- [ ] 在符合要求的 Windows 环境完成正式跨版本 Update 验收。
- [ ] 核对 Full/Update 包、驱动替换、用户数据保留、失败回滚和哈希。
- [ ] 不覆盖真实 Inventory、Credential Store、报告和用户自定义驱动。
- [ ] 验收证据更新到 Acceptance，状态以 Backlog 为准。

## OFFLINE-RUNTIME-001 — E2E_PENDING

- [ ] 在真正未安装 Python 的 Windows 主机验证绿色便携版启动。
- [ ] 在物理断网环境验证离线运行和依赖完整性。
- [ ] 验证不访问互联网、不运行系统包管理器、不覆盖用户配置。
- [ ] 验收证据更新到 Acceptance，状态以 Backlog 为准。

---

# 4. 已登记的后续 Backlog

当前 P5 未完成前，不自动启动以下任务。具体依赖、进入条件和验收标准见 Backlog/Roadmap。

## Phase 2 / 性能根因分析

- [ ] V091-P2-RESOURCE-CONTEXT-CONTRACT-001 — BACKLOG：建立数据库/实例资源观察和证据分级 Finding 的共享契约；契约/fixture 范围，不含数据库 SQL；需要单独授权。
- [ ] V091-P2-TOPSQL-DELTA-001 — BACKLOG：为已验证的累计统计源增加可选、有界的双快照区间差值；不得变成后台 Agent、定时采样或默认双采样。
- [ ] 资源上下文契约通过并获得后续授权后，才逐个选择数据库资源源；不批量启动四库实现。
- [ ] 不因 Top SQL 集成 DONE 就推导所有资源归因或根因诊断已完成。

## Slow HTML 工作台中显示「待开发」的能力

2026-11-12 Slow HTML 按 V1 Demo 回归后，以下内容在报告对应位置显示为「待开发」；高并发诊断例外，报告目前只在数据库侧证据充分时给出简短提示。这些内容尚未建立 Backlog 任务 ID，也未获授权；如需实施，先在 Backlog 登记并取得用户授权。已有任务覆盖的内容只引用，不重复登记。

- [ ] 连续采样与 CPU/逻辑读/物理读区间增量：由 V091-P2-TOPSQL-DELTA-001 覆盖，不另起任务。
- [ ] 高并发无主导慢 SQL 的资源诊断：目标是在没有单一慢 SQL 时，依据数据库内部的并发与等待分布给出结论；依赖 V091-P2-RESOURCE-CONTEXT-CONTRACT-001 及区间增量，不在 HTML 内实现根因逻辑。
- [ ] Sleeping/非根会话的上一条 SQL：目标是让 SQL Server 非根会话（most_recent_sql_handle）、Oracle 非根会话（PREV_SQL_ID）和 MySQL（events_statements_history）提供可靠的上一条 SQL；依赖新增只读 Registry SQL 与字段契约。当前仅 SQL Server/Oracle 根阻塞者和 PostgreSQL 非 active 会话有可靠来源。
- [ ] 锁资源解析到库/表/索引，以及锁模式：SQL Server 部分由 V091-P5-LOCK-OBJECT-SQLSERVER-001 实现，状态以 Backlog 为准，真实 E2E 待执行。PostgreSQL（pg_locks）、MySQL（performance_schema.data_locks）、Oracle（V$LOCK）仍显示「待开发」，尚未登记任务，也未获授权。
- [ ] Slow 报告内嵌 TOP 5 当前 SQL 的真实执行计划：目标是在报告中展示与当前请求按原生标识关联的计划树；依赖现有独立 `plan` 命令的结果契约，不重新执行 SQL，不使用模拟计划。PostgreSQL/MySQL 当前无可关联的计划标识，标记为不支持。
- [ ] 执行计划运行时统计：目标是展示实际行数、执行次数等算子运行统计（如 SQL Server 实际计划/查询分析、Oracle ALLSTATS LAST）；依赖内嵌计划能力及额外只读权限评估。
- [ ] SQL Server 与 Oracle 客户端源端口：目标是采集 client_tcp_port / V$SESSION 端口信息；依赖 Slow Registry SQL 字段变更（当前约定不修改 Registry，需单独授权）。
- [ ] Oracle 与 MySQL 当前执行的 CPU/读取计数：Oracle 需单次执行级统计（V$SQL 子游标累计值不可替代），MySQL 需 events_statements_current CPU_TIME（8.0.28+）；依赖 Registry 字段契约和版本门槛。

**资源指标边界（2026-11-12 确认）。** Slow 报告只展示数据库内部资源证据，包括 CPU 调度等待、内存授予等待、I/O 等待和并发。主机 CPU、MEM、IO 不在 Toolkit 范围：不开发 Zabbix API、操作系统 Agent 或主机资源采集，报告中也不将其显示为待开发。数据库侧存在明确资源竞争证据时，报告提示 DBA 结合 Zabbix 或主机监控人工核实；证据不足时不给出资源瓶颈结论，也不建议扩容。

## 全局诊断 SQL 超时与模块故障隔离（SQL-TIMEOUT-001）

2026-11-12 完成规则落地与架构审查（只读代码审查，未连接数据库，未改 Collector/驱动）。规则见 AGENTS.md 与 PROJECT_RULES「全局诊断 SQL 执行超时与故障隔离原则」，设计与测试方案见 `docs/design/diagnostic-sql-timeout-and-fault-isolation-v0.9.1.md`。任务状态以 Backlog 为准；每项需单独授权，不随 P5 自动启动。

- [x] 用户已于 2026-11-13 批准 `C-11`～`C-13`（决定见 Backlog §6 与设计 §7）。
- [x] SQL-TIMEOUT-CONFIG-002：用户验收，见 Acceptance §84。
- [x] SQL-TIMEOUT-CLASSIFY-003：用户验收，见 Acceptance §85。
- [x] SQL-TIMEOUT-DEADLINE-004：用户接受 PARTIAL 交付，见 Acceptance §86 与 §87 基线。
- [ ] SQL-TIMEOUT-MYSQL-005：审阅 SELECT 会话超时交付和 SHOW 取消的身份竞态，见 Acceptance §87。
- [ ] SQL-TIMEOUT-ISOLATION-006：统一 TIMEOUT/依赖失败/未执行状态和「重连一次」规则，不改变分阶段采集结构。依赖 003。
- [ ] SQL-TIMEOUT-REPORT-007：HTML/JSON/CLI 一致展示超时证据，增加查询超时文案。依赖 003、006。
- [ ] SQL-TIMEOUT-TESTS-008：故障隔离与报告完整性离线测试（设计 §6 T-01～T-14），随 002～007 增量交付。
- [ ] SQL-TIMEOUT-CANCEL-VERIFY-009：四种驱动取消能力的真实验证，证明数据库端确实停止执行（含 MySQL 负对照）。依赖 003～005，逐引擎授权，不执行 KILL。

## 基础兼容、运行时与实验室

- [ ] VP-E-001 — IN_PROGRESS：剩余迁移、文档对齐和代表性验证；Backlog 仍有该状态，但它不是 Progress 当前开发指针。P5 完成后先核对其实际剩余范围，不要直接并行开工。
- [ ] MYSQL-DRIVER-PROVENANCE-001 — BACKLOG：复现并解释导入模块版本 2.2.8 与发行包元数据 1.1.3 的不一致；先查清包来源，不要盲目重装或升级。
- [ ] VP-OLDVERSIONS-001 — BACKLOG：在安全环境存在时逐个补充正式目标旧版本的代表性真实兼容验证。
- [ ] LINUX-DRIVER-COMPAT-001 — BACKLOG：验证 Linux 原生部署/IPC、旧驱动和 TLS/补丁互通。
- [ ] LAB-SQLSERVER-TLS-001 — BACKLOG：为本地 SQL Server Docker 实验室建立可信服务端证书和主机名验证；不得改变生产 TLS 默认值。

## 长期数据库与日志分析扩展

- [ ] RUNTIME-MONGO-001 — BACKLOG：MongoDB 4.0+ 长期诊断目标；当前不宣称已实现。
- [ ] RUNTIME-REDIS-001 — BACKLOG：Redis 4.0+ 长期诊断目标；当前不宣称已实现。
- [ ] OFFLINE-LOG-MYSQL-001 — BACKLOG：评估 pt-query-digest 离线日志分析 PoC；单独审查许可、隐私和打包。
- [ ] OFFLINE-LOG-PG-001 — BACKLOG：评估 pgBadger 离线日志分析 PoC；单独审查许可、隐私和打包。

---

# 5. 暂不启动 / 明确禁止的事项

- [ ] 不在 P5 尚未验收时跳到新 Phase 或擅自启动 Top SQL Delta。
- [ ] 不重复实现已经 DONE/VERIFIED 的 Top SQL Collector、SQL Registry 安全路径或报告框架。
- [ ] 不把真实 HTML、JSON、SQLPLAN、数据库日志、Inventory、凭据配置或压测输出提交 Git。
- [ ] 不在 Codex 隔离环境尝试真实数据库 E2E；Windows 宿主机上下文无法确认时必须停止。
- [ ] 不用 Toolkit 监控账号执行 DDL/DML，不把实验室高权限测试账号转给 Toolkit。
- [ ] 不自动 KILL、cancel、terminate、GRANT/REVOKE、改参数、建索引、修复、重启或故障切换。
- [ ] 不使用 git add . / git add -A；不覆盖未审查的用户工作区文件。
- [ ] 未经用户明确授权，不提交、push、合并 main、创建 Tag 或发布 Release。

---

# 6. 每次维护 TODO 的规则

1. 先读 Backlog 和 Progress，确认正式任务状态和当前开发指针。
2. 新增任务先检查是否已有 Task ID，避免重复登记。
3. 任务状态只在 Backlog 修改；真实测试证据只在 Acceptance 添加。
4. Progress 只维护当前任务、下一步和等待用户动作。
5. 本文件只保留当前行动和关键后续事项；每次任务状态变更后同步刷新。
6. 完成任务不等于自动启动下一项；必须满足 Roadmap 依赖和用户授权。
7. 发现本文件与 Backlog 冲突时，以 Backlog 为准并修正本文件。

文档结束。
