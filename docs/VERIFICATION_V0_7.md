# V0.7 Oracle 验证记录（2026-10-15）

目标：新增 Oracle 12c+ 只读连接、采集和 Slow Diagnose。普通测试使用 Fake Driver，不连接数据库；本记录区分 Mock、SQL*Plus 实测和未完成的驱动端到端验证。

## 隔离测试环境

- 本机 Docker Desktop 内已有官方 `container-registry.oracle.com/database/free:latest` 镜像和健康的 `oraclefree` 容器，端口 1521；`V$INSTANCE.VERSION=23.0.0.0.0`、`PARALLEL=NO`，PDB 为 `FREEPDB1`。
- PDB 已有 CHINOOK、CO、HR、SH 等示例用户；本轮没有修改这些用户或其对象。仅在该测试 PDB 临时创建独立命名的专用监控账号。测试初始化使用容器内 SQL*Plus 的本地管理连接，由随机临时密码创建账号并人工模拟最小授权；Toolkit 没有执行初始化操作。
- 账号直接拥有 CREATE SESSION 和 9 个 SYS.V_$ 视图的 SELECT，无自有对象；没有角色或写权限。用该账号经 SQL*Plus 连接 PDB，最终 17 条受审查 Oracle SQL 均执行成功，无 ORA/SP2 错误。账号在同一验证流程的 finally 中删除，清理成功。
- 上述验证证实测试版本的 SQL 语法、字段和视图读取权限。由于运行时没有并发测试事务，这次实测未验证有数据时的多层阻塞、空闲未提交事务和 Root Blocker 报告；这些路径由 Fake Driver 单元测试覆盖。

## 驱动与端到端限制

- 当前项目虚拟环境未安装 `python-oracledb`，本机未发现现成离线 wheel。遵守生产无 Internet 与离线 wheelhouse 规则，本轮没有在线下载安装。
- 因此真实 `dba test-connection`、五个 `collect` 和 `diagnose slow` 的 Python Driver 端到端连接尚未完成；不能把 SQL*Plus 成功称为 Toolkit 真实连接成功。
- 补齐匹配 Windows/Python 版本的离线 wheel 后，应在隔离容器使用专用监控账号执行上述 CLI，再在独立测试用户的测试表上用显式事务验证长 SQL、单层/多层阻塞和 INACTIVE + open transaction；完成后 ROLLBACK 并清理测试对象和用户。不得使用生产账号或业务表做此验证。

## 安全与范围

- 生产 Oracle 路径只接受 Registry 签发且复核不变的 READ_ONLY/reviewed SQL，未提供任意 SQL、KILL 或权限变更执行接口。
- 连接使用 python-oracledb Thin、autocommit、连接超时、每脚本 call_timeout、fetchmany(max_rows)、结果截断标记和异常清理；以上驱动行为本轮由 Fake Driver 测试，仍需真实驱动端到端验收。
- 监控预检检查角色、系统权限、直接对象权限、自有对象及核心 V_$ 能力；可选锁/进程/等待视图缺失时标记 capability，锁链降级为 V$SESSION。授权建议仅为文本，绝不自动执行。
- 当前为单实例 V$ 快照；RAC 模式检测后将跨实例图标记不完整，不生成 KILL 参考语句。Oracle 11g 未验证。采样是顺序执行，不是原子快照。

## 自动检查

- `pytest -q`：237 passed（含现有 SQL Server、PostgreSQL、MySQL 回归）。
- `ruff check .`：通过。
- `mypy --strict dba`：通过。
- `python -m dba validate`：0 实际 Inventory 资产、45 条正式 SQL 均通过。
- CLI 启动：`python -m dba --version` 返回 0.7.0，`python -m dba list` 正常。Oracle CLI 分派由 Fake Driver 测试。
