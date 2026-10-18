# V0.9.1 Execution Plan Capability & Safety Audit

**审计日期：** 2026-10-18
**审计范围：** 现有实现、四数据库能力、权限、安全边界、产品范围和迁移设计
**代码修改：** 无。本轮仅新增本设计审计文档。

## 1. 执行摘要

当前只有 SQL Server 实现了正式执行计划采集和分析。`dba diagnose <id> plan --session <sid>` 通过两个受审查的只读 Registry 脚本，优先取得当前请求计划；必要时尝试从仍在线会话的最近 SQL handle 取得缓存计划。采集不会把目标 SQL 交给 Toolkit 执行，也不会使用 `EXPLAIN`/SHOWPLAN 对目标 SQL 重新编译。结果由本地 Showplan XML parser 分析，超过大小/节点上限时拒绝或标为不可用。计划缓存缺失时不会重新执行 SQL。

审计发现两个应在 SQL Server Plan 版本化迁移中优先关闭的 HIGH 风险：

1. `dba/permissions.py` 的通用 `plan` 声明以 `VIEW SERVER STATE` 为 required；`requirements_for(..., version)` 可以在调用方传入版本时转换为 SQL Server 2022+ 的 `VIEW SERVER PERFORMANCE STATE`。但 Plan CLI 路径没有调用版本解析、版本化权限评估或特性权限 gate。因而通用权限声明不能单独作为该路径的有效契约，Plan 在执行前没有证明当前版本所需权限已满足，执行失败后的通用缺权映射也可能不够准确。
2. CLI 的 `plan` 路由直接调用 `diagnose_plan`，未通过 `DiagnosticProfileResolver` 执行已验证的版本绑定。`versioning.py` 只有一个 SQL Server 2022 legacy binding，计划 SQL、权限和 parser 也没有在该路由中原子绑定。与 V0.9.1 项目规则要求的 VERIFIED 才允许执行不一致。

当前 `.sqlplan` 是单独的本地敏感产物；HTML 不嵌入原始 XML。解析器对 XML 字段使用通用 `redact()`，但这不等于对任意业务常量或对象名进行完整脱敏。计划和 SQL 文本可能包含谓词常量、对象/Schema/数据库名及业务信息，因此应维持本机受控保存，不自动上传或扩散。

**产品建议：** 选择方案 B（仅支持经验证的数据库已有计划来源），但 V0.9.1 本轮迁移仅闭合 SQL Server Plan 的版本、权限、能力和安全契约；Oracle、MySQL、PostgreSQL 先按各自能力明确为 UNSUPPORTED 或统计可用但计划不可用，不为表面统一而执行目标 SQL。Query Store、Oracle cached cursor、多引擎计划支持分阶段决定。

**审批结论：** `APPROVE_WITH_CHANGES`。
**Can implementation start:** `YES`，但首个实现阶段必须先修复 SQL Server 2022 权限契约并将 Plan 路由纳入既有 `DiagnosticProfileResolver`；在完成前，不能把当前路径视为已满足 V0.9.1 version-aware 执行条件。

## 2. 当前实现清点

| Area | Current code and behavior | Audit result |
|---|---|---|
| CLI | `dba/cli.py` 的 diagnose 场景含 `plan`，接受 `--session`，拒绝非 SQL Server | SQL Server only；没有 Oracle/MySQL/PG Plan 命令 |
| Collector | `dba/execution_plan.py::ExecutionPlanCollector.collect()` 先执行 `plan_request`，后执行 `plan_cached` | 只读固定 SQL；目标 session id 做整数及范围检查并以绑定参数传入 |
| Active source | `sql/sqlserver/plan_request.sql` 限定 `sys.dm_exec_requests.session_id`，并取 request 的 `plan_handle`、`sql_handle`、SQL text 和 `sys.dm_exec_query_plan()` | 当前请求已有计划；不会执行请求 SQL |
| Cached source | `sql/sqlserver/plan_cached.sql` 通过在线连接的 `most_recent_sql_handle` 联结 `sys.dm_exec_query_stats`，取最近缓存计划 | 仅是与该会话最近 handle 相关的缓存证据，不证明它是 slow 报告中同一请求的确切执行实例 |
| Registry | `plan_request.yaml` 与 `plan_cached.yaml` 均标记 `READ_ONLY`、`reviewed: true`，timeout 10 秒、max_rows 2 | SQL 来自受审查 Registry，计划 XML 另有参数化上限 |
| XML | parser 拒绝 DTD/entity、错误根节点和超限内容，最大 50,000 XML 元素、10,000 operators；清洗解析后 XML | 资源限制存在；脱敏只能视为已知标记/模式的清理，不保证业务常量彻底匿名化 |
| Analyzer/report | `ExecutionPlanAnalyzer` 只基于实际存在的估算/实际统计生成发现；HTML 不嵌原始 XML；本地可生成配对 `.sqlplan` | `.sqlplan` 仍含敏感计划内容，需要按敏感本地证据管理 |
| Fallback | 当前请求无计划时仅当活动行与缓存行 `sql_handle` 相同才采用缓存行；当前无活动请求时使用仍在线 session 最近 handle | 做了基本防错配；不具有 SQL statement offset、request_id/plan_handle 等完整一致性证明 |
| Version route | `versioning.py` 中仅有 SQL Server 2022 `plan` legacy binding；CLI 没有 resolve 并验证此 binding | 未闭合 version-aware contract，是迁移阻断项 |
| Permissions | `dba/permissions.py` 通用 plan 定义 required=`VIEW SERVER STATE`；`requirements_for(..., version)` 对 2022+ 可替换为 `VIEW SERVER PERFORMANCE STATE` | 版本化权限 helper 存在，但 Plan 命令没有调用；需把它纳入 resolved profile/执行前 readiness，而非仅依赖通用 preflight |

代码证据：`dba/execution_plan.py` 的 Collector/Parser/Analyzer；`sql/sqlserver/plan_request.sql`、`plan_cached.sql` 及各自 YAML metadata；`dba/cli.py` diagnose dispatch；`dba/permissions.py` SQL Server `plan`；`dba/versioning.py` `PROFILE_BINDINGS`。

### 当前功能支持状态

- **SQL Server：** 当前实现为 SQL Server 专属。集成验证记录表明曾在 SQL Server 2022 Docker 上真实读取缓存计划；SQL Server 2008 R2 是设计目标，未在真实旧版本验证。当前实现仍未满足版本化 Resolver 执行门。
- **Oracle：** 当前没有 Plan 命令/Plan Collector/`DBMS_XPLAN` Registry 路径。Slow 使用的 SQL_ID 等证据不构成计划支持。
- **PostgreSQL：** 当前无 Plan Collector。`pg_stat_activity.query` 和 `pg_stat_statements` 统计不是计划树。
- **MySQL：** 当前无 Plan Collector。Performance Schema statement/digest 统计不是计划树。

## 3. 计划类型与安全等级

| Type | 含义 | Toolkit policy |
|---|---|---|
| Cached Execution Plan | 从服务器已有 plan cache/cursor store 查询 | 首选；固定只读查询、可验证标识、权限和输出上限齐备后为 `SAFE_READ` |
| Currently Executing Plan | 从正在执行请求关联的已有 plan handle/cursor 获取 | 可作为 `SAFE_READ` 或 `CONDITIONAL_READ`；会话状态短暂且计划不一定含实际统计 |
| Estimated Plan | 对 SQL 进行新编译/EXPLAIN 得出的估算计划 | 不作为跨库默认功能；需明确不是已执行计划，并独立评估 SQL 文本、解析权限和编译开销 |
| Actual Plan / Runtime Statistics | 包含已有执行期间的行数、循环、读写等统计 | 只有明确证明统计来自既有执行才可读；严禁通过 Toolkit 重放业务 SQL 或 `EXPLAIN ANALYZE` 生成 |

安全标签：

- **SAFE_READ：** 只读取服务器已有 plan/statistics，执行静态审核 SQL，严格绑定标识，限制行数、时间、payload 和报告大小。
- **CONDITIONAL_READ：** 依赖可选扩展、日志预先采集、Query Store 已启用或目标 SQL 可解释等条件；报告必须标条件和来源。
- **NOT_ALLOWED：** 执行/重放用户 SQL、自动 `EXPLAIN ANALYZE`、任意 SQL 拼接、自动变更计划/索引/统计信息/参数或强制计划。

## 4. 统一能力矩阵

`SUPPORTED` 表示数据库提供该类能力，并不代表 Toolkit 当前实现；`CONDITIONAL` 表示依赖已启用能力、权限或特定目标状态。矩阵中的 Toolkit 状态以本仓库当前代码为准。

| Capability | SQL Server 2008 R2–2019 | SQL Server 2022 | SQL Server 2025 | Oracle 11gR2–26ai | PostgreSQL 13–18 | MySQL 5.7 | MySQL 8.0 | MySQL 8.4 |
|---|---|---|---|---|---|---|---|---|
| Toolkit Plan command exists | CONDITIONAL (legacy shared route; only 2022 real E2E recorded) | CONDITIONAL (implemented; permission/profile gap) | UNKNOWN | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |
| Read cached existing plan | SUPPORTED by DMV family; old-version real validation absent | SUPPORTED; Toolkit path exists | UNKNOWN pending docs/runtime verification | CONDITIONAL via loaded cursor + `DBMS_XPLAN.DISPLAY_CURSOR`; Toolkit absent | UNSUPPORTED through standard built-in stats views | CONDITIONAL via `EXPLAIN FOR CONNECTION`, only active/explainable target; Toolkit absent | CONDITIONAL; minor-version/statement support must be checked | CONDITIONAL; target and explainable statement required |
| Read currently executing plan | CONDITIONAL: active request plan handle may be available | CONDITIONAL; Toolkit path exists | UNKNOWN | CONDITIONAL: loaded cursor/active session identity required; no Toolkit route | UNSUPPORTED from PID alone using standard built-ins | CONDITIONAL via named connection route, not complete historical support | CONDITIONAL | CONDITIONAL |
| Existing runtime statistics | CONDITIONAL: DMV/cache fields depend on plan/source | CONDITIONAL; current Showplan may lack `ActualRows` | UNKNOWN | CONDITIONAL: cursor row-source stats only if collected; otherwise estimates only | CONDITIONAL: `pg_stat_statements` aggregates, not plan; auto_explain logs only if preconfigured | CONDITIONAL: Performance Schema statement instrumentation, not plan | CONDITIONAL | CONDITIONAL |
| Estimated plan without target SQL execution | CONDITIONAL through SHOWPLAN route but outside current product scope | CONDITIONAL | UNKNOWN | CONDITIONAL via cursor/explain plan sources; must distinguish source | CONDITIONAL with `EXPLAIN <statement>`; still parses/plans target text | CONDITIONAL with `EXPLAIN`; parses/plans target text | CONDITIONAL | CONDITIONAL |
| Requires target SQL execution | No for existing DMV plan route | No for existing DMV plan route | UNKNOWN | No for existing cursor; `EXPLAIN PLAN` creates plan-table effects and excluded | `EXPLAIN ANALYZE` executes; prohibited | No for regular EXPLAIN, but target statement is optimized | No for regular EXPLAIN; `EXPLAIN ANALYZE` only newer 8.0 minors and executes | `EXPLAIN ANALYZE` executes; prohibited |
| Current Toolkit status | NOT VERSION-VERIFIED | Implemented but must fix permission + profile routing | UNKNOWN | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED | UNSUPPORTED |

For Oracle version families 11gr2, 12c, 18c, 19c, 21c, 23ai and 26ai, potential cached-cursor plan capability is **conditional**, but exact support/privileges/output need per-family documented and integration validation before any route is marked supported. Unknown or unverified full versions must not inherit the latest route.

## 5. Version compatibility audit

| Engine family | Audit position | Required future validation |
|---|---|---|
| SQL Server 2008 R2, 2012, 2014, 2016, 2017, 2019 | Existing DMV names broadly target older versions, but the current SQL and Showplan XML have no per-family verified binding | Validate columns/functions, permission requirements, XML/parser shape, query limits and integration behavior on each declared family before `SUPPORTED` |
| SQL Server 2022 | Existing real container validation is recorded; uses 2022 performance-state permission model | Require `VIEW SERVER PERFORMANCE STATE`; bind only verified profile; exercise missing permission, timeout, cache miss, oversized XML and actual-stat absence |
| SQL Server 2025 | No evidence in repo establishes Plan compatibility | `UNKNOWN`; no latest-version fallback. Verify official docs and integration before adding binding |
| Oracle 11gR2–26ai | `DBMS_XPLAN.DISPLAY_CURSOR` exists in 11gR2 documentation and later references, but Toolkit has no implementation | Verify direct SYS.V_$ grants and package privilege behavior per family; multi-child semantics; bounded output parser; runtime-statistics presence; real integration |
| PostgreSQL 13–18 | Current standard stats expose activity/aggregate telemetry, not another backend's full execution plan | Any log ingestion or externally captured plan is a separate conditional feature; do not mutate config to enable it |
| MySQL 5.7, 8.0, 8.4 | `EXPLAIN FOR CONNECTION` capability exists in supported server families, but precise release/statement constraints vary | Verify exact minor version, explainable statement set, cross-user PROCESS visibility and behavior for inactive/disappeared sessions |

The lower-bound support claims are design targets, not proof of real compatibility. Keep `SUPPORTED`, `UNIT_TESTED`, `INTEGRATION_TESTED`, and `REAL_E2E_VERIFIED` as separate evidence labels. Current repo records SQL Server 2022 real execution-plan integration only; it does not prove SQL Server 2008 R2 or any other engine.

## 6. SQL Server detail

### 6.1 Existing DMV route and query execution risk

`plan_request.sql` reads `sys.dm_exec_requests`, obtains the request's `plan_handle`, and calls `sys.dm_exec_query_plan`; it also reads SQL text through the handle. It does **not** submit `sql_text` back to SQL Server. `plan_cached.sql` similarly reads `sys.dm_exec_connections.most_recent_sql_handle`, `sys.dm_exec_query_stats` and `sys.dm_exec_query_plan`. These paths do not re-run application SQL, do not issue `SET SHOWPLAN_XML`, and do not use dynamic SQL assembled from the application text.

`plan_handle` identifies a compiled/cached plan instance and is the strongest current lookup key in this implementation. `sql_handle` identifies SQL text/batch identity and can correspond to more than one plan (for example, compilation contexts/options); it is not a unique plan identifier. `query_hash` can group query shapes and is not a plan identity. A batch can contain multiple statements: `statement_start_offset` and `statement_end_offset` are necessary to identify the statement range, usually against the SQL text; current Toolkit Plan probe does not make this full offset-based identity part of its contract.

The active probe is filtered to the requested session and returns at most two rows. The cached fallback uses `TOP (1)` ordered by `last_execution_time`; ties and multiple cached plans can make this choice ambiguous. It should not claim “this exact request used this plan” from `sql_handle` equality alone. If an active request disappears between probes, the current collector correctly avoids mismatched SQL handles, but the session may have changed or been reused between slow report and plan command.

### 6.2 XML, cache eviction and statistics

The plan XML is constrained by bound maximum size parameters (default 1 MiB), output rows are bounded, and parser limits XML elements/operators. Too-large or missing plans are reported unavailable; there is no re-execution fallback. XML Showplan may be NULL/unavailable for some compiled forms, nested structures, cache evictions or unsupported plan features. `sys.dm_exec_query_stats` contains statistics for completed executions while the plan is cached; rows disappear with plan eviction. Its aggregates are not a session-specific actual row count. `sys.dm_exec_query_plan` frequently supplies the compiled estimated plan without `ActualRows`; existing analyzer tests correctly avoid cardinality conclusions when actual rows are absent.

Even an allowed plan read has cost: server DMV/cache work, XML materialization/network transfer, client parse/serialization, and local disk artifacts. Keep query timeout, max rows, XML/statement/report limits. Do not automatically fetch plans for all slow SQL rows.

### 6.3 Permission distinction and Query Store

Microsoft documents `VIEW SERVER STATE` for older SQL Server versions and `VIEW SERVER PERFORMANCE STATE` for SQL Server 2022 and later for relevant plan and request DMV access. `SHOWPLAN` is different: it authorizes a user to request a plan for a submitted query in a database, subject to permissions on referenced objects. Current cached-plan retrieval does not need SHOWPLAN and must not add it as a substitute for DMV access.

**Current permission-contract gap:** The base Plan definition represents `VIEW SERVER STATE` as required and performance-state as optional. The shared `requirements_for()` helper does translate this when passed a SQL Server 2022+ version, and `dba permissions` uses version-aware requirements in its feature assessment. The Plan diagnose branch does not use that helper/profile gate, however; it calls the collector directly. Thus SQL Server 2022 plan execution has no explicit matching Plan readiness check, and error enrichment can fall back to the generic definition. Make the effective requirements part of the resolved Plan profile, and make readiness, execution, and error explanation consume the same profile.

Query Store is a possible future historical plan source for SQL Server 2016+, not a current source. Report its states separately: engine supports it; database has it enabled; monitor can read it; matching query/plan data is retained. Version support alone is not evidence of enabled/configured/populated Query Store. Do not include Query Store enablement/configuration or plan forcing in this migration; that would change scope/state and can alter workload behavior.

## 7. Oracle detail

Potential read-only source is an already loaded cursor through `DBMS_XPLAN.DISPLAY_CURSOR`, joined with `V$SQL`, `V$SQL_PLAN`, and optionally `V$SQL_PLAN_STATISTICS_ALL`. Oracle 11gR2 documentation includes `DISPLAY_CURSOR`; it returns rows of formatted text, not a structured plan object. Calling principal runs with caller privileges. Documented direct fixed-view read needs include `V$SQL_PLAN`, `V$SESSION` and `V$SQL_PLAN_STATISTICS_ALL`; later docs also describe `SELECT` or `READ` access. Grant underlying `SYS.V_$...` objects directly as required, not `SELECT_CATALOG_ROLE`/`DBA`/`SYSDBA`. Package EXECUTE and exact object list must be verified against each supported Oracle family and actual invocation before Permission Registry changes.

Plan key semantics:

- `SQL_ID` identifies a parent SQL cursor; it is not sufficient to identify one child plan.
- One `SQL_ID` can have multiple `CHILD_NUMBER` child cursors due to environment/optimizer/metadata differences.
- `PLAN_HASH_VALUE` identifies a plan shape signature, not an execution instance and not necessarily every semantic difference.
- Therefore return/disambiguate `(SQL_ID, CHILD_NUMBER, PLAN_HASH_VALUE)` and never select an arbitrary first child. A query-level aggregate view can show executions, elapsed time, buffer gets and disk reads for each child, clearly labeled as accumulated cursor statistics.

`DISPLAY_CURSOR(sql_id, child_number, ...)` should receive bound SQL_ID and child number. Do not use omitted arguments that implicitly choose the caller's last cursor. Bound formatted output rows/characters before report generation. `ALLSTATS LAST` does not guarantee runtime row-source statistics exist: they depend on statistics having been collected during the original execution (for example, `GATHER_PLAN_STATISTICS` or an appropriate `STATISTICS_LEVEL`). Toolkit must not inject hints or change instance/session settings. Missing runtime statistics means estimated-only plan evidence, not zero actual rows.

Oracle 11gR2 availability is documented, but current toolkit support remains **UNSUPPORTED** because there is no implementation. User-provided exact package/view grants should be generated only after exact fixed SQL is approved; include shared login/version dependencies (`CREATE SESSION`, `SYS.V_$VERSION`, `SYS.V_$INSTANCE` and other actual preflight dependencies) in the full feature contract. Grants remain text only.

## 8. PostgreSQL detail

Standard `pg_stat_activity` exposes current query/activity and limited backend information. Ordinary users generally see complete details for their own sessions; `pg_monitor`/`pg_read_all_stats` grants broader monitoring visibility, but that view does not expose a complete execution plan for another backend by PID. Thus the answer to “can a monitor use only another PID and standard built-in read-only views to retrieve that backend's full currently executing plan?” is **NO**.

`pg_stat_statements` (if installed/configured) can provide query IDs, calls, planning/execution aggregate timings and block counters. It is not a stored plan tree. It requires preload/configuration and extension availability; visible query text/statistics are permission-sensitive. Treat it as optional slow-query evidence, not plan support.

`EXPLAIN` parses/plans supplied statement text and can incur planning work; this is not reading a plan already used by another backend. `EXPLAIN ANALYZE` executes the statement and can produce side effects, locks and resource use. Hard rule: **Toolkit shall not execute EXPLAIN ANALYZE against arbitrary application SQL.**

`auto_explain` can log plans for statements when already preloaded/configured; it has overhead, writes logs that can include SQL/parameters, and requires server/session configuration. V0.9.1 must not enable it, edit `shared_preload_libraries`, alter settings, or install an extension. A future independent feature may read an already-existing, access-controlled external log and must separately address retention, parameter redaction and ingestion bounds.

Current Toolkit status: **slow/activity statistics may be available; plan = UNSUPPORTED**. Do not label `pg_stat_activity.query` or pg_stat_statements aggregates as plans.

## 9. MySQL detail

MySQL Performance Schema current/history/digest tables can provide statement text/digest, execution timing and counters when instrumentation/history is enabled and retained. They are not complete execution plan trees.

`EXPLAIN FOR CONNECTION <id>` is a candidate existing-plan path for a currently executing explainable statement in documented MySQL releases. It does not mean arbitrary statement replay is safe, and target statements must be supported by the server's EXPLAIN implementation. Cross-user visibility requires appropriate PROCESS privilege; ordinary EXPLAIN also has object/view privilege constraints. The current Toolkit has no plan route or contract, so it remains unsupported until exact version/minor support, statement classes, privilege scope and session race behavior are verified. Never synthesize `EXPLAIN <captured SQL>` by concatenation.

MySQL 5.7 has regular EXPLAIN and named-connection plan behavior in its reference manual; do not infer newer JSON formats or EXPLAIN ANALYZE support. MySQL 8.0 capabilities vary by minor release: `EXPLAIN ANALYZE` appeared in 8.0.18 and executes the statement, so it is outside the permitted Toolkit contract. MySQL 8.4 also has executing EXPLAIN ANALYZE; prohibited. Establish exact minor-version capability from server full version and static reviewed variants; unsupported/unknown versions must not inherit latest syntax.

Current Toolkit status: **statement statistics may be available; plan = UNSUPPORTED**. A later read-only named-connection prototype may be considered only with strict target session binding, PROCESS permission review, statement-type tests, short timeout, bounded plan rows/bytes, and clear “currently running only” semantics.

## 10. Permission model and registry consistency

Full Plan readiness must mean all of: login/connect, common preflight, version detection, required Plan permission for the resolved engine/version, SQL Registry variant permission, and parser/output contract. `READY` must never be derived from a generic permission list while the executable SQL path requires a different permission.

For SQL Server, route by verified full/family version. SQL Server 2008 R2–2019 plan DMVs use `VIEW SERVER STATE`; SQL Server 2022+ relevant plan/request DMVs use `VIEW SERVER PERFORMANCE STATE`. `SHOWPLAN` is not a replacement and should not be granted for the existing cached DMV route. No admin roles or `CONTROL SERVER` should be generated.

For future Oracle Plan support, use direct `SYS.V_$...` object privileges only, based on exact Registry SQL and `DBMS_XPLAN` invocation. Avoid broad catalog roles. Verify principal identity and include shared login/version/preflight dependencies.

For PostgreSQL and MySQL, a supported statistics permission does not mean Plan permission/readiness. Keep Feature status UNSUPPORTED until a real plan source is implemented.

Static consistency gate for a future migration: every Plan SQL object/DMF, role, permission, version variant and parser contract must appear in the resolved feature profile; `dba validate` should fail for missing route/permission/parser metadata. Registry, Permission Registry and Preflight must have one version-consistent evidence path.

## 11. Security and privacy review

| Threat | Current mitigation | Remaining concern / required control |
|---|---|---|
| Arbitrary target SQL execution | Plan path only accepts fixed signed Registry scripts; target SQL is not submitted | Keep a closed dispatch, no raw SQL CLI arg, no dynamic SQL text composition |
| Database state change | Registry plan queries are SELECT/DMF reads; no SHOWPLAN/EXPLAIN ANALYZE | Future sources must be reviewed for side effects before approval |
| High privilege | Intended monitoring account; plan should need version-appropriate DMV permission | Current SQL Server 2022 registry mismatch may lead to confusing readiness/failure; don't grant admin roles |
| Denial/resource pressure | timeout, max rows, XML size and XML node/operator limits | DMV cache/XML extraction still consumes resources; no bulk plan crawl; retain hard output budget |
| SQL/session misassociation | active request exact session filter; cached fallback checks SQL handle equality | Session reuse/race; SQL handle alone does not prove request/statement identity; report ambiguity |
| SQL literal / business info leakage | parser applies `redact()` to XML attributes/text; HTML omits raw XML; `.sqlplan` ignored by Git | Generic redaction cannot prove all literals anonymized. Treat `.sqlplan` and SQL text as sensitive, local, bounded evidence; avoid logs/telemetry/upload |
| HTML injection | shared report renderer escapes database text; human report uses scenario fields | Preserve escaping tests for plan predicates/object names |
| Parser abuse | XML DTD/entity reject; size, node and operator limits | Add adversarial fuzz/timeout coverage before expanding format support |
| Stale/missing plan | explicit unavailable messages; no re-execution fallback | Use `PLAN_NOT_AVAILABLE` semantics and retain source/observation time |

SQL Server plans can include statement text, object/schema/database names, predicates, parameter values, and environmental metadata. Existing parser redaction handles known sensitive patterns rather than proving all literal values absent. The XML file is intentionally a raw-ish local artifact and should be access-controlled like a diagnostic report containing SQL. Do not put it in tracked files, logs, email, or shared reports by default.

## 12. Identity, confidence and Slow linkage

Plan identity strength should be explicit:

1. **Strongest practical source identity:** database/server identity + current request/session + `plan_handle` + statement offsets + observation time.
2. **Cached SQL Server plan:** database/server identity + `plan_handle`, plus `sql_handle` and statement offsets when available.
3. **Oracle cursor:** database identity + `SQL_ID` + `CHILD_NUMBER` + `PLAN_HASH_VALUE`, plus capture time and cursor statistics.
4. **PostgreSQL query statistics:** `queryid` and database/user/context; not a plan identifier.
5. **MySQL current connection:** connection/thread id plus observation time and statement identity; only current explainable request.

Slow-to-plan linking remains an explicit DBA action. The slow report's session may finish, change statement or be reused before `plan --session` runs. On collection, show actual observed session, SQL identifier/handle, plan source and capture time; compare statement/query identity with the slow evidence if available. If it differs or is not comparable, mark `AMBIGUOUS`/`UNKNOWN`, not a confident link. Do not automatically fetch plans for every slow row.

## 13. Resource, output and parser contract

Each profile should define query timeout, max rows, maximum SQL text bytes, maximum plan bytes, XML/row count limits, parser version/contract, and report output budget. On any cap: mark `truncated` or `unavailable` with reason; do not silently clip then analyze partial XML as complete. Timeout/permission/vendor failures must preserve safe structured error type and evidence, without exposing credential-bearing DSNs.

Plan analysis must stay evidence-based. A scan, nested loop, hash join, missing-index hint or estimated cost is not automatically a defect. Actual-vs-estimated row claims require real actual row statistics. Show source, limitations and confidence in every finding; recommendations are advice only, never executable tuning actions.

## 14. CLI/product scope

Keep `dba diagnose <database_id> plan` as a feature under the existing diagnosis entry point. Do not invent a cross-database low-level identifier argument that obscures engine semantics. SQL Server `--session` may remain as the explicit target for that engine; future Oracle should use SQL_ID plus child selection, and MySQL a current connection id only if the operator explicitly opts into it. A profile must determine valid identifier type/range.

Display one plan at a time by explicit request. Slow diagnosis can provide a next-step command, but not bulk-capture XML. Plan output must label: source (active/cached/cursor/log), identifier, source timestamp, size, availability, runtime stats available/absent, confidence and limitations.

## 15. Current tests: coverage and gaps

Existing `tests/test_execution_plan.py` covers:

- XML parsing, missing fields/unknown operators, DTD/entity rejection, malformed XML, and size/node bounds;
- selected plan findings, actual-vs-estimated mismatch only when ActualRows exist, and report redaction behavior for a known marked secret;
- bound Registry parameters, cache fallback, stale SQL-handle mismatch not being misattributed;
- unavailable plan/session and invalid session id;
- CLI HTML/JSON/report behavior and `.sqlplan` output not embedding XML in HTML;
- SQL Server only behavior and configuration bounds.

Documented integration record `docs/VERIFICATION_V0_4.md` reports SQL Server 2022 Docker plan retrieval for a small fixture, with cached plans lacking ActualRows. README explicitly says SQL Server 2008 R2 is designed but not real-instance validated.

Gaps before a migrated Plan route can be accepted:

- SQL Server 2022 permission readiness/required privilege and profile gate;
- version mismatch/unverified/unknown stop behavior for Plan;
- no 2008 R2/2012/2014/2016/2017/2019/2025 version-specific integration evidence;
- deterministic behavior on multiple cache rows/tied `last_execution_time` and multiple plans per SQL handle;
- statement offset and request/session reuse linkage confidence;
- permission denied, timeout, cache eviction and vendor-error semantics in end-to-end path;
- truncation distinction versus hard oversize rejection and output/report size boundary;
- broad literal/parameter redaction tests (current test uses a recognizable marked secret, not arbitrary business literals);
- no tests for Oracle multiple children/statistics absent, PG plan unsupported contract, or MySQL minor-specific named-connection behavior.

No tests were modified in this audit.

## 16. Risk register

| ID | Level | Finding | Required disposition |
|---|---|---|---|
| R1 | HIGH | Plan diagnose bypasses the version-aware permission helper/profile gate. Its generic definition says VIEW SERVER STATE required; the helper can translate this for SQL Server 2022+, but the execution path does not consume that resolved requirement | Bind per-version required privilege into Plan profile and use it for readiness, execution gate and failure explanation |
| R2 | HIGH | Plan CLI bypasses DiagnosticProfileResolver; version binding/SQL/permission/parser are not atomically resolved | Migrate route through existing resolver; VERIFIED only; stop on MISMATCH/UNVERIFIED/UNKNOWN |
| R3 | MEDIUM | Cached `TOP 1` ordering and SQL-handle identity do not prove exact statement/slow-query correspondence | Add deterministic selection or explicit ambiguity; include plan_handle/offset/time evidence |
| R4 | MEDIUM | Raw `.sqlplan` and SQL text can retain business constants/object names despite marked-value redaction | Keep local sensitive artifact; test realistic literal handling; never claim complete anonymization |
| R5 | MEDIUM | Slow report to later plan call has session/request race and session reuse risk | Revalidate identity; report ambiguity; no automatic bulk fetch |
| R6 | MEDIUM | Historic versions are design targets more broadly than real integration coverage | Label evidence level per family and require variants/tests before support claim |
| R7 | LOW | Parser/analyzer synthetic coverage can miss vendor XML structure changes | Maintain fixture set from supported major families, bounded and sanitized |

No BLOCKER was found for the principle of a read-only SQL Server Plan route, but R1 and R2 are release acceptance blockers for claiming a compliant V0.9.1 version-aware route.

## 17. Product alternatives

### A. Four engines all automatically support Plan

**Reject for V0.9.1.** It pressures the product to submit SQL to EXPLAIN/ANALYZE, broadens privileges and creates inconsistent meanings between cached, estimated and executed plans. PostgreSQL cannot retrieve a peer backend's complete current plan from standard built-in views by PID alone. MySQL named connection is restricted to active/explainable requests. Oracle has multi-child cursor ambiguity and requires careful direct grants.

### B. Support only existing, safe Plan sources

**Recommend.** Close SQL Server's current feature through version-aware profiles first. Add Oracle only after direct-grant, child cursor, output-bound and runtime-stat semantics are validated. Consider MySQL named-connection read as a narrowly scoped current-request feature. Keep PostgreSQL plan unsupported unless a pre-existing log/capture source is separately designed. No re-execution.

### C. Defer all Plan work and retain Slow evidence only

Safest minimum and avoids current gaps, but discards an existing SQL Server 2022 validated read path. Suitable if Phase P-A cannot close permission and resolver gates before release. Not the preferred product choice if the work is scoped to fixing those two issues.

## 18. Recommended architecture and migration

Reuse `DiagnosticProfileResolver`, `ResolvedDiagnosticProfile`, SQL Registry, Permission Registry, Preflight and existing parser contract. Do not create a parallel plan resolver/capability graph. A resolved profile should bind, atomically:

```text
database type + verified full/family version
→ static reviewed Plan SQL variant
→ required/optional privileges
→ parser contract and bounds
→ support state
```

Plan execution is allowed only for a `VERIFIED` profile. MISMATCH, UNVERIFIED, UNKNOWN or unsupported family stops before Plan SQL execution. Offline reference permission generation may use declared family but must state it is not runtime readiness.

### Phase P-A — Safety and contract closure

- Correct SQL Server 2022+ plan permissions; keep older family requirement separate.
- Route Plan through existing resolver and stop on non-VERIFIED state.
- Ensure `dba validate` verifies SQL, permission and parser/profile binding.
- Define plan source/identity/confidence and missing/oversize/permission semantics; no SQL execution path.

### Phase P-B — SQL Server version-aware migration

- Add reviewed static variants only where SQL Server version/column capabilities require them; do not invent broad compatibility.
- Bound plan/text/result and preserve current no-reexecution behavior.
- Add tests for version gates, privilege failure, ambiguous cache selection, session reuse, timeout and redaction.
- Run SQL Server 2022 real E2E; older versions remain UNKNOWN until tested.

### Phase P-C — Oracle cached cursor feasibility

- Separate design/permission review before code.
- Validate direct SYS.V_$ privileges, DBMS_XPLAN access, full SQL_ID/CHILD_NUMBER/PLAN_HASH_VALUE handling, child ambiguity, textual output bounds and whether runtime statistics exist.
- If the profile cannot prove identity or privileges, return UNSUPPORTED/PARTIAL, not guessed data.

### Phase P-D — Other engines

- PostgreSQL: keep plan UNSUPPORTED; optionally assess pre-existing auto_explain log ingestion separately, without enabling/configuring it.
- MySQL: prototype only `EXPLAIN FOR CONNECTION` for exact verified versions/minors and supported active statement kinds; never append captured SQL to EXPLAIN.
- SQL Server Query Store: separate historical source feature after enablement/access/data states and retention behavior are modeled; no configuration or forcing.

### Phase P-E — Validation

- Registry/Permission/Preflight/Profile consistency validation.
- Unit, SQL Registry safety, version mismatch/unknown, parser/resource/privacy, and CLI tests.
- Integration on each claimed family where available; explicitly retain `UNKNOWN` where not tested.
- Real SQL Server 2022 E2E; tests must not generate unsafe workload or change production config.

## 19. Definition of done

V0.9.1 Plan migration is acceptable when:

1. Plan commands execute only from a verified resolved profile and reviewed Registry script.
2. SQL Server 2022 requires the right performance-state permission; old/new version paths do not bleed together.
3. No application SQL is executed, recompiled via arbitrary EXPLAIN, or re-run after cache miss.
4. Active/cached source, identifiers, time and confidence are reported; ambiguity is not hidden.
5. Cache miss, permission denial, unsupported/unverified version, timeout, oversized plan and truncation have explicit outcomes.
6. SQL/XML/HTML output is bounded and treated as sensitive; no credentials or DSN secrets are emitted.
7. `dba validate` checks profile/SQL/permissions/parser alignment.
8. Test evidence levels are recorded per supported version; no broad compatibility claim from one SQL Server 2022 run.

## 20. External evidence

Primary documentation consulted (version-specific claims should be rechecked when implementation selects its exact variants):

- Microsoft, [`sys.dm_exec_query_plan`](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-exec-query-plan-transact-sql?view=sql-server-ver17) — older permission versus SQL Server 2022+ VIEW SERVER PERFORMANCE STATE; plan handle and Showplan behavior.
- Microsoft, [`sys.dm_exec_query_stats`](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-query-stats-transact-sql?view=sql-server-ver16) — cached completed-query stats, cache lifetime and version-specific access.
- Microsoft, [`sys.dm_exec_requests`](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-requests-transact-sql?view=sql-server-ver17) — active request visibility and version permissions.
- Microsoft, [`SET SHOWPLAN_XML`](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-showplan-xml-transact-sql) — separate permission/use model; does not execute supplied statements while enabled, but parses/plans submitted statements and requires object permissions.
- Microsoft, [Query Store monitoring](https://learn.microsoft.com/sql/relational-databases/performance/monitoring-performance-by-using-the-query-store) and [Query Store management](https://learn.microsoft.com/en-us/sql/relational-databases/performance/manage-the-query-store?tabs=ssms&view=sql-server-ver16) — availability, state, retention and configuration distinctions.
- Oracle 11gR2, [DBMS_XPLAN package reference](https://docs.oracle.com/cd/E16338_01/appdev.112/b56262/d_xplan.htm) — DISPLAY_CURSOR availability and fixed-view access requirements.
- Oracle, [DBMS_XPLAN 12.2 reference](https://docs.oracle.com/en/database/oracle/oracle-database/12.2/arpls/DBMS_XPLAN.html) and [21c plan generation guide](https://docs.oracle.com/en/database/oracle/oracle-database/21/tgsql/generating-and-displaying-execution-plans.html) — caller privilege model, cursor plans and output semantics.
- Oracle, [V$SQL_PLAN reference](https://docs.oracle.com/en/database/oracle/oracle-database/26/refrn/V-SQL_PLAN.html), [V$SQL reference](https://docs.oracle.com/en/database/oracle/oracle-database/26/refrn/V-SQL.html), [cursor sharing](https://docs.oracle.com/en/database/oracle/oracle-database/26/tgsql/improving-rwp-cursor-sharing.html) — child cursors and plan identifiers.
- PostgreSQL 18, [`EXPLAIN`](https://www.postgresql.org/docs/18/sql-explain.html), [runtime statistics](https://www.postgresql.org/docs/18/monitoring-stats.html), [`pg_stat_statements`](https://www.postgresql.org/docs/18/pgstatstatements.html), [`auto_explain`](https://www.postgresql.org/docs/18/auto-explain.html) — execution behavior, stats visibility, aggregate scope, preload/configuration and overhead.
- MySQL 5.7, [`EXPLAIN FOR CONNECTION` 5.7 release note](https://dev.mysql.com/doc/relnotes/mysql/5.7/en/news-5-7-2.html), MySQL 8.0 [named-connection plan](https://dev.mysql.com/doc/refman/8.0/en/explain-for-connection.html), MySQL 8.4 [EXPLAIN](https://dev.mysql.com/doc/refman/8.4/en/explain.html) and [Performance Schema statement tables](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-statement-tables.html) — current connection plans, statement constraints and telemetry.

## 21. Open questions for implementation planning

1. Should SQL Server 2022 plan permission be a standalone required grant in Permission Registry, or inherited from another feature contract while still displayed explicitly? It should be standalone in the resolved Plan feature either way.
2. Does the current 2022 test environment have enough safe sessions to verify active request versus cached fallback without manufacturing production load?
3. Should default human CLI show raw SQL literals at all, or only a normalized/redacted statement summary while full text remains opt-in local report evidence?
4. What per-version integration environments are available for SQL Server 2008 R2–2019, Oracle 11gR2+, and MySQL 5.7/8.x? Until available, keep unsupported/unknown validation labels.
5. Is Query Store historical retrieval a separate later feature, subject to DBA-controlled enablement and no plan forcing? Recommendation: yes.

## 22. Final decision

**Decision: `APPROVE_WITH_CHANGES`**
**Can implementation start: `YES`**

Implementation may begin with Phase P-A, provided the SQL Server 2022 permission mismatch and bypassed version resolver are fixed first. No other engine should be marked Plan-supported in this implementation until a static, version-verified source and permissions are approved. If those gates cannot be closed, use option C for the release and preserve Plan as unavailable rather than claim READY.

This review did not modify production Python, SQL, Inventory, Permission Registry, database state, tests or Git history. No tests were run because the permitted deliverable is an audit design document only.

## 23. Post-audit implementation status (V0.9.1 SQL Server Plan migration)

This section supplements the original audit decision; it does not revise the audit's findings or approval conclusion.

- SQL Server Plan routes now pass through `DiagnosticProfileResolver` and bind the reviewed SQL profile, permission profile, parser contract, runtime variant, and support state in one immutable resolved profile. Collector construction and execution require a matching declared/detected version (`VERIFIED`) and a supported route.
- SQL Server 2008 R2, 2012, 2014, 2016, 2017, and 2019 share the existing legacy SQL/parser profile and require `VIEW SERVER STATE`. SQL Server 2022 shares that SQL/parser profile and requires `VIEW SERVER PERFORMANCE STATE`. SQL Server 2025 remains `UNKNOWN` without a fallback.
- Plan Registry SQL is marked read-only/reviewed and explicitly applicable to the supported families. Active and cached samples include SQL/plan handles, statement offsets, and query hashes. Multiple/truncated row results are ambiguous; a missing cached plan remains unavailable. The Toolkit does not submit or re-execute captured application SQL.
- Local verification: targeted Plan/version/permission tests passed (111 tests); Ruff, strict mypy, and `dba validate` passed. Full-suite and tracked-file secret-scan results are recorded in the V0.9.1 delivery report.
- Live SQL Server 2022 checks were attempted but did not reach the database: the encrypted Credential Store session was locked and the noninteractive process reported that an interactive terminal is required to unlock it. No Plan query or database mutation was executed during this attempt. SQL Server 2022 real E2E remains pending a locally unlocked credential session.

## 24. Post-commit SQL Server 2022 system verification

After the original migration commit was pushed, the user unlocked the local credential session. The sandboxed process could not access the current-user named pipe, so the authorized read-only checks were rerun under the same Windows user context outside the sandbox; current-user pipe ACL and peer verification were not changed.

- Target: Inventory asset `sqlserver2022`, `127.0.0.1:1433/master`; detected SQL Server `16.0.4305.5`; Python 3.12.14, pyodbc 5.3.0, ODBC Driver 17 fallback.
- `dba check sqlserver2022`: connection and version verification succeeded; SQL Server Plan resolved to `SUPPORTED` / `VERIFIED`; Plan permission readiness was `READY` with `VIEW SERVER PERFORMANCE STATE`.
- `dba permissions sqlserver2022 --feature plan`: `READY`; required permission was satisfied; no grant was generated or executed.
- `dba collect sqlserver2022 active-sessions`: succeeded and returned one visible session, SQLServerCEIP session 53. No application test session was present.
- `dba diagnose sqlserver2022 plan --session 53`: executed through the versioned Plan path and returned `PLAN_NOT_AVAILABLE` with `UNKNOWN` confidence because no existing plan evidence was available for that service session. No report or plan artifact was saved, and no application SQL was submitted.
- A follow-up CLI semantics fix treats this successful probe/cache miss as a completed command; `success=true` is separate from `plan_available=false` and `plan_status=PLAN_NOT_AVAILABLE`.
- All database calls in this verification were read-only. The prior full unit suite and static gates remain the recorded results; this documentation-only follow-up does not change code.

This is a successful SQL Server 2022 connection/profile/permission/CLI system check, but it does not establish that a representative application query plan was available. Old SQL Server families remain unverified on live instances.
