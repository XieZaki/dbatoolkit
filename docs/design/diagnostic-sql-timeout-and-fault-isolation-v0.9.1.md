# 全局诊断 SQL 超时与模块故障隔离 — 架构审查与设计（V0.9.1）

> 任务：`SQL-TIMEOUT-001`（状态以 Backlog 为准）。本文为 2026-11-12 的代码审查与设计稿，**未修改任何 Collector、驱动、Adapter 或 SQL**，未连接数据库。审查结论来自当前工作树源码和 `.venv` 已安装驱动源码/文档字符串，证据类型 `ISOLATED_OFFLINE_TEST`（静态审查）；所有驱动取消行为的数据库端效果均为 `NOT_EXECUTED`，必须由 `SQL-TIMEOUT-CANCEL-VERIFY-009` 在获准 Windows 宿主机验证。
>
> 规则依据：`AGENTS.md` 与 `docs/PROJECT_RULES.md`「全局诊断 SQL 执行超时与故障隔离原则」。`C-11`～`C-13` 已于 2026-11-13 由用户批准，决定见 §7；§1–§2 保留 2026-11-12 的审查基线。`SQL-TIMEOUT-CONFIG-002` 已按 §3 第 1 点实现（见 §9）。

## 1. 当前实现（审查基线）

驱动版本（`.venv`）：pyodbc 5.3.0、psycopg 3.3.6、PyMySQL 1.1.3（`pymysql.__version__` 显示的 2.2.8 是 mysqlclient 兼容号）、python-oracledb 3.4.2 Thin。

公共路径：每个 Adapter 的私有 `_run()` 是唯一执行入口：`registry.authorize` → `cursor.execute` → `registry.fetch_bounded_rows`（逐次 `fetchmany(1)`，行数/字节上限）→ 任何错误都关闭游标并 `_close()` 整条连接 → 返回 `CollectorResult`。没有连接池，每个 Adapter 只持有一条连接。

超时来源：Registry 每个脚本必须声明 `timeout_seconds`（1..60；现有 119 个脚本：10 s ×88、5 s ×26、15 s ×5）；各引擎 Settings 的 `query_timeout` / Oracle `call_timeout` 默认 10 s，范围 1..86400、无全局上限。代码中尚无 `diagnostic_sql_timeout_seconds`。

| 引擎 | 实际超时机制 | 作用范围 | 超时后驱动行为 | 当前分类 | 数据库端是否停止 |
|---|---|---|---|---|---|
| SQL Server | `connection.timeout = min(脚本, query_timeout)` → 新游标的 ODBC `SQL_ATTR_QUERY_TIMEOUT` | 每个 ODBC 调用（execute、每次需等待服务器的 fetch），非整条 SQL 总时长 | ODBC Driver 发送 TDS Attention 并等待服务器确认，返回 `HYT00` | `QueryTimeout`（`normalize_sqlserver_error` 识别 HYT00/HYT01） | 按协议应停止当前批；**未验证** |
| PostgreSQL | 启动参数 `statement_timeout = min(query_timeout, REQUIRED 脚本超时)`，整条连接一个值 | 服务器端单语句执行；客户端游标在 execute 时取回全部结果，fetch 为本地内存 | 服务器自行取消，返回 SQLSTATE `57014`；autocommit 下连接回到空闲 | `QueryTimeout` | 是（服务器端机制）；**未验证** |
| MySQL | 连接参数 `read_timeout = min(query_timeout, REQUIRED 脚本超时)`，整条连接一个值 | 每次 socket recv，非整条 SQL 总时长 | PyMySQL `_force_close()` 后抛 `OperationalError(2013)` | **`QueryFailed`**（只匹配 `TimeoutError`/1205/3024，2013 未匹配） | **否**：只关闭客户端 socket，服务器继续执行到写结果失败或自然结束 |
| Oracle | `call_timeout = min(脚本, call_timeout) × 1000` | 每个数据库 round-trip（arraysize/prefetch 100） | Thin 发送 break，服务器以 ORA-01013 中止；连接可用时 `DPY-4024`，不可用时 `DPY-4011` / `is_session_dead` | **`QueryFailed`**（DPY 错误的 `code` 为 0，只匹配 1013 等数字） | 按协议应中止；**未验证** |

## 2. 现有架构问题

1. **无全局参数，生效规则不统一。** SQL Server/Oracle 按脚本取 `min(脚本, 引擎设置)`；PostgreSQL/MySQL 在连接时按 REQUIRED 脚本取一个连接级值，审计日志写的 `timeout_seconds = min(脚本, 连接值)` 可能不等于实际生效值。引擎设置可配置到 86400 s。
2. **没有 execute + fetch 的单条 SQL 总期限。** 四个驱动都是“每次调用/每次往返”超时，`fetch_bounded_rows` 循环中每次调用都重新获得完整超时。
3. **MySQL 超时不是数据库端取消，且被错分为执行失败。** 违反规则第 5、8、10 条。
4. **Oracle `DPY-4024` 被错分为执行失败**，也未区分 `DPY-4011`（连接已失效）。
5. **PostgreSQL 无客户端网络等待上限。** `statement_timeout` 不覆盖网络中断或服务器无响应；当前未配置 `tcp_user_timeout`/keepalive。
6. **错误结构缺字段。** `CollectorResult` + `database_error` 只有脚本名、耗时、错误类型、脱敏原始错误；缺少执行阶段（Execute/Fetch，当前统一为 `Query`）、配置/生效超时、取消结果、连接处置、脚本相对路径。
7. **报告层无查询超时文案。** `normalizers.py` 只有 `ConnectionTimeout`，`QueryTimeout` 会以通用失败文案展示，无法满足规则第 9、10 条。
8. **模块隔离策略分散。** Slow 四引擎首轮（emergency/required）遇到第一个失败即 `break`，后续采集器不执行；补充轮每次失败后重连一次；disk-growth 有独立重连逻辑并丢弃 `disk_log_legacy` 的 `database_error`；重连失败停止后续采集。这些行为大多安全，但“依赖失败/未执行”状态与 TIMEOUT 没有统一词汇。

已有的正确设计应保留：任何查询错误后关闭整条连接、不复用；后续模块只通过完整 `connect()` + preflight 重连；没有连接池；不执行任何会话终止。

## 3. 统一执行层方案（不重构现有架构）

不新增独立 SQL Executor。四个 `_run()` 已经是唯一执行入口，统一能力以“共享函数 + 各 Adapter 少量接入”实现：

1. **生效超时解析（一处实现）。** 在现有 Settings 文件的共享 `diagnostics:` 段增加全局参数（与 `diagnostics.slow.threshold_seconds` 同一机制），默认 30 s，设上限（建议 ≤ `MAX_SQL_TIMEOUT_SECONDS` = 60）。新增共享函数 `effective_sql_timeout(script, diagnostics)`，四个 Adapter 和审计日志都只用它。与 Registry 脚本超时和引擎 `query_timeout` 的关系待 `C-11` 决定（见 §7）。
2. **单条 SQL 总期限。** `fetch_bounded_rows` 增加可选 `deadline`（monotonic），在两次 fetch 之间检查；超过即抛 `FetchTimeout`。每次阻塞调用仍受驱动超时约束，因此最坏总时长为“期限 + 一次驱动超时”，不需要监控线程，也不以 Python 等待代替数据库取消。
3. **阶段与结果记录。** `_run()` 内维护 `phase ∈ {execute, fetch}`，错误映射输出 `ExecuteTimeout` / `FetchTimeout`，并在 `database_error` 中追加 `timeout` 子对象（增量字段，不改变现有 JSON 契约）：`configured_seconds`、`effective_seconds`、`elapsed_ms`、`phase`、`mechanism`、`cancel_state`、`connection_state`、`script_path`（Registry 相对路径）。
4. **五种超时情形分别标记**（规则要求区分）：

| 情形 | 判定依据 | `cancel_state` | 连接处置 |
|---|---|---|---|
| Python/客户端等待超时（socket、总期限） | 本地计时或 socket 超时，数据库未返回取消错误 | `NOT_CONFIRMED` | 关闭，不复用 |
| 驱动执行超时 | 驱动返回超时（HYT00、DPY-4024） | `DRIVER_REPORTED` | 关闭，不复用 |
| 数据库端已停止 | 数据库返回自身取消错误（PG 57014、MySQL 3024、ORA-01013），或测试中独立观测确认 | `DB_CONFIRMED` | 关闭，不复用 |
| 取消失败 | 取消调用报错、取消后仍收到结果、驱动报告无法中断 | `FAILED` | 关闭；记录关闭结果 |
| 连接失效或状态未知 | `DPY-4011` / `is_session_dead`、MySQL 2013、`08S01`、关闭失败 | `UNKNOWN` | 丢弃；后续仅经完整重连 |

   无论哪种情形都关闭连接，不做“验证后复用”，这是现有行为，最简单也最安全。重连成本是一次 `connect()` + preflight（连接超时 5 s）。

5. **模块隔离。** 保留现有分阶段采集器。统一的只是状态词汇和重连规则：超时模块标记 `TIMEOUT`；同模块依赖步骤标记依赖失败/未执行并写明原因；下一个独立模块前重连一次；重连失败则停止数据库采集，并用已有证据生成 PARTIAL 报告（沿用 `slow_reconnect_failure`）。不改变首轮 `break` 的语义，只要求其后未执行的采集器显式标记为“未执行（前序核心采集失败/超时）”。
6. **报告。** HTML、JSON、CLI 都从同一个 `database_error.timeout` 生成文案；`normalizers.py` 增加 `ExecuteTimeout` / `FetchTimeout` 中文标签和说明，CLI 继续用 `_collector_failure_payload`。显示：数据库类型/版本、模块、脚本相对路径、实际耗时、配置值与生效值、阶段、原生错误码/SQLSTATE、脱敏原始错误、取消结果、连接处置。
7. **不影响业务会话。** 所有机制都作用于 Toolkit 自己的连接（SQL Server Attention、PostgreSQL 服务器端超时/自身 backend key 取消、MySQL 语句提示、Oracle break），不按会话 ID 查找或操作任何会话。

## 4. 各数据库精确取消方式

| 引擎 | 主机制（数据库端） | 辅助/客户端 | 需修复 | 待决 |
|---|---|---|---|---|
| SQL Server | ODBC 查询超时 → 驱动在本连接发送 TDS Attention，服务器确认后返回 HYT00 | 不使用跨线程 `cursor.cancel()` | 记录阶段/取消结果；总期限 | `C-11` |
| PostgreSQL | 启动参数 `statement_timeout`（已有，连接级） | C-12 已决定：客户端网络等待保护（如 libpq `tcp_user_timeout`/keepalive，具体参数在 DEADLINE-004 验证）；首版不发送协议级 CancelRequest；超时后关闭连接 | 网络等待上限；区分 `57014`（服务端确认）与客户端状态未知 | 已决定 |
| MySQL | C-13 已决定：只读 SELECT 使用原生 `max_execution_time` SESSION 设置（5.7.8+/8.0，超时返回 3024） | 4 个 `SHOW` 脚本（`disk_binlogs`、`monitor_grants`、`replication_status`、`replication_status_legacy`）：经独立控制连接，对执行前记录的本连接 `CONNECTION_ID()` 发送 `KILL QUERY`；`read_timeout` 仅作客户端网络上限 | 2013 改分类为“客户端等待超时/连接丢失，数据库端取消未确认” | 已决定；实现细节见 §7 |
| Oracle | `call_timeout` → Thin 发送 break，服务器以 ORA-01013 中止并重置连接 | 不使用跨线程 `connection.cancel()` | 按 `full_code` 识别 `DPY-4024`（可用）与 `DPY-4011`/`is_session_dead`（失效）；总期限 | — |

Socket Read Timeout（MySQL `read_timeout`、PostgreSQL TCP 超时）只说明客户端停止等待，**一律不得记为数据库端已取消**。

## 5. 取消失败时的连接处理

1. 立即放弃当前结果，关闭游标；关闭连接（已有 `_close()`，非 autocommit 时先回滚，回滚失败也关闭）。
2. 关闭失败时记录 `connection_state=UNKNOWN` 和 `CleanupFailed`，提示 DBA 人工核对该监控连接；Toolkit 不终止任何会话。
3. 不重试同一条 SQL，不复用连接（不存在连接池）。
4. 后续独立模块只经完整 `connect()` + preflight 新建连接；新连接失败则停止数据库采集，用已有证据生成报告。
5. 对 MySQL `SHOW` 脚本或任何 `NOT_CONFIRMED`/`UNKNOWN` 情形，报告明确写“数据库端是否仍在执行未确认”。

## 6. 测试方案

证据标签：离线 Fake 驱动 = `ISOLATED_OFFLINE_TEST`；实验室真实库 = `WINDOWS_LOCAL_REAL_E2E`（一次只测用户指定的一个 `environment=test` 资产，Windows PowerShell + `.venv`）。

| ID | 场景 | 离线 | 真实 E2E | 关键断言 |
|---|---|---|---|---|
| T-01 | 正常执行 | 四引擎 Fake | 四引擎 | 结果与现有一致；审计中生效超时等于解析值 |
| T-02 | 配置解析 | 是 | — | 默认 30 s；越界拒绝；不能由模块延长；审计值 = 实际下发值 |
| T-03 | Execute 超过期限 | Fake 驱动抛各自超时异常 | 测试专用慢查询 | `ExecuteTimeout`、阶段、耗时 ≥ 期限、取消状态 |
| T-04 | Fetch 超过期限 | Fake 游标逐行延迟 | 测试专用大结果慢返回 | `FetchTimeout`，未继续 fetch，连接已关闭 |
| T-05 | 数据库端取消成功 | — | 四引擎 | 见下方“数据库端停止验证” |
| T-06 | 取消失败 | Fake：取消/关闭抛错 | 仅在用户另行授权实验室网络中断时 | `cancel_state=FAILED`，连接丢弃，后续经重连 |
| T-07 | 连接状态未知 | Fake：DPY-4011、2013、08S01 | 同上 | `UNKNOWN`，不复用，报告注明 |
| T-08 | 模块失败其他继续 | Fake：第 k 个采集器超时 | 测试资产 | 后续独立采集器执行；重连一次；报告 PARTIAL |
| T-09 | 模块依赖 | Fake | — | 依赖步骤标记依赖失败/未执行，独立模块不受影响 |
| T-10 | 原始错误保留 | 是 | 是 | 原生码/SQLSTATE/脱敏消息保留，不含凭据 |
| T-11 | HTML/JSON/CLI 一致 | 是 | 同次采集 | 三者超时字段一致 |
| T-12 | 报告仍生成 | 是 | 是 | 超时后 HTML/JSON/CLI 都生成且保留已有证据 |
| T-13 | 其他引擎不受影响 | 全量 pytest | — | 每次只改一个引擎时其他引擎测试不变 |
| T-14 | 重连失败 | Fake | — | 停止后续采集，报告生成 |
| T-15 | MySQL 负对照 | — | MySQL | 仅 `read_timeout` 时服务器端查询仍在运行（证明探针能发现“只停等待”）；加提示后不再运行 |

**数据库端停止验证（T-05/T-15）：**

- 慢查询只放在测试专用 Registry 夹具中，必须通过 `check_sql`、只读、并能在有限时间内自然结束，不进入产品 Registry。示例思路：系统视图交叉连接计数、PostgreSQL `generate_series` 计数。测试把期限设为 2–3 s，30 s 默认值由 T-02 验证。
- 在执行前取得 Toolkit 连接自身的标识（SQL Server `@@SPID`、PostgreSQL `pg_backend_pid()`、MySQL `CONNECTION_ID()`、Oracle SID/SERIAL#），由测试夹具的只读查询获取。
- Toolkit 报告超时后，由实验室测试驱动身份的**独立只读连接**在有限窗口内轮询：SQL Server `sys.dm_exec_requests`、PostgreSQL `pg_stat_activity`、MySQL `performance_schema.threads`/processlist、Oracle `V$SESSION`。断言该标识下不再执行测试语句。
- 全程不执行 KILL/CANCEL/TERMINATE。若负对照中查询仍在运行，只等待其自然结束并记录。

## 7. 已批准的决定（2026-11-13）

- **C-11。** `diagnostic_sql_timeout_seconds` 默认 30 秒，作为上限：`effective = min(script_timeout, global_timeout)`，不覆盖较短的单脚本限制；Execute 与 Fetch 共用一个绝对 Deadline，Fetch 不得重新获得完整超时。用户另行选择：引擎 `query_timeout`/`call_timeout` 及 Inventory `connection.query_timeout` 保留为可选的更严格上限，代码默认不设；`config/runtime.yaml` 删除原 10 秒值，使 30 秒默认生效。
- **C-12。** PostgreSQL 使用服务端 `statement_timeout`，增加客户端网络等待保护；首版不加协议级 CancelRequest；超时后关闭连接不复用；区分服务端确认取消与客户端取消状态未知。
- **C-13。** 只读 SELECT 优先使用原生 `max_execution_time` SESSION 设置；SHOW 脚本只允许经独立控制连接，对本次运行实际记录的 `CONNECTION_ID()` 执行 `KILL QUERY`（完整限制见 `PROJECT_RULES.md`「MySQL KILL QUERY 例外」和 `DATABASE_PERMISSIONS.md`）。严禁 `KILL CONNECTION`。

`MYSQL-005` 实施前需要先验证或说明的细节：

1. `SET SESSION max_execution_time = <整数毫秒>` 属于会话状态修改语句，现有静态 SQL 检查禁止 `SET`，Registry 也只接受只读脚本。实现时需要一个固定的 Toolkit 自有设置路径（不经 Registry、不接受任意文本、值只来自有效超时整数），不得放宽 Registry 静态检查。
2. 单脚本有效超时不同（5/10/15 秒）。在每条 SELECT 前设置一次会话值，与连接级设置一次，二者的取舍需在实现时说明。
3. MySQL 5.7.8 以下没有该变量，需版本门控；`max_execution_time` 对存储程序内语句的行为和 5.7/8.0 差异要先验证，不得假设一致。
4. 同一账号可能被其他 Toolkit 运行或人工会话同时使用，因此 `KILL QUERY` 只能针对执行前记录并与本次连接对象绑定的 ID。

## 8. 后续任务与依赖

```text
C-11 ─► CONFIG-002 ─► CLASSIFY-003 ─► DEADLINE-004
                         │               │
C-13 ─► MYSQL-005 ◄──────┤               │
C-12 ─► (PG 部分并入 003/004)            │
                         ├─► ISOLATION-006 ─► REPORT-007
                         └──────────────────► TESTS-008（随 002–007 增量交付）
003+004+005+006 ─► CANCEL-VERIFY-009（真实库，需逐引擎授权）
```

任务定义与状态见 Backlog `SQL-TIMEOUT-*`。每项实施前需用户单独授权；本文不授权任何代码修改。

## 9. CONFIG-002 实现记录（2026-11-13）

- `dba/sql_timeout.py`：`DEFAULT_DIAGNOSTIC_SQL_TIMEOUT_SECONDS = 30`、上限 60（等于 Registry `MAX_SQL_TIMEOUT_SECONDS`）、`effective_sql_timeout()`、`SqlDeadline`（单调时钟绝对期限，提供 `elapsed`/`remaining`/`expired`）。`SqlDeadline` 尚未接入 Fetch 循环，由 `DEADLINE-004` 接入。
- `dba/settings.py`：四个引擎设置都增加 `diagnostic_sql_timeout_seconds`（只能经 `diagnostics:` 段覆盖）和 `effective_sql_timeout()`；`query_timeout`/`call_timeout` 默认 `None`。
- 适配器：SQL Server 与 Oracle 按脚本下发有效超时；PostgreSQL `statement_timeout` 与 MySQL `read_timeout` 仍是连接级值，取各自 REQUIRED 脚本有效超时的最小值（保持原保守语义），审计同时记录实际下发值 `timeout_seconds` 与脚本策略值 `effective_timeout_seconds`。
- 行为变化：默认部署下，SQL Server 与 Oracle 的 3 个 Top SQL 脚本（声明 15 秒）实际下发值从 10 秒变为 15 秒，其他脚本仍为声明值。PostgreSQL 与 MySQL 的连接级值不变，仍为 5 秒（REQUIRED 中 `preflight` 声明 5 秒）；这意味着两者声明 10/15 秒的脚本目前实际按 5 秒受限，这是原有行为，在 `DEADLINE-004` 前保持不变。设置了 Inventory `query_timeout` 的资产继续按该值封顶。
