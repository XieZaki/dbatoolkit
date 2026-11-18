# DBA Toolkit TODO
## 当前待办与行动入口

> 更新日期：2026-11-18
>
> **状态权威来源：** [V0.9.1 Backlog](roadmap/DBA_TOOLKIT_V0.9.1_BACKLOG.md)。本文件是面向日常工作的精简待办视图，不创建第二套任务状态。若本文件与 Backlog 不一致，以 Backlog 为准，并在下次维护时同步。
>
> **当前开发指针：** [Progress](roadmap/DBA_TOOLKIT_V0.9.1_PROGRESS.md)。
> **验收证据：** [Acceptance](roadmap/DBA_TOOLKIT_V0.9.1_ACCEPTANCE.md)。

---

# 1. 当前优先事项

CONFIG-003 已于 2026-11-18 经用户正式验收，关闭依据见 Acceptance §122（引用 §§118–121）。配置与阈值已完成事项不再作为待办；历史证据保留，主线恢复 P5。

## 保留的 Phase 5 HTML Incident Workbench 实机待办

**正式任务：** V091-P5-WORKBENCH-001
**Backlog 状态：** IN_PROGRESS
**当前阶段：** Phase 5

### 待办

- [ ] 按下方续跑计划执行 SQL Server A/B/C/D。独立 WAITFOR、同次报告比对、DMV 偏移及长文本边界都纳入对应场景。
- [ ] 补齐 `V091-P5-LOCK-OBJECT-SQLSERVER-001` 的锁对象实机证据，覆盖同库解析、持有方与等待方，以及跨库或元数据不可见时的降级。
- [ ] 分别生成 MySQL、PostgreSQL、Oracle 实机报告，再核对四库同次采集的 JSON/HTML。
- [ ] 将实际运行的版本、命令、报告、限制和清理结果写入 Acceptance，交用户审阅后再决定是否关闭 P5。

### 已有证据与边界

- Acceptance §55：HTML inline interaction 离线回归。
- Acceptance §56：本地 Chrome 窄视口视觉验收。
- Acceptance §57：四数据库合成 JSON/HTML parity。
- Acceptance §66：SQL Server Slow session query 与数据库原始错误传播离线证据。
- Acceptance §§67、69、75、83：已完成 WAITFOR 分类、界面回归、SQL 文本边界和锁对象离线检查。§63 的旧 SQL Server 实机结果只适用于其记录的范围。
- SQL Server A 的首份 Baseline 保留 JSON 解析失败记录，见 Acceptance §113。新一组实机归档的空闲 Baseline 已通过修复后的 JSON/HTML 离线比较，见 §114。该归档缺少 `verification.json`，完整 Baseline 验收、B/C/D、锁对象及四库真实报告一致性仍待执行。§112 的宿主机 1892 项通过属于离线回归。

### P5 实机续跑计划

以下命令供人工审核后在授权 Windows 宿主机执行。使用 PowerShell Desktop 和项目 `.venv`；核验实际 HEAD 后，逐次显式选择一个获批且启用的 `environment=test` 资产。先核对主机、端口、数据库或服务、实际版本、驱动和凭据可用性；监控与负载账号分离。检查失败时保留脱敏原因。报告和日志只存本地。

```powershell
Set-Location -LiteralPath 'D:\DBA\DBAToolKit'
if ($PSVersionTable.PSEdition -ne 'Desktop') { throw 'Use Windows PowerShell Desktop.' }
$head = git rev-parse HEAD
# Record and review $head against the accepted GitHub baseline before collecting evidence.
$dba = (Resolve-Path -LiteralPath '.\.venv\Scripts\dba.exe').Path
$python = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
& $python -E -c 'import sys; assert sys.platform == "win32"; print(sys.executable)'
if ($LASTEXITCODE -ne 0) { throw 'Project Windows Python was not confirmed.' }
```

**1. SQL Server A/B/C/D。** 沿用 Acceptance §75 的 SQL Server 2022 实验室门禁。现有验收脚本要求 `sqlserver2022`、`127.0.0.1:1433`、`master`、构建 `16.0.4305.5` 及获批监控凭据。只在 `-PreflightOnly` 就绪后继续。计划场景名称与脚本参数的对应关系如下。

```powershell
$slowAcceptance = '.\scripts\acceptance\verify_sqlserver_slow_snapshot.ps1'
$labArgs = @{ Inventory = (Resolve-Path -LiteralPath (Read-Host 'Approved SQL Server master test inventory')).Path }
# 如需获批的现有运行时设置，先设置 $labArgs.Settings。
& $slowAcceptance @labArgs -Scenario Baseline -PreflightOnly
$idle = & $slowAcceptance @labArgs -Scenario Baseline
# 在独立终端启动获批负载，稳定时采集 B。
$busy = & $slowAcceptance @labArgs -Scenario A
# C：仅使用获批普通测试会话的实际 SPID。
$longSpid = [int](Read-Host 'Approved long SELECT SPID')
$waitSpid = [int](Read-Host 'Independent WAITFOR SPID')
$long = & $slowAcceptance @labArgs -Scenario B -WorkloadSessionId $longSpid -WaitforSessionId $waitSpid
# D：在同一有界事务夹具仍持锁且等待方可见时采集。
$rootSpid = [int](Read-Host 'Fixture root SPID')
$victimSpid = [int](Read-Host 'Fixture victim SPID')
$blocked = & $slowAcceptance @labArgs -Scenario C -RootSessionId $rootSpid -VictimSessionId $victimSpid
# 负载终止并正常回滚、关闭会话后，复核两类解除证据。
& $slowAcceptance @labArgs -Scenario D -BaselineJson $blocked.Json
& $slowAcceptance @labArgs -Scenario D -BaselineJson $busy.Json
```

- **A（脚本 Baseline）**：未启动负载，保留 JSON、HTML 和 `verification.json`，确认没有残留签名会话或夹具请求。
- **B（脚本 A）**：在另一终端按 §75 核验负载目标 `AdventureWorks2022` 和独立账号，再按批准的并发预算运行 `& $python .\DBAToolkit-LoadTest\run_sqlserver_load.py --sessions 100 --duration 45 --pause-ms 100 --connect-timeout 5 --query-timeout 20`。核对三类 SQL 模板、分组、累计 CPU/逻辑读与 RUNNABLE。DMV 偏移及实际长文本边界只有在原文和采集字段可核对时才记通过。
- **C（脚本 B）**：使用已有获批的、预计超过 10 秒且有查询上限的只读 SELECT，以及另一普通会话的有界 WAITFOR。记录 SPID、采样时间和两类请求各自的 SQL 分类。没有适用 SQL 或未采到预期窗口时记录 `NOT_EXECUTED` 或证据不足。等待两条语句自然结束并关闭测试会话。
- **D（脚本 C 后接 D）**：复用已审阅夹具 `& $python .\DBAToolkit-LoadTest\run_sqlserver_blocking.py --role mixed --sessions 2 --duration 45`。保留根事务、WAITFOR、等待方、原生锁等待和阻塞边；夹具回滚并关闭后，核对原事务、阻塞边及同身份测试会话均已消失。不要额外执行 KILL。

**2. SQL Server 锁对象（Acceptance §83）。** 沿用 D 的已审查夹具。上述 A–D 脚本固定连接 `master`，适用于 `AdventureWorks2022` 锁资源的跨库降级对照。正向解析须使用用户单独核准、已有元数据可见性的 `AdventureWorks2022` 单资产 Inventory，用下方同次采集命令生成报告。检查 `report.details.lock_resources` 和根阻塞者的 `held_lock_resource_ids`，记录数据库、Schema、表、索引、原生模式及持有/等待角色，并核对 HTML 阻塞页。同库元数据不可见时保留原生资源和原因。没有合格资产或权限时保留缺口。清理按 D 执行，不更改资产或授权。

**3–5. MySQL、PostgreSQL、Oracle 真实报告。** 在同一授权宿主机对三库逐个执行下方采集命令，每次只选择一个已批准资产。MySQL 核对 SQL 来源、线程/事件身份及 Performance Schema 可见性；PostgreSQL 核对 PID、事务、等待/阻塞和 PostgreSQL 视图的可见性；Oracle 核对 Thin、PDB、实例/容器、会话/SQL 身份和现有视图权限。§112 记录的 MySQL 8.4.11、PostgreSQL 16.15、Oracle Free 23.26.3.0.0 只是历史实验版本，实际版本以本次 Preflight 为准。Oracle 使用现有只读监控账号。默认采集自然负载，结束后关闭诊断连接；若另行批准受控夹具，应独立记录回滚与会话退出。空基线可证明输出生成，不能覆盖未出现的阻塞或资源场景。

```powershell
$inventory = (Resolve-Path -LiteralPath (Read-Host 'Approved single-asset test Inventory YAML')).Path
$asset = Read-Host 'Approved asset ID from this Inventory'
$opts = @('--inventory', $inventory, '--sql-root', (Resolve-Path -LiteralPath '.\sql').Path)
# 如需获批的现有运行时设置，追加 --settings 和对应路径。
$metaRaw = & $dba @opts show $asset --json
if ($LASTEXITCODE -ne 0) { throw 'Inventory inspection failed.' }
$meta = $metaRaw | ConvertFrom-Json
if (-not $meta.enabled -or $meta.environment -ne 'test') { throw 'Select an enabled test asset.' }
# 此处人工核对 $meta 的引擎、host、port 和连接目标，再运行下面的连接预检。不输出凭据别名。
# 另核对所选现有凭据来源可用。使用加密会话时先用 dba credentials status --json 确认状态。
$probeRaw = & $dba @opts test-connection $asset --json
if ($LASTEXITCODE -ne 0) { throw 'Connection preflight failed.' }
$probe = $probeRaw | ConvertFrom-Json
if (-not $probe.success) { throw 'Connection preflight did not succeed.' }
# 核对实际版本、驱动和账号安全分类；保存必要且脱敏的出站证据。
& $dba @opts check $asset --json
# 核对 Slow 权限及适用版本；如有受限或失败，先记录并确认可验收范围。
$out = Join-Path (Resolve-Path -LiteralPath '.\reports').Path ('p5-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $out | Out-Null
$jsonPath = Join-Path $out 'slow.json'
$htmlPath = Join-Path $out 'slow.html'
& $dba @opts diagnose $asset slow --quiet --report $htmlPath --json-report $jsonPath
$captureExit = $LASTEXITCODE
if ($captureExit -ne 0) { throw "Preserve the collection evidence at $out." }
if (-not (Test-Path -LiteralPath $jsonPath -PathType Leaf)) { throw "JSON report missing at $out." }
if (-not (Test-Path -LiteralPath $htmlPath -PathType Leaf)) { throw "HTML report missing at $out." }
$null = Get-Content -Raw -Encoding UTF8 -LiteralPath $jsonPath | ConvertFrom-Json -ErrorAction Stop
```

**6. 四库 JSON/HTML 一致性。** 使用 A–D 验收脚本或上面单次 `diagnose slow --json-report ... --report ...` 生成的配对文件。逐对执行 `Get-FileHash -Algorithm SHA256 -LiteralPath $jsonPath,$htmlPath`，保留摘要并用本地浏览器打开 HTML。核对同一采样的资产、版本、结论和完整性，以及可见的 SQL、请求/会话、事务、阻塞链、等待、资源指标、错误码和脱敏。确认报告按预算隐藏的行有对应省略说明，缺失权限和未知原因准确展示。为每对记录 HEAD、资产、版本、采样时间、命令、哈希、差异及清理结果。比较阶段仅访问本地证据文件。

---

# 2. 已完成但需保留边界的工作

以下任务不应重复开发；若发现新问题，使用新的 Bug ID 或任务 ID，并保留原任务范围。

| 任务 | Backlog 状态 | 备注 |
|---|---|---|
| V091-P2-TOPSQL-INTEGRATION-001 | DONE | 用户接受的范围见 Acceptance §63；不代表其他数据库/版本全部验证 |
| SQL-REGISTRY-SAFETY-001 | VERIFIED | 静态安全审计及自动化证据见 Acceptance §58；不等于真实数据库 E2E |
| BUG-WINDOWS-CI-FAILURES-001 | VERIFIED | Windows Actions 修复证据见 Acceptance §47、§60 |

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

- [ ] 高并发无主导慢 SQL 的资源诊断：目标是在没有单一慢 SQL 时，依据数据库内部的并发与等待分布给出结论；依赖 V091-P2-RESOURCE-CONTEXT-CONTRACT-001 及区间增量，不在 HTML 内实现根因逻辑。
- [ ] Sleeping/非根会话的上一条 SQL：目标是让 SQL Server 非根会话（most_recent_sql_handle）、Oracle 非根会话（PREV_SQL_ID）和 MySQL（events_statements_history）提供可靠的上一条 SQL；依赖新增只读 Registry SQL 与字段契约。当前仅 SQL Server/Oracle 根阻塞者和 PostgreSQL 非 active 会话有可靠来源。
- [ ] PostgreSQL（pg_locks）、MySQL（performance_schema.data_locks）、Oracle（V$LOCK）的锁对象与锁模式展示仍待开发。SQL Server 已实现部分的实机缺口见 §1。
- [ ] Slow 报告内嵌 TOP 5 当前 SQL 的真实执行计划：目标是在报告中展示与当前请求按原生标识关联的计划树；依赖现有独立 `plan` 命令的结果契约，不重新执行 SQL，不使用模拟计划。PostgreSQL/MySQL 当前无可关联的计划标识，标记为不支持。
- [ ] 执行计划运行时统计：目标是展示实际行数、执行次数等算子运行统计（如 SQL Server 实际计划/查询分析、Oracle ALLSTATS LAST）；依赖内嵌计划能力及额外只读权限评估。
- [ ] SQL Server 与 Oracle 客户端源端口：目标是采集 client_tcp_port / V$SESSION 端口信息；依赖 Slow Registry SQL 字段变更（当前约定不修改 Registry，需单独授权）。
- [ ] Oracle 与 MySQL 当前执行的 CPU/读取计数：Oracle 需单次执行级统计（V$SQL 子游标累计值不可替代），MySQL 需 events_statements_current CPU_TIME（8.0.28+）；依赖 Registry 字段契约和版本门槛。

**资源指标边界（2026-11-12 确认）。** Slow 报告只展示数据库内部资源证据，包括 CPU 调度等待、内存授予等待、I/O 等待和并发。主机 CPU、MEM、IO 不在 Toolkit 范围：不开发 Zabbix API、操作系统 Agent 或主机资源采集，报告中也不将其显示为待开发。数据库侧存在明确资源竞争证据时，报告提示 DBA 结合 Zabbix 或主机监控人工核实；证据不足时不给出资源瓶颈结论，也不建议扩容。

## 全局诊断 SQL 超时与模块故障隔离（SQL-TIMEOUT-001）

2026-11-12 完成规则落地与架构审查（只读代码审查，未连接数据库，未改 Collector/驱动）。规则见 AGENTS.md 与 PROJECT_RULES「全局诊断 SQL 执行超时与故障隔离原则」，设计与测试方案见 `docs/design/diagnostic-sql-timeout-and-fault-isolation-v0.9.1.md`。任务状态以 Backlog 为准；每项需单独授权，不随 P5 自动启动。

已验收的 CONFIG-002、CLASSIFY-003 和已接受的 DEADLINE-004 部分交付见 Acceptance §§84–87。已批准的 C-11～C-13 见 Backlog §6。
- [ ] SQL-TIMEOUT-MYSQL-005：MySQL 8.4.11 SELECT SESSION、Hint、SSCursor Fetch 实机结果已记录于 Acceptance §93；SHOW 自动取消身份竞态仍 BLOCKED，没有执行 KILL QUERY。按 Backlog 状态保持 BLOCKED，不扩大权限。
- [ ] SQL-TIMEOUT-ISOLATION-006：统一 TIMEOUT/依赖失败/未执行状态和「重连一次」规则，不改变分阶段采集结构。依赖 003。
- [ ] SQL-TIMEOUT-REPORT-007：HTML/JSON/CLI 一致展示超时证据，增加查询超时文案。依赖 003、006。
- [ ] SQL-TIMEOUT-TESTS-008：故障隔离与报告完整性离线测试（设计 §6 T-01～T-14），随 002～007 增量交付。
- [ ] SQL-TIMEOUT-CANCEL-VERIFY-009：四库超时证据与缺口见 Acceptance §112，任务状态见 Backlog。SQL Server 服务端停止未验证，MySQL SHOW 自动取消和 PostgreSQL 网络 Fetch 保留既有阻塞边界。Oracle 基础客户端超时与连接恢复已在指定实验环境验证，服务端停止仍为 NOT_CONFIRMED。
- [ ] Oracle Net 非阻塞研究：历史 DPY-4011 的驱动分支、A2.2 的后续 PING 错误归因与连接复用、A2.3 恢复再次超时。仅维护 acceptance 实验，不影响 V0.9.1 主线。

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
