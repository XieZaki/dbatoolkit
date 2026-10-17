# V0.6 MySQL 隔离实测

实测环境：Docker `mysql:8.0`，实际服务器版本 8.0.46；客户端 Python 3.12、PyMySQL 1.1.3。测试脚本为 `tests/integration/run_mysql_verification.ps1`。只创建专用临时容器、内部网络、监控账号与测试表；Toolkit 始终使用监控账号执行受审查的只读 SQL。测试事务由隔离夹具创建并回滚。

结果：`test-connection` 和五个 CLI Collector 成功；当前活动 SQL、长 SQL、Sleep 持有 InnoDB 事务、单层及多层锁等待、Root Blocker、`IDLE_TRANSACTION`、Markdown 报告均通过。测试密码未出现在 CLI 输出和报告。测试对象已删除，剩余 Toolkit 会话数为 0，专用测试容器与网络均已删除。机器可读摘要保存在 `.tmp/docker-mysql-verification.json`。

兼容发现：MySQL 默认可能关闭 `events_waits_current` consumer；正式程序按能力选择受审查的无等待查询或线程查询，并标记报告不完整，不修改服务器设置。隔离测试容器为验证完整等待字段而临时启用该 consumer。MySQL 8 的默认密码认证在无 TLS 的测试连接中需要 PyMySQL RSA 可选依赖；已纳入 `mysql` extra。MySQL 8.0.46 的计算时长由 PyMySQL 返回为 Decimal，已转换为整数以支持阈值比较。PyMySQL 默认游标会缓冲全部结果；正式 Adapter 使用流式游标，达到行数上限即断开连接。

本轮只实测 8.0.46。8.4/9.x、5.7、Windows 客户端 TLS 证书链与目标监控账号权限需另行验证；5.7 不在当前支持范围。
