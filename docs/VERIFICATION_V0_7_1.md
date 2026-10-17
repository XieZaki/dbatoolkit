# V0.7.1 Oracle 端到端验证（2026-10-15）

## 环境与边界

- 复用已有、健康的 `oraclefree`，`FREEPDB1` 服务经容器配置和查询确认。数据库版本 `23.0.0.0.0`，单实例。没有创建第二套数据库，也没有修改已有 CHINOOK、CO、HR、SH 等 Schema。
- 临时 Client 使用 Python 3.12.15、python-oracledb 3.4.2 Thin Mode、只读挂载项目源码，与 `oraclefree` 共享 Docker 网络命名空间。驱动只安装在该 Client，Windows 主机未安装。
- 管理员连接仅在隔离测试 PDB 创建和清理 `DBA_TOOLKIT_V071_MON`、`DBA_TOOLKIT_V071_ACT` 及后者的两行锁测试表。监控账号仅有 CREATE SESSION 和九个 SYS.V_$ 视图直接 SELECT；无角色、写授权或自有对象。Toolkit CLI 全程只用该监控账号。密码随机生成、仅在进程内和标准输入传递，未写入 Inventory、源码或报告。

## 真实 CLI 结果

通过 `tests/integration/run_oracle_verification.ps1` 显式启动，`tests/integration/verify_oracle.py` 调用实际 `python -m dba` 入口并经交互式 Credential Provider 输入凭据。结果存于 `.tmp/oracle-v071-verification.json`，仅含脱敏统计。

| 命令 | 结果 | 行数 | 截断 | 耗时 |
| --- | --- | ---: | --- | ---: |
| `test-connection oracle-v071-test` | 成功，预检全部能力通过 | — | — | — |
| `collect oracle-v071-test server-info` | 成功 | 1 | 否 | 17.3 ms |
| `collect oracle-v071-test database-status` | 成功 | 1 | 否 | 17.3 ms |
| `collect oracle-v071-test active-sessions` | 成功，空闲基线 | 0 | 否 | 20.2 ms |
| `collect oracle-v071-test active-requests` | 成功，空闲基线 | 0 | 否 | 17.0 ms |
| `collect oracle-v071-test long-running` | 成功，空闲基线 | 0 | 否 | 43.7 ms |

阻塞场景中重新经 CLI 采集，`active-sessions` 返回 3 行，`active-requests` 返回 2 行；临时将 `long_running_seconds` 设为 1 后，`long-running` 返回 2 行。每次采集均检查 Oracle 审计事件、SQL 名称、timeout/max_rows 元数据。使用临时 SQL Registry 副本将 `active_sessions.max_rows` 设为 1，真实 CLI 返回 1 行且 `truncated=true`。正式 SQL 元数据未修改。

`diagnose oracle-v071-test slow` 在无阻塞基线生成完整 Markdown，明确输出无阻塞。然后由独立测试账号创建 A→B→C 两层行锁等待；A 在未提交事务中保持 INACTIVE。真实 CLI 生成 2 个等待会话、1 个根阻塞者、1 个空闲事务；验证 SID、SERIAL#、用户名、机器、程序、前一条 SQL ID/文本、事务时长、`used_ublk`、`used_urec`、受影响会话数。`IDLE_TRANSACTION` Finding 为 CRITICAL，且包含 BLOCKING、ROOT_BLOCKER、WAITING_SQL。ReferenceCommand 为与实际 SID/SERIAL# 对应的纯文本 `ALTER SYSTEM KILL SESSION`，Toolkit 未执行。Markdown 含 Summary、Concurrency、Long Running SQL、Blocking Tree、Root Blocker、Transactions、Idle Transactions、Session Source、Findings、Recommendations、Reference Commands。

错误凭据的 `test-connection` 返回非零和受控失败结果，CLI 输出未包含提交的密码；两个报告及正常 CLI 输出也未包含测试密码或带密码连接串。诊断过程中无授权变更、KILL 或业务数据修改。此轮未运行高消耗 SQL 强制触发真实 call timeout；审计确认正式采集配置了每脚本有效 timeout，超时失败/清理由 Fake Driver 回归测试覆盖。连接超时参数的真实超时路径也未用故障网络强制触发。

## 清理与适用范围

测试事务在 finally 中 ROLLBACK，三个 Actor 连接关闭；Client 容器、临时 Monitor/Actor 用户及测试表均删除。清理后复核临时用户、表和会话计数均为 0；`oraclefree` 保持健康。未创建临时 Docker 网络。

本记录只证明 Oracle Free 23 单实例、Thin Mode、该测试权限组合下的行为。Oracle 12c/19c、RAC 与真实生产网络尚未实测；V0.7.1 未新增 Analyzer 或诊断功能。

## 回归检查

- `pytest -q`：237 passed。
- `ruff check .`：通过。
- `mypy --strict dba`：36 个源文件通过。
- `python -m dba validate`：0 个实际资产、45 条 SQL 通过。
- CLI `--version`：`0.7.1`。
- 清理后只读复核：临时用户 0、遗留 Session 0、测试表 0；Client 容器不存在。
