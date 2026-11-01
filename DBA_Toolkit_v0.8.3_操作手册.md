# DBA Toolkit V0.8.3 操作手册

> 适用版本：DBA Toolkit 0.8.3<br>
> 核心原则：**Toolkit 负责检查和诊断，DBA 负责最终决策。**

## 1. 日常只需要记住这几个命令

### 查看数据库资产

```powershell
.\.venv\Scripts\dba.exe list
```

### 检查某套数据库是否可以正常使用

```powershell
.\.venv\Scripts\dba.exe check <database_id>
```

例如：

```powershell
.\.venv\Scripts\dba.exe check sqlserver2022
```

`check` 会自动检查：

```text
Runtime
→ Credential
→ Connection
→ Preflight
→ Permissions
→ Feature readiness
```

### 查看数据库当前状态

```powershell
.\.venv\Scripts\dba.exe status <database_id>
```

### 业务反馈数据库慢

```powershell
.\.venv\Scripts\dba.exe diagnose <database_id> slow
```

### 磁盘、日志、WAL、Binlog、FRA 异常增长

```powershell
.\.venv\Scripts\dba.exe diagnose <database_id> disk-growth
```

---

## 2. 最推荐的日常使用流程

```text
能不能用？
→ check

现在怎么样？
→ status

为什么慢？
→ diagnose slow

空间为什么涨？
→ diagnose disk-growth
```

---

## 3. 第一次配置数据库

正式资产放在：

```text
config/inventory/
```

建议结构：

```text
config/inventory/
├── sqlserver/
├── postgresql/
├── mysql/
├── oracle/
├── mongodb/
└── redis/
```

SQL Server 示例：

```yaml
defaults:
  db_type: sqlserver
  port: 1433
  environment: test
  credential_alias: sqlserver-test-monitor
  enabled: true

databases:
  - id: sqlserver2022
    name: SQL Server 2022 Docker
    host: 127.0.0.1
    database: master

    group: local-test

    tags:
      - docker
      - sqlserver
      - test

    connection:
      encrypt: false
      trust_server_certificate: true
      connect_timeout: 5
      query_timeout: 10
```

`group` 是主分组，`tags` 是多标签，只用于资产分类和筛选，不参与连接。

---

## 4. 保存监控账号凭据

第一次：

```powershell
.\.venv\Scripts\dba.exe credentials set sqlserver-test-monitor
```

Windows 使用 Windows Credential Manager 保存凭据。

以后正常执行：

```powershell
.\.venv\Scripts\dba.exe check sqlserver2022
```

不需要每次重新输入密码。

查看凭据：

```powershell
.\.venv\Scripts\dba.exe credentials list
.\.venv\Scripts\dba.exe credentials show sqlserver-test-monitor
.\.venv\Scripts\dba.exe credentials test sqlserver-test-monitor
```

更新密码：

```powershell
.\.venv\Scripts\dba.exe credentials set sqlserver-test-monitor
```

删除：

```powershell
.\.venv\Scripts\dba.exe credentials delete sqlserver-test-monitor
```

密码不会写入 Inventory、日志或报告。

---

## 5. 修改 Inventory 后先校验

```powershell
.\.venv\Scripts\dba.exe validate
```

例如：

```text
Validation OK: 4 instances, 68 SQL scripts
```

---

## 6. 查看单个实例配置

```powershell
.\.venv\Scripts\dba.exe show <database_id>
```

重点看：

- host
- port
- database / service
- credential_alias
- enabled
- effective_connection

---

## 7. 权限检查

日常直接：

```powershell
.\.venv\Scripts\dba.exe check <database_id>
```

已经包含权限摘要。

详细检查：

```powershell
.\.venv\Scripts\dba.exe permissions <database_id>
```

生成单模块最小权限：

```powershell
.\.venv\Scripts\dba.exe permissions <database_id> --feature slow --generate-grant
```

生成全部 Feature 权限：

```powershell
.\.venv\Scripts\dba.exe permissions <database_id> --all-features --generate-grant
```

生成回收脚本：

```powershell
.\.venv\Scripts\dba.exe permissions <database_id> --all-features --generate-revoke
```

**Toolkit 只生成 GRANT/REVOKE 文本，不会执行。**

---

## 8. `status` 和 `collect` 的区别

日常优先：

```powershell
.\.venv\Scripts\dba.exe status <database_id>
```

如果明确只想查某一项，再用 `collect`：

```powershell
.\.venv\Scripts\dba.exe collect <database_id> server-info
.\.venv\Scripts\dba.exe collect <database_id> database-status
.\.venv\Scripts\dba.exe collect <database_id> active-sessions
.\.venv\Scripts\dba.exe collect <database_id> active-requests
.\.venv\Scripts\dba.exe collect <database_id> long-running
```

记忆方式：

```text
不知道问题在哪
→ diagnose

明确知道要查什么
→ collect
```

---

## 9. SQL Server 执行计划

```powershell
.\.venv\Scripts\dba.exe diagnose <database_id> plan --session <session_id>
```

例如：

```powershell
.\.venv\Scripts\dba.exe diagnose sqlserver2022 plan --session 217
```

Toolkit 不会自动创建索引、修改 SQL、Force Plan 或修改参数。

---

## 10. Doctor 是什么

```powershell
.\.venv\Scripts\dba.exe doctor
```

`doctor` 检查的是：

> 这台机器是否准备好运行 DBA Toolkit。

包括：

- Python
- 数据库 Python Driver
- SQL Server ODBC
- Credential Store
- 当前 Inventory 所需运行时

只检查 SQL Server：

```powershell
.\.venv\Scripts\dba.exe doctor --database sqlserver
```

---

## 11. runtime-info

```powershell
.\.venv\Scripts\dba.exe runtime-info
```

适合环境迁移、升级、驱动问题和故障排查。

---

## 12. Human 输出和 JSON 输出

默认：

```powershell
.\.venv\Scripts\dba.exe check sqlserver2022
```

是 DBA 可读输出。

程序处理时：

```powershell
.\.venv\Scripts\dba.exe check sqlserver2022 --json
```

其他命令同理。

---

## 13. 日志

结构化日志：

```text
logs/dba.jsonl
```

正常终端不会再刷大量 JSON。

---

## 14. 常见状态

- `READY`：可以正常使用
- `READY_WITH_WARNING`：可以用，但有非阻塞告警
- `PARTIAL`：部分功能可用
- `MISSING`：缺 Driver、Credential、权限等
- `FAILED`：连接或执行失败
- `UNSUPPORTED`：当前类型/版本不支持
- `UNKNOWN`：无法可靠判断

---

## 15. 常见错误

### Credential 不存在

```powershell
.\.venv\Scripts\dba.exe credentials set <credential_alias>
```

### Driver 缺失

```powershell
.\.venv\Scripts\dba.exe doctor --database <db_type>
```

### 权限不足

```powershell
.\.venv\Scripts\dba.exe permissions <database_id>
```

Toolkit 会告诉你：

- 缺什么权限
- 影响什么功能
- 建议 GRANT

### PrivilegedAccount

表示监控账号权限过高。Toolkit 会显示触发的 role / permission / source / scope / object。

不要直接改用 `sa`、`root`、`SYS`、`SYSTEM`、PostgreSQL superuser。

---

## 16. 安全边界

Toolkit 可以自动执行：

- SELECT
- DMV / 系统视图查询
- 状态采集
- 执行计划读取

Toolkit 可以生成但不执行：

- KILL
- cancel
- terminate
- GRANT
- REVOKE

Toolkit 不自动执行：

- UPDATE / DELETE / INSERT
- ALTER / DROP / TRUNCATE
- CREATE INDEX
- SHRINK
- PURGE
- FAILOVER / SWITCHOVER
- 参数修改
- 表空间扩容

---

## 17. 环境迁移

不要复制旧 `.venv`。

推荐流程：

```text
复制项目
→ 准备 wheelhouse
→ bootstrap
→ doctor
→ credentials set
→ validate
→ check
```

Windows：

```powershell
.\deployment\windows\bootstrap.ps1 -Offline
```

Linux：

```bash
./deployment/linux/bootstrap.sh --offline
```

---

## 18. 最简速查表

```text
查看数据库
  dba list

检查这个库能不能正常用
  dba check <id>

查看数据库现在怎么样
  dba status <id>

业务说慢
  dba diagnose <id> slow

磁盘 / 日志增长
  dba diagnose <id> disk-growth
```

Windows 当前项目：

```powershell
.\.venv\Scripts\dba.exe list

.\.venv\Scripts\dba.exe check sqlserver2022

.\.venv\Scripts\dba.exe status sqlserver2022

.\.venv\Scripts\dba.exe diagnose sqlserver2022 slow

.\.venv\Scripts\dba.exe diagnose sqlserver2022 disk-growth
```

---

## 19. 最重要的四句话

```text
能不能用？
→ check

现在怎么样？
→ status

为什么慢？
→ diagnose slow

为什么空间涨？
→ diagnose disk-growth
```

其他命令都属于高级功能，日常不用全部记住。
