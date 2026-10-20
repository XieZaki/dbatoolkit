# DBA Toolkit V0.9.1 — 开源诊断工具集成可行性审计

**审计日期：** 2026-10-19

**代码版本：** 0.9.0

**项目路径：** `D:\DBA\DBAToolKit`

**性质：** 只读技术审计与规划；本轮未连接数据库、安装/下载软件、修改业务代码或提交 Git。

## 结论

**建议采用“受控参考 + 可选离线文件分析”模式，不把第三方数据库端脚本或服务嵌入 Toolkit 的在线诊断路径。** 当前 Toolkit 已经覆盖四库的慢 SQL、磁盘增长和复制状态只读诊断；SQL Server 另有已有计划读取能力。优先借鉴 SQL Server First Responder Kit、sp_WhoIsActive、Percona Toolkit、pg_activity、pgBadger 的诊断思路、字段解释和人工操作手册，在完成逐脚本许可证、安全、权限、版本和负载审查前，不复制或调用它们的 SQL/脚本。

可作为后续独立选项评估的只有**离线日志分析**：MySQL `pt-query-digest` 和 PostgreSQL `pgBadger`。它们可以处理运维人员已导出的日志，不必让 Toolkit 向数据库添加对象或权限；但它们需要单独的软件包、平台依赖和数据保护流程，不适合直接作为现有 Toolkit 运行时依赖。PostgreSQL PoWA、SQL Server 安装型存储过程库以及 Oracle SYS 脚本/AHF 均不适合纳入 Toolkit 的只读、最小权限、轻部署核心。

Oracle 没有找到一个同时满足“许可证明确、活动维护、普通监控账号、无需服务端部署、适用于离线 Windows/Linux、无 AWR/ASH 许可前置”的现成开源组件，因此建议继续维护 Toolkit 自己的 Oracle Registry SQL，并仅把已确认合规的公开资料作为人工参考。这里的“未找到”是本次限定范围审计的结果，不代表不存在任何 Oracle 社区脚本。

## 审计依据与边界

本地证据来自 `dba/cli.py`、四库 Adapter/Collector/Analyzer、`dba/permissions.py`、`dba/versioning.py`、`dba/slow_profiles.py`、`dba/execution_plan.py`、四库 `sql/` Registry 及其 YAML Metadata；测试证据来自 `tests/` 和 `docs/audits/test-report-v0.9.0.md`。上游事实以各项目官方仓库、许可证文件、官方文档和发行说明为准，来源链接列在候选评估表中。维护活跃度只按公开活动和发行记录做有限判断，不构成安全审计或供应商支持承诺。

本轮没有运行第三方代码、安装包或工具，也没有连接数据库重新验证。实机结论是仓库现有验收记录中的历史证据，见下表；不能解释为本审计日期的实时可用性保证。

## Toolkit 当前能力与验证覆盖

| 数据库 | 已有 Toolkit 诊断 | 当前计划/执行计划能力 | 仓库记录的真实 E2E | 主要边界 |
|---|---|---|---|---|
| SQL Server | `slow`、`disk-growth`、`replication`；基础活动会话/请求、事务、阻塞 Collector | `plan` 仅 SQL Server；从当前请求或连接最近 handle 读取服务器已有计划，不执行用户 SQL | SQL Server 2022 `16.0.4305.5`：连接、Collectors、slow、disk-growth、replication 通过；Driver 17 fallback。已有计划能力另有 SQL Server 2022 Docker 读取记录 | 2008 R2+ 属于兼容设计目标，但旧版本实机矩阵未覆盖；无可复用计划不应解读为查询从未执行 |
| PostgreSQL | `slow`、`disk-growth`、`replication`；会话、事务、阻塞 Collector | 当前没有 Toolkit execution-plan Collector；统计信息不等于执行计划 | PostgreSQL 16.15：连接、Collectors、slow、disk-growth 通过；复制诊断有“无连接 standby”警告 | 没有 `active-requests` Collector；旧版本和完整 PG 13–18 实机矩阵未验证 |
| MySQL | `slow`、`disk-growth`、`replication`；阻塞/事务、最近语句 Collector | 当前没有 Toolkit execution-plan Collector；Performance Schema digest 不等于计划树 | MySQL 8.4.11：连接、Collectors、disk-growth、replication 通过；slow 为 PARTIAL，当前 wait detail 不可见；最近 SQL 另有真实 5 条采集验收记录 | 旧版本实机矩阵未验证；最近 SQL 受 Performance Schema 配置、权限和历史保留影响 |
| Oracle | `slow`、`disk-growth`、`replication`；会话、事务、阻塞及 previous SQL 证据 | 当前没有 Oracle plan Collector/Registry/`DBMS_XPLAN` 路由 | Oracle Free 容器的监听端可达，但监控用户缺少 `CREATE SESSION`；未能执行 Oracle 查询，也未验证实际数据库版本 | Oracle 的 SQL/功能兼容路径主要是静态与 Fake Adapter 覆盖；本地真实执行仍需 DBA 配置最低连接权限 |

上述历史结论见 [`docs/audits/test-report-v0.9.0.md`](test-report-v0.9.0.md)。该报告记录 509 passed、1 skipped，以及 Oracle 权限阻塞；不能把单一现代版本的通过推广为所有声明版本均已真实验证。

### Slow SQL 和执行计划的现状

四库 slow 诊断不是同一查询的多驱动封装：各自有绑定版本的 `DiagnosticProfile`，其中规定受审查 SQL、权限契约、解析器字段、支持状态以及 required/optional collectors。SQL Registry 要求 `READ_ONLY`、`reviewed: true`、查询超时和行数上限；Oracle 取当前/前序 SQL 与 SQL_ID、child number 等会话关联字段，MySQL 可采近期事件历史，SQL Server / PostgreSQL 依赖各自统计与活动会话视图。证据不可见时，当前语义允许结果为 PARTIAL/UNKNOWN，而不是把空结果伪装成“无问题”。

SQL Server `plan` 已有版本化绑定、只读 Registry SQL 和受限 XML 解析。当前 Collector 查询 `sys.dm_exec_requests` 的现有请求计划，或基于当前连接 `most_recent_sql_handle` 查缓存；第二条路径只表示关联缓存证据，不保证是 slow 报告中同一执行实例。当前请求已结束、缓存淘汰、权限不足或版本未验证时，计划不可用/不执行；计划 XML 另有限制，HTML 不内嵌原始 XML，`.sqlplan` 属于本地敏感产物。PostgreSQL、MySQL、Oracle 没有相应 Toolkit 计划采集实现。Oracle 的 SQL_ID、child cursor 和 V$SQL 文本采集不等于计划支持。

发现一项**文档漂移**：`docs/design/execution-plan-capability-safety-audit-v0.9.1.md` 中仍保留“Plan CLI 未纳入 Resolver”的早期结论；当前 `dba/cli.py` 已解析 `plan` profile，`dba/versioning.py` 有 SQL Server 计划族绑定，`ExecutionPlanCollector` 要求已验证且受支持的 profile。该设计稿应在后续单独文档维护时更新，不能据旧设计稿误判当前代码状态。

## 上游工具评估

| 项目 | 许可证 / 维护证据 | 运行与部署模型 | 权限、写入和性能风险 | 建议 |
|---|---|---|---|---|
| [SQL Server First Responder Kit](https://github.com/BrentOzarULTD/SQL-Server-First-Responder-Kit) | 仓库提供 MIT `LICENSE.md`，并注明部分来源代码归属；发行记录显示持续发布。当前发布说明要求 SQL Server 2016 SP2+，更老版本转向 `Deprecated` 脚本 | T-SQL 存储过程集合，典型安装需要在目标实例创建/更新对象；并非 Toolkit 客户端库 | 安装有 DDL；仓库还包含 `sp_kill`、更新器等管理能力。各过程最小权限、每个脚本写入/负载未逐项审查。自动更新器会从 GitHub 取脚本，离线生产环境不应采用 | **不集成执行。** 可引用其检测类别/人工解释；若 DBA 在独立环境使用，必须固定 release、逐脚本检查、单独授权和压测。MIT 不等于可跳过安全与归属审核 |
| [sp_WhoIsActive](https://github.com/amachanic/sp_whoisactive) | 上游作者许可说明现为 GPLv3；源文件当前标识版本 `v2200.20260409`、兼容 SQL Server 2022+ | 安装型存储过程，数据库侧有 DDL；收集活动会话、SQL、锁与等待，参数化输出 | 需要读取会话/请求 DMV，安装和授权应由 DBA 管理；服务端过程运行开销需按参数/采样频率实测。GPLv3 对与 Toolkit 分发或代码复制有合规影响 | **不复制、不安装调用。** 作为 SQL Server 会话/等待字段设计参考；其 GPLv3 与老版本/跨版本支持也不匹配 Toolkit 当前声明 |
| [Percona Toolkit / `pt-query-digest`](https://github.com/percona/percona-toolkit) | 仓库标识 GPL-2.0；官方 changelog 有持续维护记录，记录的 3.7.1-4 于 2026-07-02 发布 | Perl 命令行工具。`pt-query-digest` 可分析 MySQL slow/general/binlog 文本；可在运维机离线处理预先导出的文件 | 文件分析模式不需要数据库账号；`--review` / `--history` 会写数据库表并可能自动创建对象；实时 processlist、explain 等模式会连库。工具整体并不天然只读 | **仅推荐单独、显式的离线文件分析试点。** 禁止 Toolkit 自动调用；如未来集成需限制为输入文件模式、固定参数、隔离输出、确认 GPL 与 Perl/平台离线包，并拒绝 `--review`、`--history`、processlist、explain 等路径 |
| [pg_activity](https://github.com/dalibo/pg_activity) | 仓库标示 PostgreSQL License；GitHub 有持续项目活动 | Python/psycopg 终端交互式实时监控客户端，需要直接连 PostgreSQL | 官方文档称 superuser 可获得最多信息，否则降级；快捷键可执行 cancel/terminate backend，与 Toolkit 只生成人工参考的安全边界冲突。交互式界面也不适合作为 CLI/报告库 | **不集成运行。** 可借鉴活动会话呈现/字段解释；不能让 Toolkit 启动其交互界面或继承终止能力 |
| [pgBadger](https://github.com/darold/pgbadger) | 上游自带 PostgreSQL 风格许可文本，允许免许可费使用/复制/修改/分发，但包含署名和免责声明；发行页列出 v13.1（2025-03-16） | 单文件 Perl 日志分析器，处理已写出的 PostgreSQL 日志并生成报告；压缩日志可能调用 `zcat`、`bzcat`、`lz4cat`、`zstdcat`、`unzip` 或 `xz` 等系统命令 | 离线文件模式不访问数据库，不需要 DB 权限；日志可能包含明文 SQL、字面量、业务标识和用户数据，报告必须按敏感证据保存。Windows/离线 Perl 与压缩程序组合须自行验证 | **推荐作为可选离线分析候选。** 不作为默认依赖；限定日志副本和本机输出、审查脱敏/保留规则，先在 Windows 和 Linux 离线包中验证 |
| [PoWA](https://github.com/powa-team/powa) | 多个仓库组成（powa、powa-archivist、powa-web 及扩展）；本次没有完成各组件许可证/版本逐项闭合，标记 `LICENSE_UNVERIFIED`，尽管该项目有公开持续活动 | PostgreSQL 服务端扩展 + preload 配置 + 专用历史库/WEB/collector；官方安装要求安装扩展、编辑 `shared_preload_libraries`、重启并创建扩展/数据库 | 会修改服务端软件与配置、需重启；完整功能示例使用 superuser。与只读客户端工具的部署/权限边界相反 | **不适合 Toolkit 集成。** 如果企业另行采用，应作为独立平台项目并先完成厂商/许可证、可用性、采集负载和安全审查 |
| [Oracle Carlos Sierra cscripts](https://github.com/carlos-sierra/cscripts) | 仓库根目录列表未发现明确 LICENSE 文件，故为 `LICENSE_UNVERIFIED`；仓库含多类 SQL/SQL*Plus 脚本 | 通常从 Oracle 主机/SQL*Plus 使用；README 写明以 `SYS` 连接使用。脚本含 AWR/ASH、cursor 分享诊断、SQL profile 等不同风险类别 | SYS 要求与 Toolkit 专用普通监控账号原则冲突；脚本清单中存在 stats gather、profile 等改变状态的实用工具；AWR/ASH 还受 Oracle Pack 许可条件约束 | **不复制、不由 Toolkit 执行。** 仅可由 Oracle DBA 人工选择、验证许可及用途；本次未逐脚本审查 |
| Oracle AHF / ORAchk / TFA / SQLT | AHF 是 Oracle 官方诊断套件，不是本次可认定的开源可再分发组件；Oracle 文档称 AHF 随现有 Support Contract 提供、无额外 AHF 许可费，最新工具包来自 My Oracle Support | 主机侧安装和扫描 Oracle 软件栈；AHF 用户指南要求在受支持 OS 安装，获取/升级依赖 Oracle Support 分发 | 可能需要 Oracle 主机/系统权限和较广的系统级采集；不等于可由普通数据库只读用户运行。AWR/ASH/ADDM/SQL Monitor 等数据库 Pack 使用仍须按具体数据库授权，独立于 AHF 本身 | **只作企业 DBA 工具参考，不集成、不打包。** 离线包可用性/企业授权流程需企业自行核验；不得因 AHF“无额外费”推导 AWR/ASH 无需授权 |
| [Oracle Database Tools sample repo](https://github.com/oracle/oracle-db-tools) | Oracle 公开仓库提供 MIT License | 示例代码与 SQL Developer、Data Modeler、ORDS、DBaaS 等集成样例，不是 Oracle 慢 SQL/锁阻塞/执行计划诊断库 | 仓库声明当前不接受外部贡献；不构成可复用诊断能力或运行支持承诺 | 许可证可查，但**不作为诊断集成候选**；可以作为官方样例来源之一 |

上表维护状态为审计时公开页面所见快照，后续版本和许可证都可能变化。许可证兼容意见是工程初筛，不是法律意见；任何源码复制、二进制再分发或链接前都必须做具体版本的法务/开源审批。

## 许可证、权限和离线部署总判断

| 候选类型 | 需要 Toolkit 端额外运行时 | 目标数据库端安装/修改 | 能否完全离线 | 权限与安全判定 |
|---|---|---|---|---|
| 第三方在线 Collector/存储过程 | 有些需额外 Python/Perl 或外部 SQL 包 | 有，特别是 FRK、sp_WhoIsActive、PoWA | 下载后可离线，但须有固定版本和完整依赖归档 | 与当前 Registry/Adapter/Preflight/Report 数据模型重复；服务端对象及权限难以由统一只读契约覆盖 |
| 离线日志分析器 | Perl/操作系统命令及固定发行包 | 无 | 可以，前提是工具与其全部依赖离线归档；本次未在本机验证 | 不需要 DB 权限；日志与生成报告属于敏感文件，且某些 PT 功能有写库选项 |
| Oracle AHF/AWR/ASH | Oracle 主机侧工具和 Oracle Support 分发 | AHF 需主机安装；AWR/ASH 查询由数据库特性提供 | 离线状态需企业提供受支持安装包与 entitlement；`OFFLINE_UNVERIFIED` | AHF 安装者/DBA 权限与普通监控角色不同；Diagnostics/Tuning Pack 按数据库许可 |

项目已有 wheelhouse/bootstrap 的运行环境模型适合将来承载**独立、可选、版本锁定的本地日志分析器**，但不应将第三方 analyzer 的 Perl/RPM/DEB/Windows 依赖塞入 Toolkit 的 `all-db` 默认 extras。每个 OS、Python/Perl 版本和 CPU 架构必须准备匹配离线包；不得在目标机联网回退。当前没有为这些工具准备或验证 wheelhouse/installer，因此均标记 `OFFLINE_UNVERIFIED`。

权限仍应由 Toolkit 的 Permission Registry 和 Preflight 管理。第三方工具的“需要更高权限才能显示所有字段”不能促使 Toolkit 扩大账号权限：无权限时保留 PARTIAL/UNKNOWN，报告具体缺失能力即可。外部安装型工具需要的 `CREATE/ALTER PROCEDURE`、superuser、SYS 等权限属于 Toolkit 不应索取的权限。

## 推荐集成架构

| 方案 | 方式 | 评估 |
|---|---|---|
| A. 核心 SQL/规则借鉴 | 不运行第三方程序；比较公开、许可证明确的诊断思路，转化为 Toolkit 自有 Registry SQL、Permission Registry、Profile、测试和报告说明 | **推荐。** 与现有固定 SQL、跨版本 profile、最小权限和 Fake/Integration/System 测试一致。若复制代码或 SQL，仍必须单独审查许可证/版权和改动范围 |
| B. Toolkit 内直接执行第三方 SQL / SP | 在 Adapter 中调用外部过程或复制脚本 | **不推荐。** 会绕过或重复 Registry、版本路由、权限检查、timeout/max_rows、结果模型和安全审计；安装型过程还需数据库 DDL |
| C. subprocess 调用外部工具 | Toolkit 启动 pt-query-digest、pgBadger 等 | **当前不推荐。** 引入命令注入、PATH/平台、许可证、依赖锁、错误/输出脱敏、超时与进程清理等边界；工具自带写模式必须逐项封禁 |
| D. 独立可选离线文件分析 | DBA 显式选择本地日志文件，独立 CLI 生成受限本地报告；Toolkit 当前不自动调用 | **可规划试点。** 先评估 pgBadger 和 pt-query-digest 的只读日志模式、Windows/Linux offline package、输出脱敏和文件权限。将来若集成，需新增明确的用户确认/参数和外部工具运行时契约，不扫描日志目录、不自动上传 |

### 慢 SQL / 计划路线

1. 继续把在线诊断事实源保持在 Toolkit 的受审查 Registry：固定 SQL、版本 profile、Permission Registry、Preflight 和 schema/parser contract 一起审查。
2. 计划支持按证据来源分级：缓存/当前请求中服务器**已有**计划可在固定身份、权限、版本能力、大小上限和只读查询满足时采集；不可将“SQL 运行计划”解释为“执行 Toolkit 用户 SQL”。
3. PostgreSQL/MySQL/Oracle 只有在确认服务端内置视图/已有统计能在不执行目标业务 SQL、不创建计划表、不更改配置的条件下提供可信计划时，才增加版本化 profile。`EXPLAIN ANALYZE`、SQL Server `SET SHOWPLAN` 的不透明文本拼接、Oracle `EXPLAIN PLAN` 的 plan table 写入均不得作为默认跨库路线。
4. Oracle SQL 归因必须持续区分 `SQL_ID`、`CHILD_NUMBER`/child cursor、`PLAN_HASH_VALUE`、实例/容器与采样时间。V$SQL 是共享池中可能被刷新/多 child 的瞬时缓存；文本、计划统计、等待事件的可见性受权限、版本及采集条件影响。AWR/ASH/ADDM 或 SQL Tuning Advisor 不可默认为基础监控账号可用或已获许可。
5. 对照计划应明确比较对象：同一 SQL_ID 下不同 child、不同 PLAN_HASH_VALUE、同一语句前后缓存计划，或不同环境中的计划。要求标明 SQL 文本哈希/字面量脱敏、child cursor、plan hash、时间窗、数据库版本及计划来源；不得以 SQL_ID 相同就断言计划完全等价。

## 分阶段建议

| 阶段 | 范围 | 进入条件 / 完成证据 |
|---|---|---|
| 0. 审计基线整理 | 校正执行计划设计稿与当前代码状态；将外部上游版本、许可证、source URL 记录在候选清单；逐一检查 Registry SQL 与权限定义 | 文档与代码对齐；每条候选有精确 tag/commit、许可证、组件清单、维护负责人和弃用方案 |
| 1. 不引入依赖的知识借鉴 | 只完善现有诊断字段、解释、离线报告和版本兼容，不复制第三方代码；为新增 DMV/视图同步权限与 preflight 测试 | SQL 静态安全审查、旧版本 compatibility、Fake Adapter、权限一致性测试通过；SQL 不触发 DDL/DML |
| 2. 可选离线文件分析 PoC | 由 DBA 手工提供单个日志文件；独立启动命令/包装，不默认安装、不自动发现，不连接数据库 | 固定签名/校验和版本；许可证审查完成；Windows 与 Linux 离线依赖包通过；大文件资源上限、脱敏、临时文件清理与自包含报告验收 |
| 3. 外部工具适配决策 | 仅对 PoC 证明有明显 Toolkit 不具备的增益者评估结果模型适配；默认保留原始工具独立运行 | 决策记录覆盖权限、写入面、性能、漏洞响应、CVE、升级/回滚、离线分发、支持责任和运营成本。未经通过不得纳入核心 |
| 4. Oracle 计划能力单独立项 | Oracle V$SQL/V$SQL_PLAN/DBMS_XPLAN 已有 cursor 路线调查；不包括 AWR/ASH 授权功能 | Oracle 版本矩阵、child cursor/plan hash 关联、权限最小化、实例/RAC/CDB/PDB 语义和真实环境 E2E 完成后，才可将 feature 标记 SUPPORTED |

## 阻断项和待核实项

- **`LICENSE_UNVERIFIED`：** PoWA 多组件发行物逐组件许可证尚未闭合；Oracle `cscripts` 未见可确认的 LICENSE 文件。两者均不能直接集成或再分发。
- **`OFFLINE_UNVERIFIED`：** 本机没有准备/测试 pgBadger、Percona Toolkit、PoWA 或 Oracle AHF 的完整 Windows/Linux 离线依赖包；候选工具均未安装。
- **`EXTERNAL_EVIDENCE_NOT_AVAILABLE`：** 本次没有核对每个上游仓库所有脚本的精确权限、数据库写操作、运行负载、CVE 和兼容矩阵；对安装型工具只确认整体部署模型与明显安全边界，不能据此批准任一具体脚本上生产。
- **Oracle 许可：** 本报告未核验用户所管理数据库的订阅/授权合同。Oracle 官方许可资料明确将 AWR、ASH、ADDM、相关包/API 纳入 Diagnostics Pack 范围；SQL Tuning Advisor/Real-Time SQL Monitor 等涉及 Tuning Pack。必须由客户许可负责人逐项确认，不允许 Toolkit 自动调用。
- **Oracle 实机：** 当前仓库验收记录中 Oracle 本地实例因 `ORA-01045` 缺 `CREATE SESSION`，没有真实 Collector/slow/plan E2E 证据；listener banner 不是数据库查询得到的版本确认。
- **旧版本兼容：** SQL Server 2008 R2、PostgreSQL 13、MySQL 5.7、Oracle 11gR2 等设计支持范围没有在本次历史系统报告中形成完整真实版本矩阵。静态兼容/Mock 测试不能替代实机验证。
- **上游快照可能变化：** 发行版本、项目活动、依赖、许可证文件和下载渠道会变化；正式采用必须固定不可变版本/校验和并再次审查，不能使用 `main`、`master`、`latest` 自动更新。

## 最终决策

**GO（受限）：** 继续维护 Toolkit 原生只读 Registry/Profile/Permission 架构；可启动 pgBadger 与 pt-query-digest 的离线日志文件 PoC 设计，但先不安装、不调用、不加入默认依赖。

**NO-GO（本版本）：** 不将 First Responder Kit、sp_WhoIsActive、pg_activity、PoWA、Oracle cscripts、AHF/ORAchk/TFA/SQLT 当作 Toolkit 自动集成依赖；不在 Toolkit 执行第三方 SQL/脚本，不接受第三方安装步骤成为 Toolkit 的前置条件，不使用需要更高权限或收费 Pack 的能力作为 READY 判据。

**Oracle plan：** 保持 `UNSUPPORTED`/未实现，不因外部工具成熟或 Oracle SQL_ID 可见就宣称 Toolkit 已支持计划诊断。后续只有完成版本与权限 profile、静态 SQL 审查和真实只读 E2E 后才可提升支持状态。

### 主要上游来源

- SQL Server [First Responder Kit README / 安装与版本说明](https://github.com/BrentOzarULTD/SQL-Server-First-Responder-Kit)；[MIT License](https://github.com/BrentOzarULTD/SQL-Server-First-Responder-Kit/blob/dev/LICENSE.md)；[releases](https://github.com/BrentOzarULTD/SQL-Server-First-Responder-Kit/releases)。
- [sp_WhoIsActive source and compatibility header](https://github.com/amachanic/sp_whoisactive/blob/master/sp_WhoIsActive.sql)；[author license note (GPLv3)](https://whoisactive.com/docs/03_license/)；[releases](https://github.com/amachanic/sp_whoisactive/releases)。
- Percona [Toolkit repository/license](https://github.com/percona/percona-toolkit)；[official pt-query-digest manual](https://docs.percona.com/percona-toolkit/pt-query-digest.html)；[changelog](https://github.com/percona/percona-toolkit/blob/3.x/Changelog)。
- PostgreSQL [pg_activity repository and usage](https://github.com/dalibo/pg_activity)；[manual](https://github.com/dalibo/pg_activity/blob/master/docs/man/pg_activity.pod)。
- [pgBadger repository](https://github.com/darold/pgbadger)；[license text](https://github.com/darold/pgbadger/blob/master/LICENSE)；[releases](https://github.com/darold/pgbadger/releases)。
- PoWA [quickstart deployment requirements](https://github.com/powa-team/powa/blob/master/docs/quickstart.rst) and [project organization](https://github.com/powa-team).
- Oracle [AHF overview and support contract](https://docs.oracle.com/en/engineered-systems/health-diagnostics/autonomous-health-framework/); [AHF installation](https://docs.oracle.com/en/engineered-systems/health-diagnostics/autonomous-health-framework/ahfug/get-started/installing-ahf-on-a-single-instance-system.html); [Oracle Database 26 licensing information](https://docs.oracle.com/en/database/oracle/oracle-database/26/dblic/Licensing-Information.html); [Oracle sample tools MIT repo](https://github.com/oracle/oracle-db-tools).
