# DBA Toolkit V0.9.1 规划一致性审计与路线图整合建议

**审计日期：** 2026-10-19

**代码版本：** `0.9.0`（本轮不升级版本）

**审计基线：** `f840cdc fix(oracle): align emergency SQL and identity presentation`

**结论：** `PLANNING RECONCILIATION COMPLETE`

**边界：** 本轮只核对文档、源码、测试和 Git 历史并新增本文；未修改业务代码、SQL、Inventory、既有规划文档或数据库，未创建 Commit。

## 1. 审计方法与状态口径

本审计按以下顺序判定任务状态：当前源码与 Registry/Profile/Permission/Preflight 证据，当前自动化测试，仓库内有日期和环境范围的真实 E2E 记录，最后才是旧文档中的完成声明。旧版本实测不自动升级为当前 HEAD 的实测；用户在 2026-10-19 提供的 Oracle 真实阻塞观察属于有效外部运行证据，但在形成仓库内可复现的正式记录前，不记为当前 HEAD 的完整 E2E 验收。

| 状态 | 本文含义 |
|---|---|
| `COMPLETED_VERIFIED` | 当前实现与其适用范围内的验收证据闭合；不扩大到未实测版本。 |
| `IMPLEMENTED_E2E_PENDING` | 当前代码和自动化测试存在，但当前 HEAD、目标版本或完整 CLI 链路的真实 E2E 尚未闭合。 |
| `IN_PROGRESS` | 已有部分实现或子任务完成，仍有明确工作未闭合。 |
| `TODO` | 已有明确目标，尚未实施。 |
| `BACKLOG` | 保留目标，但不进入当前阶段。 |
| `DUPLICATED` | 与另一任务描述同一能力，应合并而不是重复开发。 |
| `CONFLICT` | 文档、顺序、范围或状态定义互相冲突，需要修订或用户决定。 |
| `OBSOLETE_CANDIDATE` | 可能已过时，不经用户确认不删除。 |
| `UNKNOWN` | 缺少足够证据。 |

本轮可复现的离线质量门：

- `python -m pytest -q`：`751 passed, 2 skipped`。
- `ruff check .`：通过。
- `mypy --strict dba`：60 个源文件通过。
- `dba validate`：4 个本地配置条目、103 条 SQL 通过；该命令未连接数据库。
- tracked-file Secret Scanner：通过。
- 未启用 System/Docker 测试，未连接任何数据库，因此本轮没有新增真实 E2E 证据。

## 2. 发现的规划、任务与验收资料

### 2.1 治理和产品方向

| 路径 | 当前用途 | 审计判断 |
|---|---|---|
| `AGENTS.md` | 修改前必须阅读项目规则、限制版本范围 | 保留；内容简洁且与本轮一致。 |
| `docs/PROJECT_RULES.md` | 长期安全、架构、CLI、报告、权限、版本和离线约束 | 应继续作为最高优先级工程约束，不承担任务状态。 |
| `README.md` | 产品能力、使用方式和历次版本摘要 | 是产品入口，不宜继续承担阶段状态；部分 V0.9.1 摘要已发生事实漂移。 |
| `docs/VERSION_PROFILES.md` | 当前 Version-Aware Profile 支持矩阵和证据级别 | 保留为能力/兼容性说明，不作为路线图状态源。 |
| `docs/MONITOR_PERMISSIONS.md` | Permission Registry 和最小权限契约 | 保留为权限规范。 |
| `docs/TESTING.md` | 测试分层、质量门和 E2E 边界 | 保留为测试规范。 |
| `docs/REPOSITORY.md`、`docs/DEPLOYMENT.md`、`docs/CREDENTIAL_SECURITY.md` | 发布、离线部署和凭据安全约束 | 保留为专项规范。 |

### 2.2 既有阶段设计和专项规划

| 路径 | 原规划/任务体系 | 当前用途 |
|---|---|---|
| `docs/design/version-aware-diagnostic-profiles-v0.9.1.md` | Phase A–E：Foundation、Replication、Slow、Disk-Growth、剩余迁移与实测 | 最成熟的既有阶段计划；A–D 已实现，E 部分完成。 |
| `docs/design/execution-plan-capability-safety-audit-v0.9.1.md` | Plan Phase P-A–P-E | 早期风险与迁移计划；P-A/P-B 已被后续实现关闭，P-C/P-D/P-E 仍有工作。 |
| `docs/design/emergency-diagnosis-ux-evidence-audit-v0.9.1.md` | 同步分阶段 P0 应急诊断设计 | 当前为未跟踪文件；核心实现已进入代码，但真实 E2E 和部分细节仍待闭合。 |
| `docs/design/performance-triage-and-sql-analysis-v0.9.1.md` | 性能快速分诊、Top SQL、SQL 深度诊断设计 | 当前为未跟踪文件；是新 Phase 1–3 的主要设计输入，不表示功能已实现。 |
| `docs/audits/opensource-diagnostic-integration-audit-v0.9.1.md` | 第三方工具许可、安全、离线集成取舍 | 当前为未跟踪文件；结论应进入长期约束和 Backlog，不应形成第二条产品路线。 |

### 2.3 功能说明、历史计划和验收记录

| 资料 | 范围 | 证据边界 |
|---|---|---|
| `docs/VERIFICATION_V0_2_1.md`、`docs/VERIFICATION_V0_3.md`、`docs/VERIFICATION_V0_4.md`、`docs/VERIFICATION_V0_6.md`、`docs/VERIFICATION_V0_7.md`、`docs/VERIFICATION_V0_7_1.md`、`docs/VERIFICATION_V0_8.md` | SQL Server 基础/Slow/Plan、MySQL Slow、Oracle Slow、四库 Disk-Growth 的历史实测 | 只证明文档日期、版本、权限和当时代码路径；不自动覆盖当前 HEAD。 |
| `docs/audits/test-report-v0.9.0.md` | 2026-10-17 四库汇总 System 验收 | SQL Server、PG、MySQL 完成真实只读 CLI；Oracle 因 ORA-01045 未进入查询链路。 |
| `docs/REPLICATION_DIAGNOSIS.md` | V0.9.0 四库复制诊断行为、权限和人工验收说明 | 功能规范；多数版本只有 Fake/Profile 证据。 |
| `docs/EMERGENCY_DIAGNOSIS.md` | P0 应急诊断操作说明 | 说明当前同步分阶段实现，不替代验收记录。 |
| Git 提交 `14c5cb9` 至 `f840cdc` | P0 分阶段呈现、MySQL 最近 SQL、PG/Oracle 证据与动作安全修复 | 是当前实现演进证据，不等于真实数据库 E2E。 |

另外发现未跟踪的根目录操作手册 `DBA_Toolkit_v0.8.3_操作手册.md`。它属于用户文档而不是规划状态源；本轮未修改或纳入版本控制。`docs/USAGE.md` 和 `docs/DOCKER_VERIFICATION_PLAN.md` 分别是使用说明与隔离验证安全流程，也不承担任务状态。

仓库没有发现独立的旧 `TODO`、`TASKS`、`BACKLOG` 或总 Roadmap 文件。原任务状态散落在 README、专项设计中的 Phase、小节末尾的实施状态、验证报告和提交历史中。这是本次需要建立单一状态来源的主要原因。

本轮检查时以下建议文件均不存在：

- `docs/roadmap/DBA_TOOLKIT_V0.9.1_ROADMAP.md`
- `docs/roadmap/DBA_TOOLKIT_V0.9.1_PROGRESS.md`
- `docs/roadmap/DBA_TOOLKIT_V0.9.1_ACCEPTANCE.md`

因此不存在需要覆盖或合并的既有三件套。本轮遵守“先审计、用户确认后再建路线图”的边界，没有创建它们。

## 3. 原项目规划的主要阶段

### 3.1 已形成产品基础的历史版本线

1. SQL Server 只读连接、Registry、Preflight、基础 Collector 和 Slow。
2. SQL Server 缓存执行计划读取；不执行捕获到的业务 SQL。
3. PostgreSQL、MySQL、Oracle Slow 与阻塞/事务证据。
4. 四库 Disk-Growth 当前状态诊断。
5. Permission Registry、离线 Runtime、Connection Profile、Credential Store/Session。
6. 统一 Human/JSON/HTML 报告、安全 ReferenceCommand 和仓库升级流程。
7. 四库 Replication 只读诊断。
8. Version-Aware Profile：Phase A Foundation、Phase B Replication、Phase C Slow、Phase D Disk-Growth、Phase E 剩余迁移与实测。
9. P0 应急体验：先输出核心证据，再采集补充证据并生成离线 HTML。

这些目标仍然有效，不能因 V0.9.1 性能路线而被删除或重新开发。

### 3.2 尚未闭合的原规划工作

| 建议保留 ID | 原来源 | 原目标 | 当前状态 | 保留原因/恢复条件 |
|---|---|---|---|---|
| `VP-E-001` | Version Profile Phase E | 完成剩余场景迁移、文档、代表性 Docker E2E | `IN_PROGRESS` | Plan 已迁移；旧版本矩阵、当前 HEAD 代表性 E2E 和文档漂移仍未闭合。 |
| `PLAN-PB-E2E-001` | Plan P-B/P-E | 在当前 Version-Aware route 上取得 SQL Server 2022 代表性计划 | `IMPLEMENTED_E2E_PENDING` | 当前 route/profile/permission/CLI cache-miss 已实测，但代表性应用计划的当前 route 验收仍需记录。 |
| `PLAN-PC-001` | Plan P-C | Oracle 缓存 cursor/child/权限/边界可行性 | `TODO` | 与新 Phase 3 完全一致，应复用原 ID 语义。 |
| `PLAN-PD-MYSQL-001` | Plan P-D | MySQL 精确版本的 `EXPLAIN FOR CONNECTION` 只读可行性 | `BACKLOG` | 必须限定活动连接和语句类型，不执行捕获 SQL。 |
| `PLAN-PD-PG-001` | Plan P-D | PG 预存在计划/日志来源研究 | `BACKLOG` | 不自动启用扩展、参数或 `EXPLAIN ANALYZE`。 |
| `PLAN-QS-001` | Plan P-D/Open Question | SQL Server Query Store 作为独立历史来源 | `BACKLOG` | 仅在已启用、可读取时设计；不配置、不强制计划。 |
| `P0-E2E-PG-001` | P0/PG P0.1 提交线 | 验证 PG 事务、Root Blocker、身份与人工动作安全修复 | `IMPLEMENTED_E2E_PENDING` | 当前自动化通过；后续修复后的正式 E2E 未在仓库闭合。 |
| `P0-E2E-MYSQL-001` | P0/MySQL recent statements 提交线 | 验证 blocker 定向最近 SQL 与降级语义 | `IMPLEMENTED_E2E_PENDING` | recent history 不是 digest Top SQL；当前代码缺正式 E2E 记录。 |
| `P0-E2E-ORACLE-001` | Oracle P0.1/P0.2 提交线 | 验证 XID、开始时间、当前/上一 SQL、链路和动作安全 | `IMPLEMENTED_E2E_PENDING` | 用户已有真实观察，但当前 HEAD 的可复现正式记录仍待补充。 |
| `VP-OLDVERSIONS-001` | Version Profile Phase E | SQL Server/PG/MySQL/Oracle 旧版本代表性实测 | `BACKLOG` | Fake/Profile 不是实机兼容性；需要可用安全环境后逐库验收。 |
| `RUNTIME-MONGO-001` | README/Runtime Manifest | MongoDB 运行时准备与未来数据库功能 | `BACKLOG` | 当前只有依赖准备，不应误报数据库诊断已支持。 |
| `RUNTIME-REDIS-001` | README/Runtime Manifest | Redis 运行时准备与未来数据库功能 | `BACKLOG` | 同上；未经用户批准不改变四库优先范围。 |
| `OFFLINE-LOG-MYSQL-001` | 开源集成审计 | `pt-query-digest` 独立离线日志 PoC | `BACKLOG` | 需单独许可、打包、隐私和输出合同评审。 |
| `OFFLINE-LOG-PG-001` | 开源集成审计 | `pgBadger` 独立离线日志 PoC | `BACKLOG` | 同上；不进入在线诊断核心。 |

## 4. V0.9.1 新规划 Phase 0–5

| 新阶段 | 目标 | 与原规划的关系 | 审计建议 |
|---|---|---|---|
| Phase 0 | 证据基线、事实漂移、当前 HEAD E2E 闭合 | 吸收 `VP-E-001`、Plan P-E 和三个 P0 E2E 尾项 | 作为用户批准后的下一阶段；先闭合证据，不开发新能力。 |
| Phase 1 | 锁、非锁长 SQL、证据不足的快速分诊 | 复用四库 Slow、P0 分阶段呈现、Profile/Permission/Reporter | 只补统一分诊入口和结论合同，不重建 Collector/Analyzer/报告框架。 |
| Phase 2 | Top SQL 与资源归因 | 主要为新能力；复用 MySQL recent SQL、SQL Server Plan 和 Oracle cursor 字段作为上下文 | 按数据库独立小任务验收；MySQL digest 可优先，但需用户确认数据库顺序。 |
| Phase 3 | SQL 深度诊断与执行计划 | 直接继承 Plan P-C/P-D、已有 SQL Server Plan | SQL Server 只做增量；Oracle 保留 Child Cursor 原生语义；PG/MySQL 先研究安全来源。 |
| Phase 4 | 数据库专项故障诊断 | 扩展已有 Disk-Growth 和 Replication | 不重复建设空间/复制底座；一次只选一个故障场景。 |
| Phase 5 | 统一 HTML 故障工作台 | 复用现有 ReportModel/Presenter/Renderer 与 P0 中文报告 | 核心诊断稳定后再做；统一导航和证据层级，不强制数据库字段一致。 |

## 5. 新旧任务映射矩阵

表中的“原优先级”按原文明确顺序或安全阻断级别记录；原文没有优先级时标为“未定义”，不自行补写成承诺。

| 原始文档/任务 | 原目标 | 原优先级/原状态 | 当前代码实现证据 | 当前测试与真实 E2E | V0.9.1 对应 | 建议 ID / 最终状态 | 冲突 |
|---|---|---|---|---|---|---|---|
| Version Profile Phase A | 单一不可变 Resolver、版本检测、fail-closed | Foundation；已标 implemented | `dba/versioning.py`、Inventory `version_family`、四库 `version_detect` | 本轮全套测试/validate 通过；版本矩阵主要为 Fake | Phase 0 公共底座 | `VP-A` / `COMPLETED_VERIFIED` | 无 |
| Version Profile Phase B | Replication SQL/权限/parser 原子路由 | 第二阶段；已标 implemented | `dba/versioning.py`、`dba/replication_collectors.py`、四库 Registry、Permission Registry | V0.9.0 三库真实 CLI；Oracle 被 CREATE SESSION 阻塞；旧版本为 Fake | Phase 0、Phase 4 | `VP-B` / `IMPLEMENTED_E2E_PENDING` | 证据范围需拆分 |
| Version Profile Phase C | 四库 Slow 原子路由 | 已标 implemented | `dba/slow_profiles.py` 和四库 Slow | 自动化通过；历史四库各有不同时点 E2E，当前 P0 修复后未统一重跑 | Phase 0、Phase 1 | `VP-C` / `IMPLEMENTED_E2E_PENDING` | README 仍说其他场景 legacy |
| Version Profile Phase D | 四库 Disk-Growth 原子路由 | 已标 implemented | `dba/disk_growth_profiles.py`、四库 disk SQL | 3 库当前路由 FULL；Oracle 当前权限门阻塞；V0.8 有旧路径四库 E2E | Phase 0、Phase 4 | `VP-D` / `IMPLEMENTED_E2E_PENDING` | 旧 E2E 与当前 route 不同 |
| Version Profile Phase E | 剩余迁移、文档和 E2E | 后续 | Plan 已迁移；README/部分审计仍保留旧描述 | Plan route 静态/自动化通过；旧版本矩阵缺失 | Phase 0、Phase 3 | `VP-E-001` / `IN_PROGRESS` | 有文档漂移 |
| Plan P-A | 权限、Resolver、validate 安全闭合 | release blocker | 当前 Plan 经 Resolver，绑定版本/权限/parser | 本轮 tests/validate 通过 | Phase 0/3 公共底座 | `PLAN-PA` / `COMPLETED_VERIFIED` | 旧风险段落已过时但有 post-status 修正 |
| Plan P-B | SQL Server Version-Aware Plan | 先于其他数据库 | `execution_plan*.py`、`plan_*.sql`、CLI | V0.4 旧路径拿到真实计划；当前 route 只记录到 cache miss 系统检查 | Phase 0、Phase 3 | `PLAN-PB-E2E-001` / `IMPLEMENTED_E2E_PENDING` | “route 实测”和“计划可用实测”易混淆 |
| Plan P-C | Oracle cached cursor | Plan 后续 | 未发现 Oracle Plan Collector/Profile | 无 | Phase 3 Oracle | `PLAN-PC-001` / `TODO` | 无 |
| Plan P-D MySQL/PG/Query Store | 各库安全计划来源 | 后续研究 | 未实现 | 无 | Phase 3 | 保留原建议 ID / `BACKLOG` | 与“自动四库 Plan”方案冲突；后者已明确拒绝 |
| P0 同步分阶段应急输出 | 核心证据先显示、补采后 HTML | P0 | CLI phased collect/build、priority candidates、共享 Presenter | 本轮自动化通过；无本轮 live E2E | Phase 1 和 Phase 5 基础 | `P0-CORE` / `IMPLEMENTED_E2E_PENDING` | 不应在 Phase 1/5 重建 |
| PostgreSQL P0.1 修复线 | 事务、Root Blocker、动作安全 | P0 | `dba/postgresql_slow.py`、Incident/Presenter | 修复后自动化通过；正式 live 记录缺失 | Phase 0/1 | `P0-E2E-PG-001` / `IMPLEMENTED_E2E_PENDING` | 无 |
| MySQL recent statements | blocker 定向有限历史 SQL | P0 增补 | `sql/mysql/recent_statements.sql`、Collector、Reporter | 自动化覆盖完整；无正式 live 记录 | Phase 1 上下文；Phase 2 不可替代 digest | `P0-E2E-MYSQL-001` / `IMPLEMENTED_E2E_PENDING` | 若当作 Top SQL 会形成语义冲突 |
| Oracle P0.1/P0.2 | 会话/事务身份、XID、当前/上一 SQL、动作安全 | P0 收尾 | 最近 3 个 Oracle fix 提交及大量 `tests/test_oracle.py` 覆盖 | 用户提供真实观察；仓库缺当前 HEAD 正式可复现 E2E | Phase 0/1，后续 Phase 3 | `P0-E2E-ORACLE-001` / `IMPLEMENTED_E2E_PENDING` | 旧 V0.9.0 Oracle blocker 不能覆盖新证据，新证据也不能反推全链路通过 |
| 四库 Slow/Blocking | 当前 SQL、阻塞链、事务证据 | 历史核心能力 | 四库 Slow Analyzer/Profile/SQL | 多份历史实测；当前修复后未统一重跑 | Phase 1 基础 | `SLOW-BASE` / `IMPLEMENTED_E2E_PENDING` | 与新“快速分诊”部分重复，应做增量差距任务 |
| 通用 Top SQL | 高频、CPU、读、等待、长 SQL 分类 | 新规划 Phase 2 高优先 | 未发现通用 Top SQL 路径 | 无 | Phase 2 | `V091-P2-TOPSQL-*` / `TODO` | 不能把 elapsed/current/recent history 冒充 Top SQL |
| SQL Server Plan 增强 | 关联、节点、参数敏感、可选 Query Store | 新 Phase 3 | 基础 Plan 已有 | 当前只有 SQL Server 2022 有历史实测 | Phase 3 | `V091-P3-SQLSERVER-PLAN-DELTA` / `TODO` | 若“重新开发 Plan”则重复 |
| 四库 Disk-Growth | 空间和日志保留当前状态 | 已有核心能力 | 已实现 | 版本/权限 E2E 范围不齐 | Phase 4 基础 | `DISK-BASE` / `IMPLEMENTED_E2E_PENDING` | 新专项必须扩展，不复制 |
| 四库 Replication | 单实例只读复制状态 | 已有核心能力 | 已实现 | SQL/PG/MySQL V0.9.0 实测；Oracle未闭合 | Phase 4 基础 | `REPL-BASE` / `IMPLEMENTED_E2E_PENDING` | 无 |
| HTML/JSON/Human Report | 离线、有界、证据优先报告 | 已有核心能力 | `dba/reporting/`、Incident/Presenter | 自动化与历史 System 报告存在 | Phase 5 基础 | `REPORT-BASE` / `COMPLETED_VERIFIED` | Phase 5 不得另建 renderer |
| MongoDB/Redis | Runtime 已准备，数据库能力未来扩展 | 后续 | extras/manifest 存在；诊断未实现 | 无数据库功能 E2E | Phase 5 之后的长期 Backlog | `RUNTIME-MONGO-001`、`RUNTIME-REDIS-001` / `BACKLOG` | 新路线未收录，不能静默取消 |
| pt-query-digest/pgBadger | 独立离线日志分析 PoC | 后续可选 | 未集成 | 无 | Phase 4 之后 Backlog | `OFFLINE-LOG-*` / `BACKLOG` | 不能变成在线依赖 |

## 6. 重复任务

1. **Phase 1 快速分诊与现有 Slow/P0 重叠。** 现有四库 Collector、阻塞图、事务关联、分阶段 CLI、Incident 和报告必须复用。新任务只应覆盖分类合同、不同结论和下一步选择。
2. **Phase 3 SQL Server Plan 与既有 Plan P-A/P-B 重叠。** 现有 Registry/Profile/parser/report 路径继续使用，只增加身份关联、节点证据和可选来源。
3. **Phase 4 空间/复制与现有 Disk-Growth/Replication 重叠。** 应按明确缺口扩展现有 Profile，不新建平行 Analyzer 或 Registry。
4. **Phase 5 HTML 与当前 ReportModel/Presenter/Renderer 重叠。** 只做信息架构和数据库专属模块，不再建第二套 HTML 框架。
5. **MySQL recent statements 与未来 digest Top SQL 容易被误合并。** 前者是有限环形历史且定向 root blocker，后者是 digest 累计统计；必须保留两个任务和两种语义。
6. **Version Profile 支持矩阵与新 Acceptance 矩阵可能重复维护状态。** 前者只写能力/版本契约；真实运行证据统一写 Acceptance，并由链接引用。

## 7. 冲突与事实漂移

| ID | 类型 | 冲突/漂移 | 当前事实 | 建议处理 |
|---|---|---|---|---|
| `C-01` | 状态 | README 只写 Phase A/B，称其他诊断仍走 legacy | 当前 Slow Phase C、Disk Phase D、SQL Server Plan route 已实现 | Roadmap 确认后修 README；不要回退代码。 |
| `C-02` | 版本范围 | README PostgreSQL 写 12+ 设计目标，Version Profiles 为 13–18 | 当前可执行 Profile 以 13–18 为准 | 用户确认是否保留 PG12 产品承诺；未确认前 PG12 标 `UNKNOWN`。 |
| `C-03` | 版本范围 | README MySQL 章节仍以 8.0 描述，Profile 已有 5.7/8.0/8.4 | 当前代码有三族路由，只有部分版本实测 | README 改为“支持契约”和“实测范围”两列。 |
| `C-04` | Oracle 验收 | V0.7.1 Oracle 23 Slow E2E 通过；V0.9.0 Oracle 因 CREATE SESSION 阻塞 | 两者测试账号、日期和代码路径不同，均为真实历史事实 | 不覆盖；Acceptance 分行记录。当前 HEAD 仍需新验收。 |
| `C-05` | Oracle 当前证据 | 用户 2026-10-19 观察到根阻塞、XID、链和参考动作；仓库正式报告未记录同轮 | 可作为外部证据，不能自动变成全链路 E2E | Phase 0 建立脱敏、可复现、范围明确的正式记录。 |
| `C-06` | Plan 状态 | Plan 审计早期 R1/R2 说绕过 Resolver；同文后续章节和当前代码说已修复 | 当前代码已绑定 Resolver/Profile/Permission/parser | 保留历史风险但在 Roadmap 中标 `COMPLETED_VERIFIED`，不重复修。 |
| `C-07` | Plan E2E | V0.4 拿到代表性计划；当前 route 系统检查只得到 `PLAN_NOT_AVAILABLE` | route/权限/CLI 已实测，当前 route 的代表性 plan retrieval 仍缺正式记录 | 状态用 `IMPLEMENTED_E2E_PENDING`，不写“Plan 全部未实测”或“当前全通过”。 |
| `C-08` | Disk E2E | V0.8 四库通过；Phase D 当前 Oracle 因 4 个 core grant 被门禁阻断 | V0.8 与当前 Version-Aware route/权限合同不是同一验收对象 | 保留旧结果，Phase 0 补当前 Oracle route 验收。 |
| `C-09` | 状态来源 | README、设计文档、VERSION_PROFILES、TESTING 都包含状态描述 | 没有唯一任务进度文件 | 建立 Backlog 单一任务状态账本，其他文档只引用 ID。 |
| `C-10` | 版本名 | 文档称 V0.9.1，包和 README 当前版本为 0.9.0 | 用户明确本轮仍为 0.9.0 | 将 V0.9.1 解释为规划目标版本；未经批准不改版本号。 |

## 8. 新规划遗漏但必须保留的旧目标

- Credential Provider/Encrypted Store/Credential Session 的 fail-closed 安全和跨平台 IPC。
- 离线 wheelhouse、Runtime Manifest、升级/回滚和 native driver 前置条件。
- Inventory 分目录、非敏感 Connection Profile、Secret Scanner 和本地资产保护。
- Permission Registry、Preflight、Account Security 与 Feature Readiness 分离。
- Version-Aware Profile Phase E 的旧版本代表性验收。
- Replication 的四库观察性诊断和绝不自动 failover/repair 的边界。
- MongoDB/Redis 的长期扩展占位；延后不等于取消，也不代表当前诊断支持。
- `pt-query-digest`、`pgBadger` 独立离线日志 PoC；不得混入当前在线诊断运行时。
- Linux 真实部署/IPC 验证、旧 ODBC/TLS 互通等跨平台遗留项。

这些工作不应插入当前 Phase 0–3 主线，但必须进入明确 Backlog 并写恢复条件。

## 9. 已实现但尚未充分实机验收

| 能力 | 当前实现 | 已有真实证据 | 仍缺证据 |
|---|---|---|---|
| 当前 HEAD 四库 Slow/P0 | 四库 Profile、Collector、Analyzer、分阶段 CLI/HTML/JSON | 历史版本分别有实测；用户有新 Oracle 观察 | 最近 PG/MySQL/Oracle 安全修复后的统一、正式 current-HEAD E2E。 |
| Oracle P0.2 | Root/XID/事务时间来源、当前/上一 SQL、身份与动作状态分离 | 用户观察和当前自动化 | 脱敏正式记录、精确 HEAD、命令/环境、完整性和清理结果。 |
| MySQL recent statements | 有界 blocker 定向 recent history | 自动化 | consumer on/off、history overwrite、权限降级的真实环境记录。 |
| SQL Server Version-Aware Plan | route/permission/parser/ambiguity/cache-miss 已实现 | 当前 route 的连接/权限/cache-miss；V0.4 旧路径代表性计划 | 当前 route 上存在可用计划时的完整 CLI/HTML/`.sqlplan` 验收。 |
| Oracle Version-Aware Disk-Growth | 代码/Profile/Permission 合同存在 | 旧 V0.8 路径通过；当前连接/版本/permission gate 可达 | 当前所需 direct grants 配置后 feature SQL 和报告验收。 |
| Oracle Replication | 现代/旧 Data Guard profile 存在 | Fake/Registry | 真实 Oracle 当前版本 E2E；11gR2 保持未验证。 |
| 旧版本兼容矩阵 | SQL Server 2008 R2–2019、PG13–18、MySQL5.7/8.0、Oracle11gR2–26ai 路由存在 | 代表性版本和 Fake tests | 各声明族的实机兼容性；不能从 Fake 推导。 |

## 10. 建议的唯一规划与进度管理结构

待用户确认后再创建以下文件；本轮不创建：

| 文件 | 唯一职责 | 不应包含 |
|---|---|---|
| `docs/roadmap/DBA_TOOLKIT_V0.9.1_ROADMAP.md` | 产品目标、Phase 0–5、依赖、进入/完成条件、明确不做和长期方向 | 易变的逐任务当前状态、重复验收结果。 |
| `docs/roadmap/DBA_TOOLKIT_V0.9.1_BACKLOG.md` | **唯一任务状态账本**：Task ID、目标、模块、输入/输出、安全、单测、E2E、完成条件、状态、阻塞和恢复条件 | 大段设计正文、重复粘贴验收日志。 |
| `docs/roadmap/DBA_TOOLKIT_V0.9.1_PROGRESS.md` | 当前 Phase、当前 Task ID、临时 Bug 栈、NEXT TASK 和新会话恢复指针 | 全量任务状态；只能引用 Backlog ID。 |
| `docs/roadmap/DBA_TOOLKIT_V0.9.1_ACCEPTANCE.md` | 按 Task ID 记录代码版本、测试层、目标版本、权限、命令、结果和证据路径 | 把 Mock/Fake 写成真实 E2E、维护第二份任务状态。 |
| 本文 | 新旧 ID 对照、冲突和一次性合并决策记录 | 长期更新日常进度。 |

若坚持只保留 Roadmap/Progress/Acceptance 三份文件，可把 Backlog 作为 Roadmap 的“任务附录”；但必须规定任务状态只在该附录更新，Progress 仍只保存当前指针。独立 Backlog 更适合长期维护和 Bug 插队恢复。

Bug 使用稳定 ID（例如 `BUG-014`），在 Backlog 中关联 `parent_task_id` 和 Phase；Progress 记录“暂停的主线任务 → 当前 Bug → 修复后恢复任务”。任何专项设计文档只能引用这些 ID，不再维护独立完成状态。

## 11. 合并建议与首批 Task ID

### 11.1 合并原则

1. 原 Version Profile、Plan 和 P0 ID/阶段语义保留，建立别名，不全量重新编号。
2. 已实现能力先做差距核对，禁止以新 Phase 名义重建 Collector、Adapter、Registry、Permission、Preflight 或 Reporter。
3. `COMPLETED_VERIFIED` 只在任务自己的范围内成立；真实版本矩阵和 current-HEAD E2E 分成独立任务。
4. Phase 0 结束必须由用户确认后才能进入 Phase 1；Phase 1 结束后再进入 Phase 2。
5. 每个数据库的 Top SQL/Plan/专项故障分别验收，不为四库同步而牺牲原生语义。

### 11.2 建议首批任务

| Task ID | 任务 | 状态 | 完成条件 |
|---|---|---|---|
| `V091-PLAN-RECON-001` | 新旧规划一致性审计 | `COMPLETED_VERIFIED` | 本文完成，冲突、遗漏、重复、待决策和下一任务明确。 |
| `V091-P0-BASELINE-001` | 建立当前 HEAD 能力/证据基线和 Acceptance 账本 | `TODO` | 每项区分 code、unit/fake、historical E2E、current-HEAD E2E；引用精确证据。 |
| `V091-P0-ORACLE-002` | Oracle P0.2 current-HEAD E2E 收尾 | `TODO` | 在授权隔离环境验证 root/XID/链/SQL/动作抑制与清理；不执行处置命令。 |
| `V091-P0-PLAN-003` | SQL Server Plan 当前 route 代表性 E2E | `TODO` | 验证有计划与 cache miss 两种路径、报告/`.sqlplan`、权限和清理。 |
| `V091-P0-FOURDB-004` | 四库 current-HEAD 代表性回归汇总 | `TODO` | 只对可用安全目标逐库运行，未运行项明确 pending，不强求同一会话全部完成。 |
| `V091-P0-DOCS-005` | 修正文档事实漂移 | `TODO` | 修正 README Phase/版本范围/证据口径，不改变产品范围或版本号。 |
| `V091-P1-TRIAGE-001` | Phase 1 增量差距设计 | `BACKLOG`（未获准开始） | 明确复用点、只新增的结论合同和验收样例；用户批准后才实施。 |

Phase 2–5 的细分 Task ID 应在相应阶段进入前建立，避免现在锁定未经用户确认的数据库顺序和范围。

## 12. 需要用户确认的重大决策

1. **任务文件结构：** 是否批准新增独立 `DBA_TOOLKIT_V0.9.1_BACKLOG.md` 作为唯一任务状态账本；推荐批准。
2. **PostgreSQL 最低版本：** 是维持当前可执行 Profile 的 PG13+，还是恢复 README 的 PG12 产品承诺并新增兼容任务。推荐当前 V0.9.1 明确 PG13+，PG12 作为需单独评审的 Backlog。
3. **Phase 0 E2E 顺序：** 推荐先 Oracle P0.2，再 SQL Server Plan 代表性 route，最后按可用环境补四库汇总；这只调整验收顺序，不改变产品功能范围。
4. **Phase 2 首库：** MySQL digest、SQL Server query stats、Oracle V$SQL/V$SQLSTATS、PostgreSQL 可选 pg_stat_statements 中先做哪一个。推荐在 Phase 1 验收后，根据安全环境和生产痛点再决定，不在本轮预先承诺。
5. **MongoDB/Redis：** 是否确认继续作为长期 Backlog 而不进入 V0.9.1 Phase 0–5。推荐保留但延后。
6. **离线日志工具：** 是否接受 `pt-query-digest`/`pgBadger` 仅作为独立 PoC Backlog，不作为 Toolkit 运行时依赖。推荐接受。

## 13. 本轮结论与下一步

```text
PLANNING RECONCILIATION COMPLETE

OLD PLANS FOUND:
- Version-Aware Profile Phase A-E
- Execution Plan Phase P-A-P-E
- P0 emergency diagnosis design and subsequent PG/MySQL/Oracle fix line
- Historical version feature line V0.2.1-V0.9.0
- Performance triage / Top SQL / deep SQL design
- Open-source integration audit and future offline-log options

OLD TODO ITEMS:
- 14 grouped retained items in section 3.2

DUPLICATED TASKS:
- 6 duplicate/overlap groups in section 6

CONFLICTING TASKS:
- 10 conflicts or fact drifts in section 7

MISSING TASKS:
- Credential/runtime/repository safety maintenance
- Version Profile old-version E2E
- MongoDB/Redis future scope
- Offline log analysis PoCs

IMPLEMENTED BUT UNVERIFIED:
- Current-HEAD four-database Slow/P0 fixes
- Oracle P0.2 formal E2E
- MySQL recent statements live behavior
- Current-route representative SQL Server Plan retrieval
- Oracle current-route disk-growth and replication
- Old-version compatibility families

USER DECISIONS REQUIRED:
- Backlog as the single task-status ledger
- PostgreSQL minimum supported family
- Phase 0 E2E order
- Future Phase 2 first database
- MongoDB/Redis and offline-log backlog disposition

NEXT PHASE: Phase 0 (only after user approves the merge structure)
NEXT TASK ID: V091-P0-BASELINE-001
NEXT TASK: Create the approved Roadmap/Backlog/Progress/Acceptance structure and populate the current-HEAD evidence baseline; do not develop new functionality.
REQUIRED EVIDENCE: This reconciliation, current HEAD/status, source/profile/registry/permission paths, current offline quality-gate results, historical verification documents, and clearly labeled user-provided Oracle evidence.
BLOCKERS: User decisions in section 12; live E2E additionally requires explicitly authorized isolated targets and available credentials.
```

下一次 Codex 会话可直接使用以下任务描述：

> 读取 `AGENTS.md`、`docs/PROJECT_RULES.md` 和 `docs/audits/roadmap-reconciliation-v0.9.1.md`，检查 Git HEAD/状态；按用户已确认的文件结构执行 `V091-P0-BASELINE-001`。只建立路线图、唯一任务账本、当前进度和验收证据基线，不修改业务代码，不连接数据库，不进入 Phase 1。

## Post-audit product-scope decision (2026-10-19)

The user subsequently confirmed the formal product targets: MySQL 5.7 and 8.0+, Oracle 11gR2+, PostgreSQL 14+, SQL Server 2008 R2+, MongoDB 4.0+, and Redis 4.0+. The four relational databases remain the current development mainline; MongoDB and Redis remain long-term Backlog targets. This decision supersedes the PostgreSQL alternatives and PG12 decision requested in conflict `C-02`, and supersedes older MongoDB/Redis 6.0+ wording. PostgreSQL 12/13 are outside the current target; existing PG13 compatibility code is retained without a formal support or verification claim. This addendum records the decision without rewriting the audit's contemporaneous findings. Current target and evidence authority is the Roadmap, Backlog, and Acceptance under `docs/roadmap/`.
