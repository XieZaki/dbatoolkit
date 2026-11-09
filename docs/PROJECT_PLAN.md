# DBA Toolkit 项目规划书
## Product & Engineering Master Plan

| 项目 | 内容 |
|---|---|
| 文档版本 | 1.0 |
| 文档状态 | 用户确认的产品规划基线 |
| 制定日期 | 2026-11-09 |
| 当前产品版本 | 0.9.1 |
| 仓库 | https://github.com/XieZaki/dbatoolkit |
| 本规划建立时的基线分支 | feature/top-sql-readable-report-20261108 |
| 本规划建立时的基线提交 | 15e2f881303221317d9c38745c6c05c8e3823fe5 |
| 产品负责人 | 用户 |
| 产品定位 | 跨数据库、离线、只读的 DBA 故障诊断、性能分析与数据库原生机制分析工具 |

> 本文件是产品目标、范围、原则和长期能力方向的权威基线。它不替代现有 Roadmap、Backlog、Progress、Acceptance，也不自动授权任何具体开发任务。

---

# 1. 产品愿景与定位

## 1.1 产品愿景

DBA Toolkit 面向数据库管理员（DBA），帮助其在数据库运行缓慢、业务阻塞、资源异常、日志或临时文件快速增长、数据库存在可用性风险时，快速取得可信证据、定位问题、分析性能瓶颈并制定人工处置方案。

产品不仅分析通用数据库问题，还必须逐步理解 MySQL、PostgreSQL、SQL Server、Oracle 各自的内部机制、优化器行为、事务模型、存储结构、并发控制和后台维护机制。

**产品核心主张：通用诊断负责发现现象，数据库原生诊断负责解释机制，证据关联负责判断影响和可能根因，DBA 负责最终决策与操作。**

## 1.2 产品定位

DBA Toolkit 是一款跨数据库的只读诊断与性能分析工具，不是数据库管理平台、自动运维平台或自动修复工具。

它通过目标数据库提供的只读系统视图、动态性能视图、DMV、Performance Schema、统计信息和其他经过审查的数据源采集证据，结合数据库版本、SQL、会话、事务、等待、执行计划、存储和实例资源信息进行分析，生成可追溯的诊断报告。

产品重点解决：

1. 开发反馈数据库慢，但无法快速确认是 SQL、锁、事务、资源还是数据库内部机制问题。
2. 出现阻塞时，难以从大量等待会话中找到根阻塞、阻塞链和真正持锁的事务。
3. 慢 SQL 很多，难以区分单次耗时、累计资源消耗、频繁执行和并发放大效应。
4. 数据文件、事务日志、WAL、binlog、归档、临时文件等突然增长，难以定位保留原因和相关对象。
5. 数据库 CPU、内存、IO、Latch、Mutex、工作线程或连接资源出现竞争，通用指标无法解释内部机制。
6. 数据库发生故障后缺少完整、可信、可复查的现场证据。
7. 不同数据库的诊断机制差异较大，DBA 需要反复切换客户端、手工执行脚本和关联信息。

## 1.3 目标使用场景

- 开发反馈数据库响应慢：快速识别慢 SQL、阻塞、长事务、执行计划问题及数据库内部竞争。
- 业务请求大量堆积：定位根阻塞、等待链、热点资源和影响会话。
- 数据库磁盘即将耗尽：分析数据文件、事务日志、WAL、binlog、归档、临时文件和可见的增长来源。
- 数据库整体性能下降：将 SQL 消耗与实例级等待、CPU、IO、内存、并发和数据库原生机制关联。
- 数据库存在潜在可用性风险：检查长事务、日志保留、复制槽、XID、TempDB、FRA、Undo 等风险。
- 故障复盘：使用诊断快照还原当时的会话、SQL、等待、事务、资源和采集完整性。

## 1.4 产品成功标准

不以代码行数、功能菜单数量或支持的数据库数量单独衡量成功，而以以下结果衡量：

- DBA 能否更快确定问题发生在哪个数据库对象、SQL、会话、事务或内部机制。
- 诊断结论是否准确，是否明确区分事实、推测和证据不足。
- 是否能指出证据来源、采样时间、指标单位和适用范围。
- 是否减少人工查询、复制和关联多个系统视图的时间。
- 报告是否足以支持 DBA 复核、向开发/运维说明问题和事后追溯。
- 工具是否保持低开销、只读、离线和可控，不因诊断本身给生产带来明显风险。

---

# 2. 产品目标与非目标

## 2.1 核心目标

| 优先级 | 目标 | 说明 |
|---|---|---|
| P0 | 快速故障定位 | Slow、Blocking、事务、磁盘/日志增长和关键风险 |
| P0 | 诊断可信度 | 结论必须由可追溯的数据库证据支持 |
| P0 | 数据安全 | 只读、最小权限、有界采集、失败关闭 |
| P0 | 现场留痕 | 生成可保存、可复核的 HTML 和结构化快照 |
| P0 | 数据库原生语义 | 保留各数据库的身份、等待、事务、计划和内部机制差异 |
| P1 | 性能根因分析 | 关联 SQL、计划、锁、事务、CPU、IO、内存和内部竞争 |
| P1 | 风险发现 | 识别可能导致业务阻塞、空间耗尽或数据库不可用的隐患 |
| P1 | 交付可靠性 | Windows 优先，支持离线绿色部署、升级和回滚 |
| P2 | 深度专项诊断 | 按场景逐步扩展数据库内部机制诊断 |
| P2 | 数据库扩展 | 在四种主要关系型数据库稳定后研究 MongoDB、Redis |

## 2.2 明确非目标

当前产品不承担以下职责：

- 自动 KILL、取消或终止会话/SQL。
- 自动修改数据、表结构、索引、统计信息或数据库参数。
- 自动执行 DDL、DML、GRANT、REVOKE。
- 自动创建诊断对象、安装扩展或启用监控消费者。
- 自动重启数据库、执行故障切换、修复或恢复。
- 任意 SQL 执行终端。
- SSH、远程 Shell、操作系统管理或主机自动修复。
- 必须访问公网、云端 API 或远程服务才能运行的功能。
- 未经批准的常驻 Agent、后台持续采集或自动告警平台。
- 以 AI 推测替代数据库证据。

可以展示供 DBA 人工复核的参考 SQL 或处置命令，但不得执行。引用命令必须有明确的证据门槛、风险说明和重新核对要求。

---

# 3. 数据库与操作系统支持范围

## 3.1 数据库产品目标

以下版本范围沿用当前已确认的产品目标，不代表所有版本和功能均已实现或完成真实环境验收。

| 数据库 | 产品目标 | 当前开发优先级 | 当前状态解释 |
|---|---|---|---|
| MySQL | 5.7、8.0+ | 当前主线 | 具体功能按 Profile 和验收证据确认 |
| PostgreSQL | 14+ | 当前主线 | PG12/13 不属于当前正式目标；保留代码不等于正式支持 |
| SQL Server | 2008 R2+ | 当前主线 | 旧版本 DMV、Showplan、驱动和 TLS 兼容性需独立验证 |
| Oracle | 11gR2+ | 当前主线 | 各版本视图、字段、权限和计划语义需独立验证 |
| MongoDB | 4.0+ | 长期目标 | 当前不宣称已有诊断实现 |
| Redis | 4.0+ | 长期目标 | 当前不宣称已有诊断实现 |

能力状态必须按数据库、版本、功能分别记录：

- TARGET：产品目标。
- IMPLEMENTED：存在经过审查的代码和 Profile 路径。
- AUTOMATED_VERIFIED：自动化测试覆盖该能力。
- E2E_VERIFIED：指定数据库版本、环境、账号和场景完成真实端到端验证。
- PARTIAL：仅部分采集或部分证据路径通过。
- UNVERIFIED：尚缺必要验证。
- UNSUPPORTED：有明确的不支持决定。

TARGET、代码存在、自动化测试通过和 E2E_VERIFIED 不得相互推导。开放式版本范围中的新版本不会自动获得正式支持。

## 3.2 操作系统目标

| 平台 | 优先级 | 要求 |
|---|---|---|
| Windows x64 | 最高 | 首要开发、测试和发布平台；绿色便携版优先 |
| Linux x64 | 高 | 保留原生运行和离线部署能力，独立验证 |

不能仅凭 Python 代码跨平台就宣称 Linux 已完整支持。文件权限、凭据存储、IPC、驱动、TLS、打包和升级流程均需在 Linux 环境独立验收。

## 3.3 部署与依赖

- 目标机器不要求预装 Python 或通过 pip 联网安装依赖。
- 支持完全离线安装、升级和回滚。
- 使用锁定依赖、Runtime Manifest 和离线 wheelhouse 管理运行时。
- 数据库 Python 驱动可独立替换或升级。
- 系统级原生依赖单独管理并提供离线安装说明。
- SQL Server ODBC Driver、Oracle Instant Client 等系统级组件不混入 Toolkit 内部驱动自动替换机制。
- Oracle 默认优先使用 python-oracledb Thin 模式；需要 Thick 模式时明确说明 Instant Client 前置条件。
- 不在生产诊断过程中联网下载依赖、驱动或更新。
- 安装、升级和回滚不得覆盖用户 Inventory、凭据库和历史报告。

---

# 4. 产品能力地图

DBA Toolkit 采用“通用诊断能力 + 数据库原生机制能力 + 跨维度证据关联 + 统一报告”的产品结构。

## 4.1 通用诊断能力

所有数据库尽可能提供一致的操作入口和可理解的诊断体验，但不强求底层指标和机制完全一致。

| 能力域 | 主要诊断内容 |
|---|---|
| 实例与连接 | 数据库身份、版本、实例状态、连接和可见性 |
| 活动会话 | 活跃请求、等待、运行时间、用户、客户端和应用来源 |
| 锁与阻塞 | 根阻塞、被阻塞会话、阻塞链、锁资源、锁模式 |
| 事务 | 长事务、未提交事务、事务年龄、事务与会话关联 |
| Slow SQL | 当前慢请求、执行统计、等待、SQL 身份和来源 |
| Top SQL | 按耗时、频率、CPU、IO、逻辑读等分别排名 |
| 执行计划 | 缓存计划、估算计划、历史计划和真实运行证据的边界 |
| 资源上下文 | 数据库/实例级 CPU、IO、内存、等待、工作线程和连接队列 |
| 磁盘与容量 | 数据文件、日志、临时文件、表空间、对象和增长来源 |
| 复制与高可用 | 复制延迟、日志保留、复制槽、发送/应用队列和可见的 HA 状态 |
| 风险检查 | 长事务、容量、日志、连接、维护积压和回绕风险 |
| 报告与留痕 | CLI 摘要、HTML、JSON、原始错误和诊断证据快照 |

## 4.2 数据库原生机制诊断

通用 Slow、Blocking 或 Top SQL 只能说明现象。产品必须逐步建立数据库专属的诊断规则，以解释优化器、事务、存储、并发和后台机制对性能及稳定性的影响。

原生诊断不是把数据库特有字段翻译成中文，而是回答：

1. 该机制是什么，在哪些版本和条件下存在。
2. 哪些系统视图、统计信息或等待事件能够提供证据。
3. 该机制是否真的处于异常状态。
4. 它与哪些 SQL、会话、事务、对象或资源相关。
5. 是否对当前性能或可用性产生了可观察影响。
6. 哪些反向证据或缺失证据会改变结论。
7. DBA 下一步应检查什么，可能的处理方向及风险是什么。

---

# 5. 四种数据库原生诊断能力规划

以下目录是产品能力地图和候选需求池，不是已实现清单，也不是一次性开发承诺。每项能力必须按具体版本、权限、采集成本和验收证据逐步进入 Backlog。

## 5.1 Oracle

重点领域：游标与绑定变量、解析与共享池、优化器、锁与事务、Buffer Cache、Undo/Redo、Temp、RAC 和存储。

| 专项 | 重点问题 | 候选证据来源/方向 |
|---|---|---|
| Child Cursor | VERSION_COUNT 异常、Child Cursor 分裂、共享失败原因 | V$SQL、V$SQL_SHARED_CURSOR |
| Bind Variables | Bind Mismatch、绑定类型不一致、绑定变量窥探 | V$SQL、V$SQL_BIND_CAPTURE、V$SQL_SHARED_CURSOR |
| Adaptive Cursor Sharing | Bind Sensitive/Bind Aware、不同选择性导致计划差异 | V$SQL_CS_*、V$SQL |
| SQL Parsing | Hard Parse、解析调用、游标加载和失效 | V$SYSSTAT、V$SQL |
| Shared Pool | Library Cache Lock/Pin、Mutex、共享池压力 | V$LIBRARYCACHE、V$MUTEX_SLEEP 等版本适用视图 |
| Optimizer | Plan Hash 变化、基数估算、直方图、统计信息问题 | V$SQL_PLAN、DBMS_XPLAN、DBA_TAB_STATISTICS |
| Lock/Enqueue | TX/TM 锁、阻塞链、事务等待 | V$LOCK、V$SESSION、V$TRANSACTION |
| ITL/Hot Block | ITL 不足、Buffer Busy、热点数据块或索引块 | V$SESSION_WAIT、V$SEGMENT_STATISTICS 等 |
| Undo | 长事务、Undo 保留、ORA-01555 风险 | V$UNDOSTAT、DBA_UNDO_EXTENTS |
| Redo/Commit | Log File Sync、Redo 生成和刷盘压力 | V$SYSTEM_EVENT、V$SYSSTAT 等 |
| Temp | 排序、Hash 工作区和临时表空间压力 | V$TEMPSEG_USAGE、工作区统计 |
| Archive/FRA | FRA 空间、归档堆积、归档目的地状态 | V$RECOVERY_FILE_DEST、V$ARCHIVE_DEST_STATUS |
| RAC | Global Cache、GC Buffer Busy、跨实例热点块 | GV$SESSION、GV$INSTANCE、GV$SEGMENT_STATISTICS 等 |
| Sequence/Index Hotspot | 高频插入、索引热点块和序列访问竞争 | 等待、段统计和 SQL 证据 |

专项规则要求：

- Child Cursor 数量高本身不是故障结论；必须分析共享失败原因、执行量、解析消耗和计划差异。
- V$SQL_BIND_CAPTURE 具有采样和可见性限制，不能承诺捕获全部绑定值；报告不得泄露敏感绑定值。
- Bind Peeking、Adaptive Cursor Sharing 和参数选择性问题必须结合版本、Child Cursor、计划和可获得的绑定证据判断。
- Library Cache、Mutex 或 Buffer Busy 等等待名称不能单独证明根因。
- RAC 相关等待必须结合实例、对象、块和请求身份，不能把普通本地锁等待误判为 Cache Fusion 问题。

## 5.2 PostgreSQL

重点领域：MVCC、HOT、表/索引膨胀、Vacuum/Autovacuum、XID、WAL、复制槽、锁/LWLock、执行计划和临时文件。

| 专项 | 重点问题 | 候选证据来源/方向 |
|---|---|---|
| Table/Index Bloat | Dead Tuple、Heap/Index 膨胀估算和空间回收 | pg_stat_user_tables、pg_class、可选扩展需单独审批 |
| HOT Update | HOT 与 Non-HOT 更新比例、索引维护放大 | n_tup_hot_upd、n_tup_upd、索引定义 |
| Fillfactor | 页面可用空间与 HOT 更新机会 | pg_class、pg_attribute、表/索引存储参数 |
| Autovacuum | 清理积压、Worker 占用、表级配置和执行进度 | pg_stat_user_tables、pg_stat_progress_vacuum、pg_settings |
| Long Transaction | 长事务、Idle in Transaction、xmin 阻塞清理 | pg_stat_activity、backend_xmin、事务年龄 |
| XID Wraparound | datfrozenxid 年龄、冻结进度和强制 Vacuum 风险 | pg_database、pg_class、pg_stat_progress_vacuum |
| Replication Slot | xmin/catalog_xmin、滞留 WAL、Inactive Slot | pg_replication_slots |
| WAL/Checkpoint | WAL 生成、归档失败、Checkpoint 压力 | pg_stat_wal、pg_stat_archiver、版本适用统计 |
| Lock | Heavyweight Lock、阻塞链、死锁 | pg_locks、pg_stat_activity |
| LWLock/Buffer Pin | 内部共享资源、Buffer Pin 等待 | pg_stat_activity.wait_event 等 |
| Temp/Spill | Sort/Hash 临时文件、临时块读写 | pg_stat_database、语句统计及日志（若已可用） |
| Planner | 估算行数、Join 算法、Generic/Custom Plan | EXPLAIN、prepared statement 统计和已有计划证据 |
| Visibility Map | Index Only Scan 可见性条件和 Heap Fetches | EXPLAIN、表统计及可用系统视图 |
| TOAST | 大字段存储、读写和空间占用 | pg_class、pg_attribute、对象大小统计 |
| Replication | Sender/Receiver、Replay Lag、Slot 状态 | pg_stat_replication、pg_stat_wal_receiver 等 |

专项规则要求：

- Dead Tuple 多不等于表已严重膨胀；膨胀估算必须标注估算方法和误差限制。
- 普通 VACUUM 通常使空间可重用，不保证文件立即缩小或归还操作系统。
- HOT 比例应结合计数器区间变化、更新列是否被索引引用、页面空间和 Fillfactor 分析；单次累计值不能直接代表当前速率。
- Autovacuum 问题必须区分未启动、运行缓慢、被长事务/xmin 阻碍、资源不足和配置不合适。
- XID 风险必须按数据库版本和官方机制确定阈值及判定方式，不能只用一个固定年龄阈值适配所有版本。
- 表/索引膨胀精确测量若依赖扩展或高成本扫描，必须是显式可选路径；默认不自动安装扩展或执行重型操作。
- Generic/Custom Plan 分析必须基于目标版本实际提供的证据，不得把 EXPLAIN ANALYZE 用于自动执行捕获的业务 SQL。

## 5.3 SQL Server

重点领域：参数嗅探与计划缓存、Query Store、TempDB、Memory Grant、Latch/Spinlock、调度器、事务日志、Version Store、Columnstore 和 Always On。

| 专项 | 重点问题 | 候选证据来源/方向 |
|---|---|---|
| Parameter Sniffing | 参数分布导致计划不稳定、同一 SQL 性能差异 | Query Store、计划缓存、运行统计 |
| Parameter Sensitive Plan | Dispatcher/Query Variant、参数敏感计划优化 | SQL Server 2022+ 且兼容级别等条件满足时的 Query Store/计划证据 |
| Plan Cache | 计划复用、重新编译、计划失效和缓存压力 | sys.dm_exec_query_stats、sys.dm_exec_cached_plans 等 |
| Statistics/Cardinality | 统计信息、数据倾斜、估算行数偏差 | sys.stats、Showplan、受审查统计信息读取 |
| TempDB Allocation | PFS、GAM、SGAM 分配页竞争 | PAGELATCH_*、等待资源/page_resource（版本适用） |
| TempDB Metadata | 临时对象元数据竞争 | 等待类型、资源信息和版本能力 |
| TempDB Spill | Sort/Hash Spill、临时空间压力 | Showplan、Query Store/运行证据（来源可用时） |
| Memory Grant | 等待授予、授予过量/不足、并发内存压力 | sys.dm_exec_query_memory_grants |
| Latch/Spinlock | Latch、Spinlock 与页面/内部结构竞争 | DMV、等待和资源地址 |
| Scheduler | SOS_SCHEDULER_YIELD、Runnable Queue、调度压力 | sys.dm_os_schedulers、等待统计 |
| Lock | LCK_*、阻塞链、死锁 | sys.dm_exec_requests、锁 DMV |
| Transaction Log | Log Reuse Wait、日志空间、长事务 | sys.databases、日志空间和事务 DMV |
| Version Store/ADR | 版本存储增长、清理和长事务影响 | 版本适用 DMV、数据库配置 |
| AG | Send Queue、Redo Queue、同步提交等待 | Always On DMV |
| Columnstore | Delta Store、Rowgroup、删除行和压缩状态 | Columnstore 相关 DMV |
| Index/Hot Page | Page Split、热点页、索引访问和写入模式 | sys.dm_db_index_operational_stats 等 |

专项规则要求：

- Parameter Sniffing 不能仅凭多个计划或一次耗时差异认定；需要结合参数选择性、计划、运行统计和观察窗口。
- PSP 能力必须按 SQL Server 版本、数据库兼容级别和具体计划证据判断，不能套用到旧版本。
- TempDB 必须区分 PFS/GAM/SGAM 分配页竞争、元数据竞争、物理 IO、空间耗尽和 Sort/Hash Spill。
- PAGELATCH_* 与 PAGEIOLATCH_* 必须分开解释；Latch 与 Lock 不能混用。
- Memory Grant 高不等于主机内存耗尽；必须结合等待队列、授予、并发和 Spill 证据。
- Version Store 与 ADR 的诊断必须检查版本、数据库选项和实际可见证据。
- Query Store 是可选的既有数据源；Toolkit 不自动启用、配置或清理 Query Store。

## 5.4 MySQL

重点领域：InnoDB Buffer Pool、Adaptive Hash Index、Doublewrite、Change Buffer、Redo/Undo/Purge、MDL/行锁、热点页、Binlog 和复制。

| 专项 | 重点问题 | 候选证据来源/方向 |
|---|---|---|
| Adaptive Hash Index | AHI 内部竞争、RW-Latch 等待、热点访问 | InnoDB 状态、Performance Schema 等版本适用证据 |
| Doublewrite | 双写写入路径、脏页刷新、存储延迟和刷盘压力 | InnoDB 状态、配置、IO/刷盘指标 |
| Change Buffer | Change Buffer 积压和 Merge 压力 | InnoDB 状态及相关统计 |
| Buffer Pool | 命中、脏页、刷新、淘汰和工作集压力 | Buffer Pool 状态与 Performance Schema |
| Redo/Checkpoint | Redo 生成、Checkpoint 推进、刷盘压力 | InnoDB 状态、Redo 指标和等待 |
| Undo/Purge | History List Length、Purge 滞后、长事务 | InnoDB 状态、information_schema.innodb_trx |
| Row Lock | Record、Gap、Next-Key Lock、锁等待 | performance_schema.data_locks、data_lock_waits |
| MDL | DDL 与业务请求之间的 Metadata Lock 等待 | performance_schema.metadata_locks |
| Hot Page | 热点记录、索引页和并发更新竞争 | 等待事件、InnoDB 状态和 SQL 证据 |
| AUTO_INCREMENT | 自增锁及高并发插入影响 | 锁/等待和版本配置 |
| Binlog/Commit | Binlog 写入、Group Commit、提交刷盘 | 状态、Performance Schema 和版本适用统计 |
| Replication | SQL Thread/Worker、复制延迟和应用积压 | replication status、Performance Schema |
| Temp | 内存/磁盘临时表、排序和临时文件 | Performance Schema、状态和语句统计 |
| Tablespace | 数据文件、临时表空间和文件增长 | INFORMATION_SCHEMA、文件与空间统计 |

专项规则要求：

- AHI 已启用不等于 AHI 争用；必须有相关内部等待/Mutex/RW-Latch 证据，并关联并发工作负载。
- Doublewrite 是数据页写入完整性保护机制，不得把“开启 Doublewrite”本身判为性能问题，也不得默认建议关闭。需要分析版本、写入路径、脏页刷新、Redo/Checkpoint、fsync 和存储证据。
- MySQL 版本对 Doublewrite 存储实现、AHI 和 InnoDB 统计有差异，必须通过 Profile 路由。
- Change Buffer、Purge、History List Length 需要结合实际积压、长事务和 IO 变化分析。
- MDL 与 InnoDB 行锁必须分开诊断。
- Record/Gap/Next-Key Lock 的报告要保留锁类型和资源语义。
- Buffer Pool 命中率、Redo 大小或 History List Length 单一数值不能直接认定根因。

---

# 6. 数据库原生诊断目录与规则合同

## 6.1 Engine Diagnostic Catalog

建立统一的数据库原生诊断目录（Engine Diagnostic Catalog），作为长期能力目录和规则索引。

它不是第二套 SQL Registry，也不是把规则散落在 HTML 模板中。它通过既有 Profile、Reviewed SQL Registry、Collector、Analyzer 和 ReportModel 实现。

目录按以下维度组织：

- 数据库引擎。
- 机制类别。
- 诊断场景。
- 数据库版本/版本族。
- 证据来源。
- 规则状态。
- 权限和采集成本。
- 测试及验收记录。

## 6.2 每条专项诊断规则的必备字段

每条规则必须明确：

| 属性 | 要求 |
|---|---|
| Rule ID | 稳定、唯一的规则标识 |
| 诊断名称 | DBA 可理解的中文名称，保留原生术语 |
| 数据库/版本 | 明确适用引擎和版本族 |
| 诊断目标 | 要确认或排查的具体问题 |
| 触发条件 | 哪些证据出现后才启动分析 |
| 数据来源 | 系统视图、DMV、统计视图或已有历史源 |
| 权限要求 | 最小读取权限和部署准备说明 |
| 采集成本 | 查询范围、频率、行数、超时和额外开销 |
| 指标语义 | 累计/当前/区间差值/估算/历史及单位 |
| 关联对象 | SQL、会话、事务、对象、计划、实例或主机 |
| 支持证据 | 支持当前发现的原始证据 |
| 反向证据 | 不支持或削弱该判断的证据 |
| 缺失证据 | 当前无法确认的关键数据 |
| 判定逻辑 | 适用条件、阈值来源和例外情况 |
| 结论等级 | CONFIRMED、HIGH_CONFIDENCE、SUSPECTED、INSUFFICIENT |
| 建议 | 下一步只读检查、人工处置方向和风险 |
| 版本差异 | 视图字段、内部实现和能力变化 |
| 测试状态 | 单元、集成、真实 E2E 的精确范围 |

规则必须可以独立测试、审查和停用。未完成版本/权限审查的规则不能进入默认生产诊断路径。

## 6.3 规则阈值原则

- 优先使用数据库官方定义、明确的资源上限或可解释的比例/变化量。
- 不使用未经验证的固定经验值作为跨版本通用阈值。
- 累计计数器必须区分实例启动时间、重置、缓存淘汰和采样窗口。
- 需要速率或趋势时必须有可比较的多次观测或可信历史数据。
- 估算值必须标注估算方法、适用限制和置信度。
- 不因单个高值直接生成故障结论。
- 阈值必须按数据库版本、对象类型、工作负载和配置能力审查。

---

# 7. 跨维度证据关联与根因分析

## 7.1 关联目标

产品应逐步从“列出异常指标”提升为“将相关证据连接起来”，但不能把相关性自动当作因果关系。

需要关联：

- SQL 与请求/会话身份。
- SQL 与执行计划、Child Cursor、Query Variant 或计划缓存身份。
- 会话与事务、锁、等待和阻塞链。
- 表/索引/数据块与内部等待。
- 数据库/实例资源与 SQL 工作负载。
- 日志、WAL、Undo、TempDB、FRA 等空间变化与事务/复制/后台任务。
- 数据库内部机制与实际业务影响。
- 数据库证据与经批准的外部主机/存储监控证据。

## 7.2 诊断结论模型

Finding 应保留：

- 诊断对象和原生身份。
- 观察时间和时间窗口。
- 数据来源与指标单位。
- 数据完整性、可见性、截断和失败信息。
- 支持证据和反向证据。
- 影响范围。
- 可能机制。
- 结论等级。
- 缺失证据。
- 下一步安全检查和人工建议。

结论等级沿用现有证据语义：

- CONFIRMED：证据足以确认该事实/机制在指定范围内发生。
- HIGH_CONFIDENCE：证据高度支持该判断，但仍有明确限制。
- SUSPECTED：存在相关信号，但不足以确认。
- INSUFFICIENT：缺少必要证据或身份/时间关联不足。

数据可见性和采集状态应独立表达，例如完整、部分、未观察到、失败、截断、不支持或权限不可见。未观察到不等于不存在；缺失不等于健康。

## 7.3 因果关联示例

以下是产品应具备的分析方向，不代表每条链都必然成立。

### PostgreSQL 更新变慢和空间增长

可能需要关联 HOT Update 比例下降、索引维护增加、Dead Tuple、Autovacuum 进度、长事务、xmin/复制槽、WAL 保留、IO 和 SQL 变化。必须逐项验证，不能仅凭 Dead Tuple 或 WAL 大小认定根因。

### SQL Server 并发和 TempDB 压力

需要区分 PFS/GAM/SGAM 分配竞争、TempDB 元数据竞争、Sort/Hash Spill、Memory Grant 等待、TempDB IO 和工作线程队列，再关联具体请求及观察窗口。

### MySQL 整体响应变慢

可能需要关联 AHI 内部等待、热点页、Redo/Checkpoint、脏页刷新、Purge、长事务、Buffer Pool 和存储延迟。不能仅凭 AHI 启用或 Redo 数值判断根因。

### Oracle SQL 性能波动

可能需要关联 SQL_ID、Child Cursor、共享失败原因、绑定变量类型/选择性、执行计划、Hard Parse、Library Cache/Mutex、统计信息和 SQL 执行来源。

## 7.4 资源范围不得混淆

必须区分：

- SQL/请求级资源消耗。
- 会话级等待。
- 数据库级统计。
- 实例级资源状态。
- 主机 CPU、操作系统内存和物理存储设备状态。

例如：

- SQL CPU 高不等于实例 CPU 已饱和。
- Logical Reads 高不等于物理设备读取高。
- 数据库文件 IO 等待不等于存储设备已饱和。
- Memory Grant 大不等于主机内存不足。
- 单次快照不等于趋势。
- SQL 文本相似不等于同一执行身份。
- 同一时间发生不等于存在因果关系。

主机 OOM、操作系统 CPU 饱和、物理磁盘剩余空间等信息，数据库查询未必能够可靠提供。没有经批准的主机证据来源时，报告必须说明需要结合外部监控或运维证据，不能伪装为 Toolkit 已确认的数据库结论。

---

# 8. 核心技术架构

## 8.1 总体架构

继续演进现有架构，不为新规划无必要地整体重写。

~~~text
                 DBA
                  |
             CLI / 操作入口
                  |
               Preflight
                  |
             诊断编排层
                  |
        +---------+----------+
        |         |          |
     Profile   Registry   Collector
        |         |          |
        +---------+----------+
                  |
             原始采集结果
                  |
       +----------+-----------+
       |                      |
  通用 Analyzer        Engine Analyzer
       |                      |
       +----------+-----------+
                  |
       跨维度证据关联 / Incident
                  |
              ReportModel
                  |
          +-------+-------+
          |       |       |
         CLI     JSON    HTML
~~~

## 8.2 必须复用的现有基础

- Inventory 和非敏感 Connection Profile。
- Credential Provider、Credential Resolver、Credential Session。
- Version-Aware Profile。
- Reviewed SQL Registry。
- Preflight。
- Collector / CollectorResult。
- 通用 Analyzer、Incident、Findings。
- ReportModel、Presenter、HTML Renderer。
- JSON、JSONL 和敏感 SQLPLAN 的现有处理。
- Runtime Manifest、离线安装、驱动替换、升级和回滚流程。

数据库原生 Analyzer 可以有各自的规则和结果，但必须通过现有安全采集与报告架构运行。禁止为单个专项另建 Adapter、Registry、Profile、Credential、权限系统或报告框架。

## 8.3 分层职责

- Inventory：只定义用户明确配置的目标实例。
- Credential：安全取得凭据，不暴露密码。
- Profile：识别版本和能力路由，未知能力失败关闭。
- Registry：只提供经过审查的有界只读 SQL。
- Collector：执行采集、记录时间、来源、行数、截断和错误。
- 通用 Analyzer：识别跨数据库的会话、锁、事务和资源现象。
- Engine Analyzer：解释数据库原生机制。
- Correlation：基于精确身份和兼容时间窗口关联证据。
- Incident/Findings：表达发现、影响、证据等级和限制。
- ReportModel/Presenter/Renderer：统一展示，不在模板中实现诊断逻辑。

---

# 9. 报告、快照与可追溯性

## 9.1 输出格式

- CLI：快速查看 DBA 摘要、异常和下一步检查。
- HTML：主要人工分析、保存和分享格式，必须自包含并支持离线打开。
- JSON：结构化诊断快照，供程序处理和一致性验证。
- JSONL：结构化运行审计日志，不混入正常终端输出。
- SQLPLAN：按现有安全策略单独保存，不默认嵌入 HTML；视为可能含业务 SQL、对象名和敏感字面量的内部数据。

CLI、JSON 和 HTML 必须来自同一诊断结果模型，不能各自形成不同结论。

## 9.2 报告内容

第一层：结论。

- 当前发生什么问题。
- 影响范围和观察窗口。
- 已确认事实、疑似原因和证据不足。
- 优先调查对象。
- 下一步安全检查和人工处置建议。

第二层：关联证据。

- SQL、会话、事务、阻塞链、等待、执行计划。
- 数据库原生机制和相关对象。
- 数据库/实例级资源上下文。
- 关键指标、时间和单位。

第三层：技术明细。

- 数据库身份、版本、Profile。
- 原生对象身份。
- Collector/数据来源。
- 采样起止时间。
- 指标语义和单位。
- 可见性、完整性、失败、截断。
- 权限限制、计数器重置/缓存淘汰等上下文。
- 原始数据库错误和必要的技术字段。

## 9.3 快照归档

建议采用 DBA 可配置的本地目录，按实例、日期和诊断场景组织。每份报告至少记录唯一标识、数据库身份、版本、Toolkit 版本、场景、采样时间范围、报告生成时间和完整性状态。

归档要求：

- 默认本地文件，不依赖云端。
- 不覆盖已有报告。
- 不擅自删除历史报告。
- 保留 HTML 与对应 JSON 的关联标识。
- SQL、主机、客户端和对象信息按内部敏感数据管理。
- 报告分享前由 DBA 审核。
- 后续如增加保留策略，必须显式配置并提供预览，不默认清理。

报告自动归档目录、索引和保留策略属于后续实现需求，不能在尚未实现时宣称已具备。

---

# 10. 安全、权限与生产运行约束

## 10.1 永久安全原则

- 仅连接 Inventory 明确选择的实例，不自动扫描或连接其他资产。
- 生产诊断只读、最小权限、有界、离线、版本感知、失败关闭。
- SQL 只能来自受审查的 Registry。
- 所有采集有超时、行数、结果大小和资源预算。
- 凭据不进入 CLI 参数、Inventory、日志、报告、环境变量或源码。
- 诊断账号与测试环境高权限账号严格分离。
- 权限由 DBA 按部署文档提前准备；权限不足保留原始数据库错误，不自动申请或扩大权限。
- 不自动 KILL、取消、修复、变更、重启或故障切换。
- 参考处置命令只供人工审核，必须说明重新核对会话身份和回滚影响。
- 不把权限不可见、采集失败或无结果当作健康。
- SQL 文本、执行计划、客户端主机、IP 和对象名称可能含内部敏感信息，报告必须按内部敏感资料管理。

## 10.2 采集开销控制

- 默认优先单实例、一次性、低开销、串行、有界采集。
- 默认不引入后台 Agent、常驻采样或高频双采样。
- 需要区间速率或趋势时，必须明确启用有界多次采样并记录采样窗口。
- 大范围历史扫描、全量执行计划、重型膨胀检查和额外扩展必须独立评估。
- 新增 SQL 必须评估执行计划、锁影响、数据量、超时、结果上限和版本兼容性。
- 不能为获得诊断数据而修改生产配置或启用额外监控功能。
- 需要外部主机证据时，优先由 DBA 对照已有监控资料；未来任何集成必须单独评估授权和安全边界。

---

# 11. 交互与产品形态

## 11.1 第一阶段：CLI + HTML

当前优先保持 CLI 作为操作入口，HTML 作为主要人工分析界面，JSON 作为结构化输出。

原因：

- 适合 Windows 运维机和隔离网络。
- 易于通过堡垒机环境使用。
- 部署依赖少。
- 便于脚本化和批量操作。
- 不引入 GUI 框架的额外维护成本。

GUI 不属于当前 1.0 的强制目标。待核心诊断和报告稳定后，根据实际使用效率评估。

## 11.2 中文化

- CLI 和 HTML 尽可能中文化。
- 结论、说明、状态、建议和报告章节使用 DBA 容易理解的中文。
- 数据库原生字段、SQL、错误码、等待事件、专业术语保留原文。
- 专业术语首次出现时可增加中文解释。
- 不得为了中文化而丢失原始数据库错误、字段和身份。

## 11.3 命令设计

沿用现有命令体系和场景化入口。未来命令扩展必须保持一致的参数、错误码、JSON、HTML 和报告预算行为。示例只表达目标交互，不代表所有场景当前均已实现。

---

# 12. 开发路线与版本策略

## 12.1 不重置现有 Roadmap

现有 Phase 0–5 的依赖关系、任务状态和用户决策继续以以下文件为准：

- docs/roadmap/DBA_TOOLKIT_V0.9.1_ROADMAP.md
- docs/roadmap/DBA_TOOLKIT_V0.9.1_BACKLOG.md
- docs/roadmap/DBA_TOOLKIT_V0.9.1_PROGRESS.md
- docs/roadmap/DBA_TOOLKIT_V0.9.1_ACCEPTANCE.md

本规划定义产品应该成为什么，不自行重排现有 Phase，不改变当前任务状态，也不授权跳过正在进行的 WAITFOR、四数据库报告和其他已登记验收工作。

新专项需求进入开发前，必须检查现有 Roadmap、Backlog、Progress 和 Acceptance，确认是否已有实现、候选任务、重复任务或未关闭的证据门槛。

## 12.2 当前 0.9.1 阶段

当前重点是完成已登记的核心诊断和统一 HTML Incident Workbench 工作，包括：

- SQL Server WAITFOR 独立等待与持有未提交事务时的混合阻塞场景。
- 正常等待不得误报；真实持锁 WAITFOR 不得被误判为无害等待。
- 根阻塞、被阻塞会话、事务、SQL 来源和采样完整性关联。
- CLI、JSON、HTML 结论一致。
- 四数据库报告及真实 E2E 的实际证据边界。
- 报告可读性、数据库原始错误和技术证据完整性。

以 Progress 当前指针和 Backlog 状态为准；用户提供的 HTML、日志和真实测试结果应按 Acceptance 规则登记，不能仅凭聊天结论自动改变任务状态。

## 12.3 1.0：核心诊断稳定与可交付

1.0 的重点是可靠性，不是要求所有原生专项全部完成。

建议 1.0 达到：

- 四种主要关系型数据库的已声明核心功能有明确的版本/功能验收边界。
- Slow、Blocking、事务和 Disk-Growth 核心路径可靠。
- Top SQL 和资源上下文保留真实指标语义。
- 已实现的执行计划来源明确区分缓存、估算和实际数据。
- 数据库原生错误可追溯。
- 报告结构稳定、离线可打开、结论与证据一致。
- Windows Full/Update 包、升级、回滚和驱动替换有正式验收。
- Linux 原生运行/部署的支持范围有独立证据。
- 不把未实现的专项诊断或未验证版本描述为已支持。

1.0 的精确功能范围、发布门槛和版本兼容矩阵必须结合现有 Acceptance 证据另行审核，不因本规划文件创建而自动宣布达到 1.0。

## 12.4 1.x：数据库原生机制诊断

在核心诊断稳定后，逐项增加 Oracle、PostgreSQL、SQL Server、MySQL 原生机制规则。

候选优先级以生产故障价值、现有数据源、测试可行性、权限和采集成本决定。建议优先研究：

- PostgreSQL：Autovacuum/长事务/xmin/WAL 关联、HOT 与膨胀。
- SQL Server：TempDB 分配与元数据竞争、参数敏感计划、Memory Grant/Spill。
- MySQL：Redo/Checkpoint、Undo/Purge、MDL/行锁、AHI 内部竞争。
- Oracle：Child Cursor/Bind Mismatch、Bind Peeking/Adaptive Cursor Sharing、Library Cache/Mutex、ITL/热点块。

这是产品研究优先级，不是已经批准的具体开发顺序。进入开发前仍须由用户结合当前环境和 Backlog 选择一个有界场景。

## 12.5 2.x 及长期方向

- 跨机制、跨时间窗口的证据关联和根因分析。
- 更多数据库专项故障场景。
- 经单独批准的离线主机/外部监控证据关联。
- MongoDB 4.0+、Redis 4.0+ 的独立诊断设计与实现。
- 可选离线日志分析 PoC，例如 pt-query-digest、pgBadger；必须单独审查许可、打包、隐私和输出边界，不作为核心运行依赖。
- GUI 或离线 AI 辅助解释仅在核心能力稳定后评估；AI 不能生成虚构证据或执行数据库操作。

---

# 13. 测试实验室与验收标准

## 13.1 本地四数据库实验室

用户授权的测试实验室位于：

**D:\DBA\DBAToolKit\DBAToolKit-LoadTest**

实验室使用 Docker 容器部署 MySQL、PostgreSQL、SQL Server 和 Oracle，并包含以下公共测试库：

| 数据库 | 测试库 |
|---|---|
| MySQL | Chinook、Sakila |
| PostgreSQL | Chinook、Pagila |
| SQL Server | AdventureWorks2022、AdventureWorksDW2022、Chinook |
| Oracle | Chinook、HR、CO |

本地账号密码文件：

**D:\DBA\DBAToolKit\DBAToolKit-LoadTest\local_config.py**

安全要求：

- 该目录、local_config.py、账号密码、测试输出和本地报告不得上传 GitHub。
- 不读取或输出密码，不将凭据复制到提示词、Inventory、文档或源码。
- 用户允许在该隔离测试实验室使用高权限账号进行测试数据准备、DML、DDL、索引、统计信息和压力测试。
- 该授权只适用于用户明确指定的本地测试环境，不延伸到生产环境。
- Toolkit 诊断 Adapter 仍使用独立监控账号，不能把实验室高权限账号当作产品运行默认身份。
- 测试前确认目标容器、数据库、测试对象、并发和清理路径。

## 13.2 现有并发负载脚本

每个会话轮换执行四类 SQL：

1. 多表 JOIN：在关联键使用 CAST、TO_CHAR、CONVERT 等函数/类型转换，使普通索引可能难以用于 JOIN 条件；最终访问路径仍由数据库优化器决定。
2. 自连接：对样本数据进行函数连接，增加比较和 Hash Join 工作量。
3. 多表聚合：连接事实表和维度表，再执行聚合。
4. 聚合与排序：增加 GROUP BY、ORDER BY 工作量。

数据库执行倾向：

- MySQL：通过关联键函数转换影响普通索引等值查找，是否全表扫描由优化器决定。
- PostgreSQL：每个会话关闭 enable_indexscan、enable_indexonlyscan、enable_bitmapscan，倾向顺序扫描。
- SQL Server：测试表使用 INDEX(0)，并使用 HASH JOIN、MAXDOP 1。
- Oracle：对 SH.SALES 等表使用 FULL、USE_HASH，并对关联键使用 TO_CHAR。

SQL 注释标识：**DBA_TOOLKIT_LOAD**。可用于 Slow、Top SQL 和活动会话报告中的测试负载识别。

这些策略是测试负载构造方式，不保证每次执行计划完全相同；应在报告中核实实际执行计划和运行结果。

## 13.3 压测执行顺序

先进入测试目录：

~~~powershell
Set-Location 'D:\DBA\DBAToolKit\DBAToolKit-LoadTest'
~~~

先做 10 会话、15 秒连接和 SQL 冒烟测试，每次只运行一个数据库：

~~~powershell
python .\run_mysql_load.py --sessions 10 --duration 15
python .\run_postgresql_load.py --sessions 10 --duration 15
python .\run_sqlserver_load.py --sessions 10 --duration 15
python .\run_oracle_load.py --sessions 10 --duration 15
~~~

确认连接、SQL 和容器资源正常后，再进行正式测试，每次只运行一个数据库：

~~~powershell
python .\run_mysql_load.py --sessions 100 --duration 45
python .\run_postgresql_load.py --sessions 100 --duration 45
python .\run_sqlserver_load.py --sessions 100 --duration 45
python .\run_oracle_load.py --sessions 100 --duration 45
~~~

常用参数：

- sessions：并发连接数，默认 100，允许 1–300。
- duration：负载持续秒数，默认 45。
- pause-ms：每条 SQL 完成后的间隔，默认 100 毫秒；0 会更激进。
- query-timeout：单条 SQL 超时秒数，默认 60。
- dry-run：读取配置并打印 SQL，不连接数据库。

300 是脚本允许的参数上限，不是推荐默认值。提高并发前必须评估 Docker 容器和宿主机资源。不得同时启动四个数据库的高并发负载，除非用户另行批准并确认资源预算。

示例：

~~~powershell
python .\run_postgresql_load.py --sessions 100 --duration 60 --pause-ms 150
~~~

## 13.4 测试分层

- Unit：规则、解析、模型和边界测试。
- Integration：Registry、Collector、Analyzer、Presenter 组件集成。
- System：指定实例的完整 CLI 诊断流程。
- Regression：已修复问题和安全边界回归。
- Real E2E：明确版本、环境、账号、场景、命令和实际结果的真实数据库验证。

Fake/Mock、静态检查、自动化测试、历史 E2E、当前 HEAD E2E 和用户提供的部分报告是不同证据等级，不能混为一谈。

真实数据库测试必须在已确认的 Windows 宿主机测试环境执行。若当前执行环境无法访问用户授权的实验室，应提供用户可执行的 PowerShell 命令，不得猜测网络地址、容器端口或凭据。

## 13.5 专项诊断验收门槛

每个数据库原生专项至少完成：

1. 明确故障场景和目标机制。
2. 明确适用数据库版本及版本路由。
3. 审查官方文档和系统视图语义。
4. 审查监控账号最小权限。
5. 审查 SQL 开销、超时、行数和结果上限。
6. 设计正向、反向、边界、无权限、采样不完整测试。
7. 验证与 SQL/会话/事务/对象的身份关联。
8. 验证结论不会把相关性写成因果。
9. 验证 CLI、JSON、HTML 结论一致。
10. 在可用隔离实验室完成真实 E2E，记录精确版本和环境。
11. 记录已知限制、缺失证据和未覆盖版本。
12. 通过用户审核后才能标记相应验收状态。

---

# 14. 质量、性能和发布标准

## 14.1 代码质量

根据变更范围执行 pytest、Ruff、MyPy、Registry/配置校验、Git diff 检查和敏感数据扫描。测试结果必须对应实际提交和工作区，不能引用旧结果代替当前 HEAD 证据。

## 14.2 生产采集质量

每次诊断应记录采集起止时间、执行耗时、来源、超时、行数、截断、失败和可见性。采集不是原子快照时必须说明。数据不足时输出 UNKNOWN/PARTIAL/INSUFFICIENT，不得生成健康结论。

## 14.3 报告质量

- 自包含离线 HTML。
- 有界输出和明确省略数量。
- 正确转义用户和数据库内容。
- 不泄露凭据。
- 原始数据库错误可追溯。
- 原生字段和指标单位保留。
- 结论与证据可相互定位。
- 不输出没有 Analyzer 证据支持的根因徽标或处置建议。

## 14.4 发布质量

正式 Release 前必须审核：

- 功能和版本支持矩阵。
- 自动化与真实 E2E 证据。
- Windows Full/Update 包。
- 离线安装、升级、回滚。
- 驱动独立替换。
- SHA256 校验。
- 敏感文件扫描。
- 用户 Inventory、凭据和报告不被覆盖。
- Release 说明准确区分 TARGET、IMPLEMENTED 和 E2E_VERIFIED。

版本号、Tag、Release 和 main 合并均按用户授权及现有仓库流程执行。

---

# 15. 开发治理与文档职责

## 15.1 规划和执行文档分工

| 文档 | 唯一职责 |
|---|---|
| docs/PROJECT_PLAN.md | 产品定位、目标、范围、原则、能力地图和长期规划 |
| docs/roadmap/DBA_TOOLKIT_V0.9.1_ROADMAP.md | 当前规划流的阶段边界、依赖、进入/退出条件和非目标 |
| docs/roadmap/DBA_TOOLKIT_V0.9.1_BACKLOG.md | 任务状态的唯一来源 |
| docs/roadmap/DBA_TOOLKIT_V0.9.1_PROGRESS.md | 当前开发位置、下一步和恢复指针 |
| docs/roadmap/DBA_TOOLKIT_V0.9.1_ACCEPTANCE.md | 自动化、实机、版本、报告和验收证据 |
| docs/PROJECT_MEMORY.md | 项目背景、历史决策、技术约束、用户偏好和新会话恢复指南 |

同一任务状态不得在多份文档重复维护。PROJECT_PLAN 不承担每日进度或任务状态台账职责。

## 15.2 新功能启动流程

任何新功能开发前必须明确：

1. 对应本规划章节和用户价值。
2. 当前是否已有实现、Profile、SQL Registry 或验收证据。
3. 具体数据库版本和适用范围。
4. 要解决的单一问题和非目标。
5. 需要的证据源、权限和采集成本。
6. 身份关联和时间窗口要求。
7. 正向、反向和不完整场景。
8. 自动化和真实 E2E 计划。
9. 风险、回滚和对既有功能的影响。
10. 明确验收标准。

一次只推进一个有边界的场景。不得以专项诊断为由同时重写多个稳定模块。

## 15.3 变更控制

若发现规划之外的功能：

1. 说明现有规划为什么无法满足。
2. 说明业务价值、风险、成本和依赖。
3. 检查是否已有类似实现。
4. 提交规划变更建议。
5. 用户批准后更新本规划及对应 Roadmap/Backlog。

未经用户确认，不得擅自扩大范围、改变安全边界、调整开发顺序或宣布阶段完成。

## 15.4 Git 和敏感资料

- 采用功能分支开发。
- 先审查 diff 和暂存清单，再提交。
- 提交前运行敏感数据检查。
- 远程核验后再考虑合并 main。
- Release 必须单独获得授权。
- 不上传真实 Inventory、local_config.py、账号密码、Credential Store、真实诊断报告、数据库日志、SQLPLAN、本地压测数据、虚拟环境和临时测试产物。
- 不盲目使用 git add . 或 git add -A。

---

# 16. 规划执行规则

1. 本规划是 DBA Toolkit 的产品目标和工程原则基线。
2. 新功能必须能对应本规划中的产品目标或经用户批准的变更。
3. 现有 Roadmap 决定阶段和依赖，不因本规划创建而重置。
4. Backlog 是任务状态唯一来源。
5. Progress 是当前开发位置唯一来源。
6. Acceptance 是测试和验收证据唯一来源。
7. PROJECT_MEMORY 记录历史背景和恢复上下文，不维护第二套任务状态。
8. 通用诊断与数据库原生诊断必须共同演进，不能以通用字段覆盖原生语义。
9. 每项原生专项必须有版本、权限、采集成本、证据、规则和验收定义。
10. 缺少证据时必须明确说明，不允许为了报告完整而推断。
11. 生产安全和数据完整性优先于诊断覆盖率、执行速度和功能数量。
12. 本规划的修订、阶段切换、范围扩大和发布均须经用户确认。

---

# 17. 最终产品定义

DBA Toolkit 最终应成为一款 DBA 在遇到数据库性能问题、业务阻塞、资源异常或潜在故障时，可以放心使用的跨数据库诊断工具。

它应具备三项核心竞争力：

1. **快速故障定位：** 找到相关 SQL、会话、事务、根阻塞、对象和资源瓶颈。
2. **数据库原生机制分析：** 理解 Oracle、PostgreSQL、SQL Server、MySQL 各自的优化器、事务、存储、并发和后台机制，解释通用指标无法说明的问题。
3. **证据关联与故障追溯：** 将不同来源、不同范围和不同时间窗口的证据关联起来，明确事实、推测、反向证据和缺失信息，形成可复核的诊断报告。

**工具负责采集、关联、分析和说明；DBA 负责判断、沟通和执行。**

规划书结束。
