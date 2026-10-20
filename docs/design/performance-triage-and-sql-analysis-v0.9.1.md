# DBA Toolkit V0.9.1 — 性能分诊与 SQL 深度诊断详细设计

**性质：** 只读设计与源码核对；本文不代表新功能已经实现。
**代码版本：** 0.9.0
**设计日期：** 2026-10-19

> **版本范围决策（2026-10-19）：** 本文中的 PostgreSQL 13–18 等“目标范围”措辞是本设计快照的历史记录，不是当前正式产品目标。当前目标以 `docs/roadmap/DBA_TOOLKIT_V0.9.1_ROADMAP.md` 为准：MySQL 5.7/8.0+、Oracle 11gR2+、PostgreSQL 14+、SQL Server 2008 R2+；PG13 现有兼容代码保留但不构成正式支持声明。MongoDB/Redis 4.0+ 属长期目标，尚无诊断 Profile/能力声明。目标不等于实现或实机验证。

## 1. 结论与范围

建议沿用现有 Credential、Inventory、SQL Registry、Version-Aware Diagnostic Profile、Permission Registry、Preflight、CollectorResult 和离线报告路径，逐步补充性能证据。默认诊断应是一组有界的单次快照；只有需要计算累计计数器速率时，才由 DBA 显式选择两次短窗口采样。单次快照不应被包装成趋势，也不应让“没有观测到”变成“数据库健康”。

当前可复用的核心是四库活动会话、不同程度的活动 SQL、事务和阻塞分析，以及 SQL Server 已有计划读取能力。当前不具备通用 Top SQL、跨库统一 CPU/IO 归因、SQL Server 聚合 wait stats 采集；PostgreSQL、MySQL、Oracle 的 Toolkit 执行计划采集也未实现。不能以统计视图可查询、SQL_ID 可见或计划 Hash 存在，推断相应深度诊断已经完成。

本文的状态含义：

| 状态 | 含义 |
|---|---|
| IMPLEMENTED | 当前代码有数据路径、受控 SQL/分析逻辑和对应测试；不自动代表目标版本均实机验证。 |
| PARTIAL | 只有部分字段、部分数据库/版本、可选或受配置限制的证据；结论需明确边界。 |
| MISSING | 当前没有对应 Toolkit 数据路径。 |
| UNVERIFIED | 代码可能存在或设计目标覆盖，但没有足够测试/真实环境证据证明当前环境可用。 |

## 2. 当前代码基线与证据

### 2.1 现有验收证据的时间边界

仓库最近的四库汇总系统验收文档是 [docs/audits/test-report-v0.9.0.md](../audits/test-report-v0.9.0.md)，记录 SQL Server 2022、PostgreSQL 16.15、MySQL 8.4.11 的连接、Collectors 与部分诊断结果，以及 Oracle Free 因 ORA-01045 缺少 CREATE SESSION 而未完成查询链路。该报告不是 2026-10-19 的新一轮实机保证。

此后仓库有 PostgreSQL P0.1、Oracle P0.1/P0.2、MySQL SQL 历史和应急诊断修复提交/设计记录。[docs/VERSION_PROFILES.md](../VERSION_PROFILES.md) 也包含 SQL Server 2022、PG16、MySQL 8.4、Oracle 26ai 的阶段性验证说明。但这些材料的范围不同：Oracle 的连接/版本/权限门槛验证不能替代 Oracle Collector、Slow、SQL 统计或计划的 E2E；MySQL 最近 SQL 记录也不能代替 digest Top SQL 验收。本文对后续提交影响到的路径标为“实现存在，修复后实机状态需复核”，不把旧汇总报告或对话中的结果冒充为当前可复现证据。

### 2.2 能力矩阵

矩阵状态针对 Toolkit 当前实现，而非数据库产品是否有相应系统视图。

| 能力 | SQL Server | PostgreSQL | MySQL | Oracle | 源码与测试依据 |
|---|---|---|---|---|---|
| 活动会话/来源 | IMPLEMENTED：login、host、program、client address、状态及请求时间；旧版另有兼容 SQL | IMPLEMENTED：pg_stat_activity 的 PID、用户、数据库、客户端、state、query 与时间 | IMPLEMENTED：processlist/Performance Schema thread 映射，字段可见性依版本和权限降级 | IMPLEMENTED：V$SESSION，含 SID/SERIAL#、SQL_ID/child、用户、machine、program/module | sql/[database]/active_sessions*.sql；dba/*_collectors.py；tests/test_slow.py、test_mysql.py、test_postgresql.py、test_oracle.py |
| 活动请求/当前 SQL | IMPLEMENTED：sys.dm_exec_requests，当前请求 elapsed/CPU/reads/waits/blocker 和 SQL text | PARTIAL：由活动 backend/query 和 long-running SQL 表达，没有独立同构 active-requests Collector，也没有 per-query CPU/IO | IMPLEMENTED/PARTIAL：events_statements_current；wait event consumer 缺失时可走降级查询 | IMPLEMENTED/PARTIAL：会话与 V$SQL 子游标关联；当前调用时长与游标累计统计必须区分 | sql/sqlserver/active_requests.sql、sql/postgresql/active_sessions.sql、sql/mysql/active_requests*.sql、sql/oracle/active_requests.sql；Slow profile 和各库 Collector 测试 |
| 锁、阻塞链、根阻塞 | IMPLEMENTED：requests/blocking evidence 构图；不完整/循环数据降级 | IMPLEMENTED：pg_locks + pg_stat_activity 路径；PID 身份仍需 backend_start 复核 | IMPLEMENTED：InnoDB data lock/wait 与 transaction 关联；wait detail 可缺失 | IMPLEMENTED/PARTIAL：V$SESSION blocker 与可选 V$LOCK 等补充；RAC/权限/版本可降级 | dba/slow.py、postgresql_slow.py、mysql_slow.py、oracle_slow.py；sql/[database]/blocking*.sql；tests/test_slow.py、test_mysql.py、test_postgresql.py、test_oracle.py |
| 未提交/空闲事务 | IMPLEMENTED：事务 DMV 佐证并关联会话 | IMPLEMENTED/PARTIAL：xact_start/idle-in-transaction 等，不同 PG 版本可见性需 profile | IMPLEMENTED：InnoDB transaction 关联 processlist connection ID；不能仅凭 Sleep 推断 | IMPLEMENTED/PARTIAL：V$TRANSACTION 关联会话；身份/等待视图缺失时降级 | sql/[database]/transactions.sql 或对应 profile；四库 Slow analyzer/tests |
| 当前慢 SQL | IMPLEMENTED：当前 request 运行时长是采样时在执行的请求 | PARTIAL：当前 backend query age 与阈值筛选；不是已结束 SQL 的排名 | PARTIAL：当前 statements + blocker 定向的最近历史；consumer/权限/历史保留影响结果 | PARTIAL：当前会话、SQL_ID/child 与游标统计；执行耗时与累计游标耗时不是同一口径 | 四库 slow_profiles.py 与 sql/[database]/{active_requests,long_running,previous_sql,recent_statements}* |
| 通用 Top SQL | MISSING：没有跨指标、可配置维度的 Top SQL Collector；已有计划缓存路径不等价 | MISSING：没有 pg_stat_statements Profile/Collector | MISSING：没有 events_statements_summary_by_digest Profile/Collector；recent history 不是 digest Top SQL | PARTIAL：当前查询可能携带所关联子游标的累计字段，但没有 SQL_ID/child 级 Top SQL 工作流 | 源码/Registry 清单未发现四库通用 Top SQL 路径；tests/test_mysql_recent_statements.py 测的是有限近期历史，不是汇总排名 |
| SQL CPU/逻辑读/物理读 | PARTIAL：当前 request 提供 CPU、logical reads、reads；缓存查询统计不是完整 Top SQL 流程 | MISSING：当前 pg_stat_activity 不提供 per-query CPU、logical/physical reads | PARTIAL：当前 statement 有 rows examined 等字段；没有 SQL CPU，rows examined 不等于物理读 | PARTIAL：V$SQL 有 CPU、buffer gets、disk reads 等缓存子游标累计值；非单次执行值 | 四库 active request SQL；sql/oracle/active_requests.sql；相关 Collector tests |
| 执行次数/每次均值 | PARTIAL：缓存统计路径可有执行计数，但无统一排名/差值合同；单条当前请求不能代表频率 | MISSING：未接入 pg_stat_statements | MISSING：未接入 digest summary 的计数/计时差值 | PARTIAL：Oracle 子游标统计有 executions 等累计项，可计算累计以来均值但不等于窗口均值 | sql/sqlserver/plan_cached.sql、sql/oracle/active_requests.sql；计划/Collector tests；暂无跨库 Top SQL 测试 |
| 等待事件 | PARTIAL：当前 request wait type/time；未采 sys.dm_os_wait_stats 聚合视图 | IMPLEMENTED/PARTIAL：当前 backend wait_event/type；没有独立的累计 wait Top SQL | PARTIAL：可选 Performance Schema wait detail；consumer/权限缺失时保持 partial | PARTIAL：会话当前 event/wait class；不是 SQL 级历史等待分布 | sql/[database]/active_requests.sql、active_sessions.sql、wait variants；应急 E2E 与 Fake tests |
| 缓存/执行计划 | IMPLEMENTED：plan_request 和 plan_cached 只读 Registry，Profile 版本路由，受限 XML parser；缓存淘汰/会话关联限制明确 | MISSING | MISSING | MISSING：已有 SQL_ID/child 与 SQL text 不等于 V$SQL_PLAN/DBMS_XPLAN 计划采集 | dba/execution_plan.py、execution_plan_profiles.py、versioning.py::_sqlserver_plan_binding、sql/sqlserver/plan_*.sql；tests/test_execution_plan.py、tests/integration/verify_plan_sqlserver.py |
| SQL 来源归因 | IMPLEMENTED/PARTIAL：当前会话 login/host/app/client address；代理/NAT 和连接池会影响业务用户归因 | IMPLEMENTED/PARTIAL：user/client/app；客户端地址可能隐藏，连接池身份不等同终端用户 | IMPLEMENTED/PARTIAL：user/host/db/program 可见性依赖 processlist/PS；host 不等于真实业务调用者 | PARTIAL：username/machine/program/module/client identifier 字段；client IP 与中间层来源不一定齐全 | 各 active_sessions.sql；Presenter 中 identity/quality 描述；四库 Slow tests |
| 实例资源压力 | PARTIAL：当前 request 与 disk-growth 的空间/TempDB 线索；没有整体 CPU、内存、I/O OS 健康模型 | PARTIAL：pg_stat_database/replication/disk 当前状态；无 OS CPU、磁盘使用率和完整内存视图 | PARTIAL：Performance Schema、InnoDB、binlog/空间线索；没有 OS 资源探针 | PARTIAL：当前会话/等待、FRA/redo/temp 等；没有许可无关的全实例 OS 资源诊断 | dba/disk_growth_profiles.py、dba/*_slow.py、disk Registry；tests/test_disk_growth.py；各库无统一资源 Collector |

**计划能力补充：** 旧的 docs/design/execution-plan-capability-safety-audit-v0.9.1.md 有早期结论“Plan 尚未纳入 Resolver”；当前 dba/cli.py 已解析 plan profile，dba/versioning.py 已有 SQL Server plan family binding。以当前代码为准，并把旧文档视为待单独校正的文档漂移。SQL Server 2008 R2+ 和其他声明版本仍是目标范围，不是全版本实机证明。

### 2.3 现有实现的主要边界

- Slow 诊断已能按证据识别阻塞根节点、影响会话、长事务候选和数据库专属 SQL；证据不完整会降级。它不是统一指标平台，也不具备统一 3–5 个跨类型核查对象排名。
- SQL Server 缓存计划是服务器已有的编译计划，通常不包含实际行数；缓存可能淘汰。计划可能来自当前 request 或当前会话最近 SQL handle，后者不保证和此前慢 SQL 样本是同一次执行。
- MySQL recent_statements 是有限事件历史，按已知 blocker 目标采集，不是整个实例 digest Top SQL。
- Oracle 当前 SQL/子游标统计能提供有用累计指标，但字段含义是 cursor 自加载后的累计计数；不能当作当前调用独占成本。
- 本地 Mock/Fake、静态 Registry 检查和 Docker 集成测试需分别标注。只有明确执行 CLI→Credential→Adapter→Registry→Profile→Collector→Analyzer→Presenter/Report 的 E2E，才算对应链路的实机验收。

## 3. 性能快速分诊架构

### 3.1 建议工作流

1. **解析资产和运行时：** 只使用 DBA 指定的 database_id；检查本地驱动、Credential、版本/Profile 和 Permission Readiness，不扫描 Inventory。
2. **核心快照：** 执行每个数据库版本 Profile 明确列出的少量必需 SQL，优先活动会话/请求、阻塞关系、事务关联；SQL 必须来自 READ_ONLY 且 reviewed=true Registry，并设置查询超时、最大行数和数据大小上限。
3. **先给分诊摘要：** 核心证据返回后输出事实、时间、完整度和首要核查方向；没有阻塞只表示采样时未观察到已确认阻塞。
4. **按证据选择后续检查：** 仅当结果支持进一步检查时，再显式调用 Top SQL、plan、disk-growth、replication 或 SQL 深度分析。不要默认把所有昂贵查询塞入 quick triage。
5. **可选双采样：** 只有选择窗口速率分析时，执行被 Profile 声明为 delta-safe 的累计统计查询两次。每次独立记录采样时间、状态、身份和窗口；任一端缺失、重置或身份变化时返回 UNKNOWN/PARTIAL。
6. **共享呈现：** Presenter/ReportModel 渲染中文摘要和自包含 HTML；JSON 仍是一个合法结构化对象。各数据库可保留专有字段，不能为凑齐统一表格伪造空值。

### 3.2 六类分诊

| 类别 | 首选证据源 | 权限方向 | 采集成本/版本 | 可以证明 | 不能证明 | 下一步 |
|---|---|---|---|---|---|---|
| 锁阻塞与长事务 | 四库已有活动会话、lock/wait relation、事务视图 | 复用 Slow Profile 的跨会话只读权限 | 已有查询低至中成本；行数受限；旧版视图需 Profile 兼容 | 采样时可见的等待边、可证实根候选、事务存在和身份字段 | 不证明事务是业务根因或回滚耗时；截断/权限缺失时不证明“无阻塞” | Blocking Tree、idle transaction、人工身份复核 |
| 活动请求与连接压力 | SQL Server dm_exec_requests/sessions；PG pg_stat_activity；MySQL processlist/PS；Oracle V$SESSION | 当前各库会话监控能力；按 Profile 明示字段可见性 | 单次小型实例范围采样；繁忙实例限行数和排序成本 | 当前活动请求、连接数线索、请求年龄、可见身份/来源 | 单一连接数不证明接近容量；连接池数量不等于活跃负载；客户端地址会被 NAT/代理覆盖 | 会话分布、连接限制的只读检查和来源方核对 |
| CPU 压力线索 | SQL Server 当前 request CPU 和可选窗口缓存统计；Oracle V$SQL CPU 累计；PG/MySQL 当前无 per-query CPU | 仅请求/缓存统计视图权限 | 当前请求低成本；全缓存汇总可能随缓存规模放大，需单独限时 | SQL 当前/自缓存以来累积 CPU 或符合条件的窗口增量 | 不能证明 CPU 饱和、OS run queue 高，也不能把 CPU 与 wall time 等同 | 高 CPU 候选→有界 Top SQL；OS/监控系统另查 |
| 读写 I/O 线索 | SQL Server 当前 reads/logical_reads；Oracle buffer_gets/disk_reads 累计；MySQL rows_examined 不当物理读；PG 当前无 SQL I/O | 现有只读统计视图的最小权限；PG16+ pg_stat_io 是 PG16+ 对象/数据库级证据 | 逐请求低成本；累计扫描限 Top-N 并实测 | 数据库内部读/等待计数或差值 | logical read 不代表物理盘读；wait 不证明磁盘故障；rows examined 不是磁盘读 | 对照计划、频率、数据量、同窗口 wait；必要时补 OS/存储证据 |
| 内存、临时空间及资源等待 | 当前 wait/event、现有 disk/TempDB/TEMP/Temp/WAL 证据 | 只用已定义对象权限；新视图同步 Permission Registry/Preflight/tests | 明细可能较贵、高基数；先聚合，按需查询、限行数 | 采样期间某类资源事件或容量状态 | 单个 wait/Temp 总量不能定位会话；累计 temp counter 非当前占用；无 OS 数据不判主机内存不足 | 分类命中后再查会话/SQL明细或 disk-growth |
| 其他等待或证据不足 | 各库当前 wait type/event；采集质量、权限、版本、截断元数据 | 当前 Profile | wait 字段通常低成本；扩展/累计视图可能另需授权/配置 | 观测到某类等待，或明确数据不可见/不完整 | 等待名字不等于根因；事件未知不等于无等待；未知权限不等于 READY | 报告 UNKNOWN/PARTIAL 和下一项低风险核查，不作健康评分 |

数据库等待表示引擎记录了等待，不直接等于 OS 资源瓶颈。高 I/O wait 只能启动 I/O 核查；没有阻塞不等于正常；没有 SQL CPU 统计不等于该 SQL 消耗为零。

### 3.3 单次快照与短窗口双采样

**默认模式：单次快照。** 按 Profile 顺序串行查询，复用连接，记录每个 Collector 的开始/结束时间。明确标记 snapshot_kind=single、captured_at 和 best-effort 窗口，不承诺全局原子快照。

**可选模式：双采样差值。** 仅针对明确的累计计数器，并且数据库键和计数器语义有 Profile 声明时启用。下表是需要 DBA 评审的初始预算建议，不是生产 SLA：

| 参数 | 初始建议 | 理由与约束 |
|---|---:|---|
| 样本间隔 | 默认 3 秒，可配置 1–10 秒 | 足以产生短窗变化，同时限制命令停留；过短会放大采样扰动 |
| 单查询超时 | 默认不超过 3 秒，可按环境调小 | 避免计数器查询长期等待；仍受 Adapter/驱动超时语义约束 |
| 每采样最大行数 | 默认 100 行，具体查询可更低 | 控制缓存/摘要读取和报告体积；达到上限则显式 truncated，不宣称完整 Top-N |
| 双采样阶段预算 | 默认 12 秒 | 包含两侧采集及间隔；到期停止可选采样并保留已取得事实 |
| 命令总预算 | 建议 25 秒上限 | 需和连接/查询/Collector timeout 协同；不等于单条 SQL 超时 |

有效速率使用 delta / 实际 elapsed_seconds，并展示两个时间戳和窗口长度。必须按实例/数据库/SQL身份匹配同一计数器；发现 stats_reset、实例重启、统计 epoch 改变、缓存条目消失、计数器回退、Plan/child 变化或身份不一致时，输出 RESET_OR_EVICTED/IDENTITY_CHANGED，不计算负值或零值速率。缺字段/权限不足为 UNKNOWN；不可用计数器不影响其他证据。禁止为确定是否有下一页而运行无界 COUNT。

双采样是短窗口差值，不是历史趋势或因果证明。首期只对 Profile 列出的低成本累计统计做差；活动阻塞/会话快照本身不应被相减或解释为速率。

## 4. Top SQL 和指标语义

### 4.1 证据类型必须保留

| 类型 | 正确语义 | 典型误读 |
|---|---|---|
| 当前请求 | 采样时正在运行的请求/语句；可能含本次 request elapsed、CPU 和等待 | 把运行时间长当成 SQL 总体最贵或根因 |
| 自缓存/进程启动以来累计 | 缓存条目、backend 或实例累计计数；受重启、reset、eviction 影响 | 直接称为当前负载或单次执行消耗 |
| 数据库内置历史统计 | 扩展/Performance Schema 中聚合结果，受配置、重置、容量、淘汰和权限影响 | 当作永久审计历史或完整 workload |
| 短窗口差值 | 两次同身份累计计数的差 / 实测时间 | 当作长期趋势，或忽略重置/条目替换 |
| 单次执行统计 | 明确绑定某次执行的实际耗时/行数/资源 | 用缓存平均值替代本次执行，或从估算计划推实际值 |

每项指标带 metric_semantics 和 window：current_request、since_cache_load、since_stats_reset、sample_delta、single_execution 或 unknown。单位明确：SQL Server cpu_time 为 ms；Oracle V$SQL CPU/elapsed 为微秒，elapsed 是数据库时间，并行时可能大于 wall time；其他单位按相应字段文档确定。不可跨库直接混排。

### 4.2 候选分类，而非单一 elapsed 排序

| 候选类别 | 主要证据/排序 | 语义与限制 |
|---|---|---|
| 长时间运行 SQL | 当前请求 elapsed/query age | 只表示当前仍在执行；锁等待受害者需单独标记 |
| 高频短 SQL | 窗口执行次数 delta、平均/每次耗时（支持时） | 无窗口计数不能仅凭累计次数断言当前频率；均值隐藏长尾 |
| 高累计 CPU SQL | 累计 CPU 与有界窗口 CPU delta | 累计排名反映统计期；无 OS 饱和证据不能称 CPU 瓶颈 |
| 高逻辑读 SQL | logical/buffer gets 与窗口 delta | 数据库内部工作量，不等于物理盘读取 |
| 高物理读 SQL | disk/read counter 与窗口 delta | 引擎定义不同；PG 当前没有 per-query 物理读，MySQL rows_examined 不是磁盘读 |
| 高等待 SQL | 当前等待事件关联当前语句；或带统计区间的等待汇总 | 当前 wait 和累计 wait 不能混为一项 |
| 阻塞受害 SQL | 已确认阻塞图里的受害会话和影响路径 | elapsed 可能很高，但不是 Root Blocker |
| 后台维护 SQL | 已识别 job/program/module/user、命令/对象和来源 | 未知程序不能强制归为业务/后台 |
| 统计不足 SQL | 视图不可见、统计未配置、reset、eviction、截断等 | 保留候选和缺失原因，不补 0、不排为低成本 |

不要用一个跨库分数把 elapsed、reads、wait、执行次数相加。首期建议各类独立 Top-N（默认 N=5，最大值受 Report Policy 限制），按数据库原生口径排序并说明不可比原因。N 与 Collector max_rows 不同，Collector 仍可有更小安全上限。

### 4.3 各库统计来源与边界

| 数据库 | 可调查的数据源 | 当前 Toolkit 状态与设计边界 |
|---|---|---|
| SQL Server | sys.dm_exec_requests（当前）；sys.dm_exec_query_stats + sys.dm_exec_sql_text（缓存聚合，可选窗口）；sys.dm_os_wait_stats（实例累计 wait，可选）；Query Store 仅当已启用且另行设计 | 当前请求已有字段；当前没有 query_stats/wait_stats Top SQL Registry。Query Store 不自动启用；缓存会淘汰。新增查询按版本/权限 Profile 限制，避免扫描大缓存 |
| PostgreSQL | pg_stat_activity（当前）；pg_stat_database（数据库累计）；pg_stat_statements（需 DBA 预先配置）；pg_stat_io（PG16+，实例/对象统计，不是 per-query） | 当前主要是 activity/slow/replication/disk。未接 pg_stat_statements。绝不自动改 shared_preload_libraries、CREATE EXTENSION 或参数；未启用时区分 NOT_CONFIGURED/UNSUPPORTED 与缺权限 |
| MySQL | Performance Schema events_statements_current（当前）；events_statements_summary_by_digest（digest 累计）；statement/wait summary；events_statements_history（有限近期事件） | 当前有 current + blocker 定向 recent history；无 digest Top SQL。依赖 consumers/instruments、容量、权限；不自动启用/调整。reset/容量满需标注 |
| Oracle | V$SQL（每 child cursor 的 CPU/elapsed/buffer gets/disk reads/executions 累计）；V$SQLSTATS（SQL_ID 聚合）；V$SESSION 当前会话/等待；计划和 child 信息需独立 Registry | 当前 active request SQL 有 V$SQL 关联统计，无独立 Top SQL/SQL plan collector。缓存非持久历史；权限用最小直接对象授权。不用 AWR/ASH/ADDM/Tuning Pack 作默认源 |

MySQL digest 容量满、consumer 未启用或统计 reset 会造成覆盖不完整；PG 扩展不存在/未启用需显示配置状态；SQL Server 条目会淘汰；Oracle child cursor 会 aging out。它们都进入 Collection Quality，不能返回“Top SQL 为空”。

## 5. SQL 深度诊断与计划分析

### 5.1 身份和关联键

| 数据库 | 建议关联键 | 必须披露的复用/可见性风险 |
|---|---|---|
| SQL Server | database_id + plan handle/query hash + statement offset + plan generation/cache timestamp；当前执行加 session_id/request start | plan cache eviction；query_hash 可能碰撞；session_id 可复用；handle 不代表一次完整业务调用；缓存计划常无 actual rows |
| PostgreSQL | database OID/name + queryid（仅版本/扩展提供时）+ stats reset epoch + query fingerprint；活动 backend 加 PID/backend_start | PID 复用；queryid 随版本/配置可能不同；pg_stat_statements 可 reset/淘汰；query text 可截断/不可见 |
| MySQL | schema + digest/digest_text + Performance Schema reset/consumer 状态；当前连接单独用 PROCESSLIST_ID，线程关系存 THREAD_ID | PROCESSLIST_ID 与 P_S THREAD_ID 不同；digest 不绑定一次执行；history ring buffer 会覆盖 |
| Oracle | DBID/实例/CON_ID + SQL_ID + CHILD_NUMBER + PLAN_HASH_VALUE + first_load/last_active/capture time；活动会话加 INST_ID/SID/SERIAL# | SQL_ID 不区分 child；child 可变化/淘汰；RAC/PDB 作用域需显式；SID 可复用；cursor 统计自 load 后累计 |

不把用户业务 SQL 原文作为执行输入。日志、绑定值和凭据继续遵守脱敏；不能为摘要绕过脱敏器。

### 5.2 计划证据流程

1. 从异常请求/Top SQL 提取一个已确认身份（query/cursor/plan key、数据库和时间范围）。
2. 只读取服务器已有缓存计划；必须有受审查、版本绑定、权限受控的 Registry SQL。缓存未命中、淘汰、权限不足、版本不支持分别呈现。
3. 记录计划来源：current_request_cached、session_recent_handle、query_stats_cache、server_plan_cache 或 unsupported；附采集时间和关联置信度。不得声称计划对应此前一次历史执行，除非身份链能证明。
4. 解析计划节点：访问方式、扫描、join、sort/hash、估算行数、实际行数（仅真实来源提供时）、并行、内存授予/溢写（如有）。未知节点保留其类型。
5. 分开呈现计划事实与实际/累计统计。Plan Hash 不同只表示计划标识不同；不能单独推导退化。估算/实际行数必须来自同一次执行且身份一致才可比较。
6. 输出候选原因、证据等级、反证/缺项、下一步；不产生索引、hint、参数或强制计划变更语句。

当前只有 SQL Server 有该路径。dba/execution_plan.py、execution_plan_profiles.py、versioning.py 与 sql/sqlserver/plan_request.sql、plan_cached.sql 已有受控计划读取与有限 XML 分析。V0.4 隔离 Docker 五种计划节点记录在 docs/VERIFICATION_V0_4.md；实际缓存计划缺少 ActualRows，未宣称真实基数偏差或 spill。SQL Server 2008 R2+ 仍需逐 Profile 实机核验。PostgreSQL、MySQL、Oracle 不得因为 SQL 统计存在就宣布支持执行计划。PG/MySQL EXPLAIN ANALYZE 会执行语句；Oracle EXPLAIN PLAN 走写入式 plan table 路径；它们不是默认采集方式。其他数据库只有在找到合规只读且可关联目标 SQL 的缓存计划源，并完成 Profile/权限/E2E 后才可支持。

### 5.3 SQL 异常候选规则

| 候选 | 需要的最小证据 | 不足以单独下结论的事实 |
|---|---|---|
| 估算/基数偏差 | 同一次执行的 estimated/actual rows，节点与 SQL identity 一致 | 估算计划、不同执行的行数、统计信息日期 |
| 计划变化 | 同 SQL 身份至少两个可比较计划及时间/版本/作用域 | 单独不同 Plan Hash；SQL_ID 相同但 child 不同 |
| 参数敏感/Child 差异 | 参数化行为、同 SQL 下 child/plan 统计差异和执行群体对比；不输出 bind 值 | 单条快/慢执行或只出现多个 child |
| 索引/扫描候选 | 真实计划节点、估算/实际行数或 IO 统计、对象规模/选择性上下文 | 单独 table scan、missing-index 文字或高 logical reads |
| 高频负载 | 短窗口 execution delta 与稳定身份；可带真实支持的每次统计 | 缓存累计执行数或单条 duration |
| 等待导致 elapsed 高 | 当前会话 wait/blocker 和同一 SQL 的时间关联 | 累计 elapsed 或单独 wait event 名称 |

结论以 candidate/supported_by 表达，不做未经验证的自动根因分类器。

## 6. Oracle 专项深度分析方案

### 6.1 可调查的只读证据

- V$SESSION：当前会话、SQL_ID/CHILD_NUMBER、版本支持时的 SQL_EXEC_START、event/wait、状态、模块/客户端标识和实例作用域。
- V$SQL：SQL_ID + CHILD_NUMBER 的 SQL 文本（受权限/长度/脱敏限制）、PLAN_HASH_VALUE、FIRST_LOAD_TIME/LAST_ACTIVE_TIME、EXECUTIONS、CPU_TIME、ELAPSED_TIME、BUFFER_GETS、DISK_READS 等游标累计统计。
- V$SQLSTATS：SQL_ID 聚合缓存统计和 plan hash；经版本 Profile 与最小权限验证后才用于 Top SQL。
- V$SQL_PLAN：既有游标计划节点；关联 SQL_ID、child、plan hash、instance/container 和采样时间，不使用 EXPLAIN PLAN。
- V$SQL_SHARED_CURSOR：child 不可共享的 reason flags；只采需解释字段，按版本核验。
- 当前 wait/lock：复用现有 Slow/Blocking Profile，不为计划路径扩大到 DBA 角色。

当前 Toolkit Oracle Active Request SQL 已关联 V$SESSION 与 V$SQL 的 SQL_ID/child，并返回部分游标累计统计；尚无独立 Oracle Top SQL、V$SQL_PLAN 或 V$SQL_SHARED_CURSOR Registry/Collector，也无 Oracle plan analyzer。因此上述后几项是设计调查，不是当前功能。

### 6.2 多 Child 与指定案例

指定 SQL 分析请求显式携带 SQL_ID（或受控选择结果）、数据库/实例/CON_ID、采样时间范围和可选 CHILD_NUMBER。SQL_ID 存在多个 child 时按 child 分组展示计划、统计窗口和 mismatch flags；缺少 child identity 时不把各行相加成单一 SQL 成本。

| 场景 | 需要的证据 | 不能得出的结论 |
|---|---|---|
| HASH_MATCH_FAILED | SQL_ID/child、V$SQL_SHARED_CURSOR mismatch 字段、各 child Plan Hash、统计时间/计数 | 单一 flag 不说明哪条更慢，也不代表运行错误 |
| ROLL_INVALID_MISMATCH | 同上、Oracle 版本、child 创建/失效时间线和统计变化 | 不据 flag 单独归因生产慢 SQL；版本语义需核对 |
| cursor_sharing=force | 只读参数元数据（权限允许时）及 child/bind 证据 | 不自动修改参数；设置本身不证明 SQL 慢 |
| LEADING hint | 脱敏 SQL/计划 outline 的 hint 证据及 child/plan 对比 | hint 存在不证明它导致问题或应被删除 |
| 同 SQL_ID 不同计划 | SQL_ID、CHILD_NUMBER、PLAN_HASH_VALUE、实例/容器、版本、缓存时间、匹配窗口统计 | Plan Hash 不同不等于回退；不同 child 不一定有问题 |
| 手工约 0.022 秒、生产曾慢 | 生产采样当时的 SQL_ID/child/plan、运行环境和统计窗口，对比手工执行上下文/参数化 | 一次手动快执行不否定生产慢历史，也不能证明是计划问题 |

Oracle elapsed/cpu/buffer/disk 是缓存计数器/游标累计，不能冒充某一次执行耗时。AWR、ASH、ADDM、SQL Tuning Advisor、Real-Time SQL Monitor 等受许可/功能包约束的能力不纳入默认设计；许可负责人另行确认后才可单独评审，不能进入基础 READY 判据。

### 6.3 版本和权限风险

Oracle 11g–26ai 是目标兼容范围，不是全面实机通过范围。视图字段、容器/实例作用域、V$ synonym 底层对象授权和 RAC 汇总语义需逐 Profile 验证。权限优先对象级 SELECT ON SYS.V_$...；不索取 DBA、SELECT_CATALOG_ROLE 或 SYSDBA。Oracle P0.2 修复后是否覆盖这些新字段，必须另做完整只读 E2E。

## 7. PostgreSQL、MySQL、SQL Server 专项方案

### 7.1 PostgreSQL

- **当前：** pg_stat_activity 会话和阻塞/事务路径已存在；未集成 pg_stat_statements，没有 per-query CPU/IO Top SQL 或 Toolkit 计划 Collector。
- **可选统计：** pg_stat_statements 要求服务端预先配置 shared_preload_libraries，并在数据库预先创建 extension；Toolkit 不自动安装或改参数。不可用时显示 NOT_CONFIGURED/UNSUPPORTED 与影响，不能作为普通缺权限。
- **资源统计：** pg_stat_database 是数据库级累计；pg_stat_io 从 PostgreSQL 16 提供，属于数据库/对象级 IO 统计，不是 SQL 级归因，需分开报告。
- **身份：** 活动 PID 配 backend_start 做会话复核；queryid/统计 epoch 以数据库版本和扩展状态为条件。
- **版本：** PostgreSQL 13–18 为目标范围；字段、wait event、pg_stat_io、扩展能力按版本 Profile 验证。旧汇总实机是 PG16.15；较新 P0 修复后完整 E2E 应单独重跑。
- **权限：** 复用 pg_monitor 或当前 Permission Registry 认可的最小只读权限，不要求 superuser、pg_signal_backend。新增视图同步 Permission Registry、Preflight 和一致性测试。

### 7.2 MySQL

- **当前：** events_statements_current 和应急路径读取当前请求；近期 SQL 是小范围事件历史，不是 digest 总榜。旧系统记录中 slow wait detail 因不可见而 PARTIAL。
- **候选汇总：** events_statements_summary_by_digest 可支持频率/时间/rows 等 digest 累计候选，前提是 Performance Schema 表、consumer、容量与权限具备。Toolkit 不自动启用 consumer/instrument 或调整参数。
- **身份：** PROCESSLIST_ID 是客户端连接 ID；performance_schema.threads.THREAD_ID 是内部线程 ID；InnoDB trx_id 是事务 ID。必须分列保存，不能将 THREAD_ID/trx_id 当 connection ID。
- **指标：** ROWS_EXAMINED 是引擎检查行数，不等于物理读取；digest 累计不是当前请求。容量满、digest catch-all、events history 覆盖、reset 和 consumer 缺失都应标记质量。
- **版本：** MySQL 5.7、8.0、8.4 为目标范围；data_locks/data_lock_waits、replica 字段和 PS 列有版本差异。通过 DiagnosticProfileResolver 选择固定受审查 SQL。
- **权限：** 只授予 Registry 实际需要的只读访问和按 feature 定义的 PROCESS 等权限；不要求 SUPER、SYSTEM_USER、root。只读预检不证明所有隐含能力均只读。

### 7.3 SQL Server

- **当前：** sys.dm_exec_requests 已提供当前 request 的 elapsed、CPU、logical reads、reads/writes、wait、blocking 和 SQL text；SQL Server plan 已有版本化 Profile 与只读缓存计划 Collector。
- **候选汇总：** sys.dm_exec_query_stats 可为仍在缓存中的语句提供累计计数/执行次数，但条目会淘汰；只能作为 cache-resident evidence。sys.dm_os_wait_stats 是实例启动/清零以来累计等待，必须报告 reset 时间/未知时长并通过双采样计算 delta。
- **计划：** sys.dm_exec_query_plan 提供缓存编译计划，常常无实际运行行数；cache miss 不等于查询未执行。plan_cached 关联当前连接最近 SQL handle 的降级路径不保证和之前 Slow 样本同一次执行。
- **版本：** SQL Server 2008 R2–2022 是声明目标；SQL Server 2022+ 与旧版权限能力不同。DMV 字段及权限走 Version Profile；不由此推断 Azure SQL 兼容。
- **权限：** 基础活动统计按现有 Registry/Permission Registry；新增 query_stats、wait_stats 前确认版本级最小权限，不以 sysadmin、CONTROL SERVER、ALTER SERVER STATE 为方案。

## 8. 权限、生产开销与降级

1. 每个新增 Collector/Analyzer/Registry SQL 同步维护 Permission Registry、对应 Preflight、Version-Aware Profile、静态安全检查和一致性测试。
2. 用户交互只接受明确 database_id；禁止默认全 Inventory 扫描、发现邻居实例或连接复制拓扑里的 peer。
3. 查询需小型、确定性排序、有限结果；设置 timeout_seconds、max_rows 和 SQL 文本字节上限。大范围缓存汇总按需执行；不得用无界 COUNT 证明“无更多记录”。
4. 查询超时、Permission Missing、字段不存在、consumer disabled、统计未配置、缓存淘汰、结果截断是不同质量状态，不统一包装为零行/健康。
5. 高负载实例查询 timeout 时尽早停止 optional 查询，保留核心结果和未采项；不自动并发重试或放大预算。
6. 报告/日志沿用脱敏、HTML escaping、行数/SQL文本/字节预算；不得写原始业务 SQL、绑定值或凭据。人工处置参考不执行。
7. Oracle Diagnostic/Tuning Pack 未确认授权时不用；PG 扩展/MySQL consumer 不自动启用；外部 OS CPU/磁盘数据不伪装为数据库证据。

## 9. 统一诊断证据结构建议

这是设计对象，不要求本轮修改模型。每个 observation/finding 至少包含：

| 字段 | 用途 |
|---|---|
| evidence_id / finding_type | 稳定引用；类型可含 LOCK_CONTENTION、HIGH_QUERY_COST、CONNECTION_PRESSURE、IO_WAIT、MEMORY_PRESSURE、LONG_TRANSACTION、UNKNOWN |
| database_id / database_type / server_version / profile_id | 明确单资产与解析路由 |
| observed_at / sample_started_at / sample_finished_at / window_seconds | 区分快照和差值窗口；不承诺原子性 |
| source / registry_sql_id / permission_state | 可追溯受审查 SQL 和权限能力 |
| subject_identity | 数据库专属键：SQL Server session/request；PG PID+backend_start；MySQL PROCESSLIST_ID 与 THREAD_ID 分离；Oracle INST_ID+SID+SERIAL#+SQL_ID+child |
| metric_name / value / unit / metric_semantics | 保留单位及 current/cumulative/history/delta/per-execution 语义 |
| evidence_status / completeness | COMPLETE、PARTIAL、MISSING、UNSUPPORTED、UNKNOWN、TRUNCATED、RESET_OR_EVICTED 等 |
| facts / inference / confidence | 先列事实，再列候选推断与理由；不生成健康分数 |
| unknown_reasons / counter_evidence | 字段不可见、未配置、版本限制、权限、reset、eviction、身份冲突和反证 |
| next_safe_check | 下一项低风险核查，不输出变更动作 |

数据库专属字段可放入类型化 database_evidence，不删除 CHILD_NUMBER、query digest、request wait、事务 ID 等核心语义。

## 10. CLI 命令组织建议

只提出建议，不修改 CLI。沿用 dba diagnose <database_id> <scenario> 和现有 dba collect 资产选择；入口显式、按需，不一次执行所有昂贵检查。

| 目的 | 建议入口 | 行为 |
|---|---|---|
| 性能快速分诊 | dba diagnose <id> performance，或评估复用 slow | 单次核心快照；不默认 Top SQL、完整计划或双采样 |
| 阻塞/事务 | 继续 dba diagnose <id> slow，必要时显式子视图 | 复用现有链路，避免复制 blocker analyzer |
| Top SQL | dba diagnose <id> top-sql --by cpu/logical-reads/physical-reads/executions/waits（仅 Profile 支持时） | 说明语义、窗口和覆盖；未支持时明确 unsupported/not configured |
| 指定 SQL 深度分析 | dba diagnose <id> sql --sql-id ... 或数据库专属受控身份参数 | 接受 SQL_ID/query hash/digest，不接受任意 SQL 文本执行 |
| 执行计划 | 继续现有 SQL Server plan；其他库仅未来专门实现后开放 | 显示来源、版本、身份和缓存状态；不运行目标语句 |
| 日志/空间 | 继续 disk-growth 并区分当前状态/累计量/速率 | 不在 quick path 默认重复采集 |
| 复制/高可用 | 继续 replication | 单资产只读；不连 peer，不 failover/repair |
| 双采样 | 支持时显式 --window/--interval | 用户显式选择短暂停留；打印预算；永不称历史趋势 |

参数名称和 JSON schema 在实现前评审；本建议不构成命令承诺。

## 11. 分阶段开发和验收

### Phase 0 — 证据基线闭合

- 核对四库最新 P0/P0.2 验收文档与源码 HEAD，记录日期、DB 版本、驱动、权限和未覆盖场景；区分 Collector E2E、Slow E2E、Oracle privilege gate。
- 校正执行计划能力文档漂移；核对当前 plan profile 与 SQL。
- 验收：每个 IMPLEMENTED 有 SQL/源码、测试和适用实机证据；其他标 PARTIAL/MISSING/UNVERIFIED；Mock 不作为 E2E。

### Phase 1 — 最小性能分诊（建议 P0）

- 仅复用现有四库 Slow 核心 evidence（活动会话/请求、阻塞边、事务）；统一呈现观测窗口/完整性和“未观察到不等于健康”。
- 六类分诊只声称已有证据支持的内容；未实现的 CPU/IO/resource 类返回 PARTIAL/UNKNOWN，不造 Collector。
- 默认串行单快照；时间和预算显式；Presenter/HTML/JSON 共享 domain result；不新增昂贵 Top SQL。
- 验收：Mock 覆盖 required/optional、权限失败、超时、截断、unsupported、零行；四库隔离 E2E 核验现有路径；报告显示 snapshot window、来源、限制；不重复执行 SQL；无写操作。

### Phase 2 — 有界 Top SQL 与短窗差值

- 评估 SQL Server query_stats/wait_stats、MySQL digest、Oracle V$SQLSTATS 的最小 SQL/权限/Profile；PG pg_stat_statements 作为预配置可选能力，PG16+ pg_stat_io 只作实例/对象 IO 证据。
- 分开实现 current/cumulative/history/delta/per-execution；每引擎可缺字段，缺失不变成 0。
- 双采样限预算、行数和超时，并检测 reset/eviction/身份变化。
- 验收：高频短 SQL、低频慢 SQL、reset/eviction、disabled extension/consumer、权限缺失、条目变化、采样失败的 Mock/Fixture；隔离环境实测负载扰动；不在生产造故障。

### Phase 3 — SQL 深度/计划扩展

- 复用 SQL Server 计划；完善身份关联和 actual/estimated 差异表达。
- Oracle 单独做 V$SQL/SQLSTATS/SQL_PLAN/SHARED_CURSOR Profile、最小权限静态审查和版本矩阵；PG/MySQL 只有找到符合只读约束的计划源并验证后才立项。
- 验收：缓存命中/淘汰、同语句多计划、Child Cursor、多实例/容器 scope、脱敏、大小限制、无权限、未知字段；真实 E2E 只读且无 Diagnostics/Tuning Pack；无计划不推断未执行。

不进入常驻监控、历史数据库、Web UI、跨采样长期趋势、自动优化或数据库变更执行框架。

## 12. 真实场景验收矩阵

| # | 场景与期望 | 测试层级 | 现有证据状态 |
|---:|---|---|---|
| 1 | 空闲未提交事务阻塞；根和事务身份一致 | Fake + 显式隔离集成 | 四库历史应急链路不同程度覆盖；当前 HEAD 完整汇总需核对，Oracle 曾被连接权限阻断 |
| 2 | 长 SQL 只是锁等待受害者；不判根因 | Fake + 四库隔离集成 | 应急模型/测试区分根和受害者；逐库最新 E2E 需复核 |
| 3 | 高频短 SQL；窗口 execution delta 指出候选 | Counter fixtures + 每库隔离负载 | 未验证；当前无通用 Top SQL |
| 4 | 无阻塞但 SQL logical reads 高；区分逻辑读和 OS I/O | Fake + SQL Server 隔离 E2E；其他库按能力 | SQL Server 有 request logical reads；跨库 Top SQL/窗口归因未验证 |
| 5 | 同 SQL 不同缓存计划；身份/时间窗关联 | Plan fixture + SQL Server Docker | SQL Server 2022 有计划读取记录；退化闭环未验证 |
| 6 | Oracle 多 Child Cursor | V$SQL/V$SQL_SHARED_CURSOR fixture + Oracle E2E | 未验证；当前无 Oracle plan/child analyzer |
| 7 | elapsed 高但无充分瓶颈证据；UNKNOWN/PARTIAL | Fake + CLI/HTML presentation | 现有降级语义部分覆盖；统一 performance 分类未实现 |
| 8 | 连接数接近限制；需要上限证据才能判压力 | Fixtures + 版本化 integration | 未验证：无统一 max-connections collector |
| 9 | 当前负载消失但累计统计仍高；分开显示 | Counter fixture + 隔离 DB | 未验证：无 Top SQL 窗口模型 |
| 10 | 统计 reset/缓存淘汰；拒绝差值并说明 | Fake counter epochs + per-engine integration | 未实现 |
| 11 | 监控账号缺权限；区分 MISSING 与 privileged risk | Permission tests + 实机只读用户 | Permission framework 已有；新统计视图权限需审计 |
| 12 | 高负载时限流；超时不重试放大 | Timeout Fake + 隔离压测 | 查询 timeout/行数已有基础；性能总预算未实现/未实测 |
| 13 | SQL 已结束且计划不可用；不推断未执行 | SQL Server cache fixture + E2E | SQL Server cache miss 有处理；完整四库链路不适用/未实现 |
| 14 | 数据库版本缺统计视图 | Profile Fake + 目标版本实机 | 有 Version/Profile 框架；旧目标版本矩阵未覆盖 |
| 15 | 完全离线运维机运行 | Runtime fixture + 离线 Windows/Linux | Runtime 有部署框架/文档；新能力的离线部署仍需验证 |

场景 1、2 历史通过不能推导为所有数据库当前 HEAD 刚刚重跑；Phase 0 应附具体 E2E artifact/日期。普通 pytest 使用 Fake/Fixture，不能冒充实机验收。

## 13. 已知限制和未解决问题

1. 仓库汇总记录中的 Oracle 查询曾被 CREATE SESSION 权限阻断。Oracle P0.2 修复后需要新的完整 Collector/Slow E2E；连接或权限 gate 不能证明计划与 Top SQL。
2. 四库只有单一现代版本的部分实机记录。SQL Server 2008 R2、PG13、MySQL 5.7、Oracle 11gR2 等需旧版实机矩阵；2025 SQL Server、Oracle 26ai 按 Profile 的真实支持状态处理。
3. 没有 OS 级 CPU、内存、磁盘压力证据。单数据库连接不能代替 OS/虚拟化/存储监控。
4. pg_stat_statements 配置/权限、MySQL consumers/digest overflow、Oracle V$ 直接对象授权与容器范围、SQL Server 2022+ 和旧版 DMV 权限需逐项确认。
5. 各库 session/request/space Collector 时间不完全相同，不是原子快照；不同时间的证据不能无说明地拼成因果链。
6. 会话 ID 会复用；人工处置参考遵守现有人工复核和不自动执行规则。
7. 通用 Top SQL、跨类型核查对象 Top 3–5、统一性能 Evidence Model、双采样窗口均未实现。
8. SQL Server 缓存计划通常无 actual rows；其余三库 Toolkit 计划能力未实现。不可因第三方工具成熟而引入写操作、管理员权限、收费许可或服务端部署。
9. 统计和 SQL 文本受权限、缓存、扩展配置、reset、容量、脱敏与报告预算影响；UNKNOWN 必须是一等结果。

## 14. 需要用户评审的决策

1. **命令边界：** 新增 diagnose performance，还是现有 slow 承担核心分诊，其他按需命令处理 Top SQL/资源？建议先复用 slow 核心证据。
2. **默认证据规模：** 首期限定为现有 Slow required evidence 和小型会话候选，不增加 CPU/IO Top SQL 查询？建议是。
3. **窗口预算：** 是否接受 3 秒间隔、每端最多 100 行、单查询 3 秒、双采样阶段 12 秒、命令总预算 25 秒作为初始可调值？需隔离环境实测后定。
4. **可选统计依赖：** 是否把预先启用的 pg_stat_statements、MySQL Performance Schema consumers、SQL Server Query Store 仅作可选能力检测？建议不自动配置。
5. **Oracle 权限与许可：** 是否为未来 V$SQL_PLAN/V$SQL_SHARED_CURSOR 单独审查对象级 SELECT 和版本？本文不要求 Diagnostics/Tuning Pack，相关功能继续排除。
6. **Top-N 呈现：** 建议各指标类型独立 Top 5，不做综合分数。是否接受多榜单而非跨库最差 SQL 单一排名？
7. **验证门槛：** 是否要求 SQL Server 2022、PG16、MySQL 8.4、一个 Oracle 目标实例完成完整 CLI E2E 后，才提升新能力状态？旧版仍需最老支持分支抽样验证。

## P0 建议、无需重复开发的能力与最小交付

**P0 最应该先做的三个事项：**

1. 闭合四库基线和证据质量呈现：每项事实绑定采样时间、SQL/Profile、权限、截断、版本和失败原因；尤其补齐 Oracle P0.2 修复后的完整 E2E 证据。
2. 复用当前 Slow/阻塞/事务路径形成快速单次性能分诊摘要：区分 root、受害者、长 SQL、长事务和 unknown；不宣称健康，不默认查昂贵 Top SQL/计划，不改变人工处置安全门。
3. 建立指标语义和可选短窗口设计：先落 current/cumulative/history/delta 类型与 reset/eviction 降级，再逐库增加最小 Top SQL source；不能先做跨库排名再补语义。

**无需重复开发：** 四库 Credential/Inventory/受控 SQL Registry 与权限/Preflight、Version-Aware Diagnostic Profiles、当前 Slow 会话/阻塞/事务诊断、Reporter/离线 HTML 安全边界、SQL Server 版本化缓存计划读取与 XML 分析。扩展现有路径，不另建 resolver、权限表、阻塞分析器或 Renderer。

**主要版本/权限风险：** SQL Server 2008 R2+ 旧 DMV 字段和权限、SQL Server 2022+ performance-state 权限差异；PG13–18 统计列/pg_stat_io 和 pg_monitor 可见性；MySQL 5.7/8.0/8.4 lock/replica/Performance Schema consumer 差异；Oracle 11g–26ai V$ 字段、child cursor、RAC/CDB 范围和直接对象授权。目标版本声明不等于 E2E 通过。

**第一阶段最小可交付：** 单实例、单次、串行、短预算快照；只复用已有 Slow 核心查询；输出六类证据状态和时间/来源/权限/截断质量；无证据则 UNKNOWN/PARTIAL；Presenter/HTML/JSON 共享 Evidence 结果；无新扩展启用、无新增昂贵 SQL、无双采样、无历史和无健康分数。Phase 0 基线闭合后再启动。

**等待评审：** 命令入口、双采样预算、Top-N 多榜单、统计扩展/consumer 是否只做可选检测、Oracle 计划调查边界。本文不开始实现。

## 参考资料

项目内证据：[开源工具集成审计](../audits/opensource-diagnostic-integration-audit-v0.9.1.md)、[四库系统测试报告](../audits/test-report-v0.9.0.md)、[版本 Profile](../VERSION_PROFILES.md)、[SQL Server 计划验证](../VERIFICATION_V0_4.md)、[项目规则](../PROJECT_RULES.md)；各节列出相关源码、Registry 和测试。

数据库语义以官方文档为准；实现时按目标版本重新核对字段：

- Microsoft SQL Server [sys.dm_exec_requests](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-requests-transact-sql?view=sql-server-ver17)、[sys.dm_exec_query_stats](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-exec-query-stats-transact-sql?view=sql-server-ver17)、[sys.dm_exec_query_plan](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-exec-query-plan-transact-sql)、[sys.dm_os_wait_stats](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-os-wait-stats-transact-sql?view=sql-server-ver17)。
- PostgreSQL 16 [Monitoring Database Activity](https://www.postgresql.org/docs/16/monitoring-stats.html)、[pg_stat_statements](https://www.postgresql.org/docs/16/pgstatstatements.html)。
- MySQL [Statement Summary Tables](https://dev.mysql.com/doc/refman/8.0/en/performance-schema-statement-summary-tables.html)、[Statement Digests](https://dev.mysql.com/doc/refman/8.0/en/performance-schema-statement-digests.html)。
- Oracle 26 [V$SQL](https://docs.oracle.com/en/database/oracle/oracle-database/26/refrn/V-SQL.html)、[V$SQLSTATS](https://docs.oracle.com/en/database/oracle/oracle-database/26/refrn/V-SQLSTATS.html)、[V$SQL_PLAN](https://docs.oracle.com/en/database/oracle/oracle-database/26/refrn/V-SQL_PLAN.html)、[V$SQL_SHARED_CURSOR](https://docs.oracle.com/en/database/oracle/oracle-database/26/refrn/V-SQL_SHARED_CURSOR.html)；Oracle 19c [V$SQL_SHARED_CURSOR](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/V-SQL_SHARED_CURSOR.html) 用于旧版语义对照。

---

<pre>
DESIGN COMPLETE
SOURCE CODE CHANGED: NO
DATABASE ACCESSED: NO
THIRD PARTY INSTALLED: NO
COMMIT CREATED: NO
</pre>
