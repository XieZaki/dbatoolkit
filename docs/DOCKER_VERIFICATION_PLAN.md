# SQL Server 隔离验证环境方案（已授权初始化）

2026-10-12 用户明确授权：“允许仅隔离测试容器的初始化例外”。此例外只适用于下述全新空测试容器；Toolkit 和生产规则保持只读。

本机 Docker Desktop 4.54.0 / Engine 29.1.2 正常，Linux x86_64，16 CPU、约 15 GiB 内存。当前没有运行容器，也没有 SQL Server 镜像；已有 nginx/mysql 镜像不作为本轮验证目标。

拟使用一个全新的 SQL Server 2022 Developer 容器 `dba-toolkit-v021-sqlserver`，仅绑定 `127.0.0.1:11433`，使用独立临时容器网络，不挂载现有数据库目录、不使用生产 Inventory、不创建生产连接。测试完成停止测试容器，保留无密码的结果记录。

客户端在独立临时 Linux 容器中安装 pyodbc 和 Microsoft ODBC Driver 18，挂载本项目源码只读；生成的测试配置仅包含容器地址、端口、master、credential_alias 和 enabled。不会安装或修改 Windows 系统驱动。容器镜像/依赖准备需要联网；这只用于本机开发测试，不改变生产离线部署规则。

SQL Server 官方容器首次启动需要设置初始化管理员密码。创建 `toolkit_monitor` 专用账号及其只读诊断授权需要一次管理员初始化，这是数据库写操作，不能根据此前的只读验证授权自动执行。

若用户明确批准例外，该初始化仅在全新空测试容器中进行：创建监控登录、master 用户，授予 SQL Server 2022 诊断需要的 VIEW SERVER PERFORMANCE STATE、VIEW ANY DATABASE，及必要的只读元数据访问。初始化不调用 Toolkit、不加入 Toolkit 的 SQL Registry；管理密码不提供给 Toolkit。不得通过降低 Preflight 标准使管理员账号通过。

实测发现 master 的 public 角色存在默认对象 EXECUTE 授权。空容器初始化对专用监控用户执行数据库级 `DENY EXECUTE`，减少继承权限；该操作仅属已授权的空测试账号准备。Toolkit 不执行 DENY，生产账号仍由 DBA 手工配置。Preflight 保留有效 EXECUTE 和任何授权转授能力的拒绝；仅对明确被 DENY 覆盖的对象级 EXECUTE GRANT 消除误报，权限判定不确定时仍拒绝。

Toolkit 实机验证全程仅使用 `toolkit_monitor`，执行现有 Registry 中的已审查只读脚本，依次验证 test-connection 和五类 Collector。账户不足只报告，不在采集期间自动追加授权。所有测试凭据临时生成，禁止写入源码、Inventory、日志或报告。SQL Server 容器启动环境含临时初始化凭据，不输出完整 docker inspect 配置。

记录实际服务器/pyodbc/ODBC版本，每次采集的 success、elapsed_ms、row_count、truncated 和错误类别。max_rows 可对 server_info 使用独立测试元数据配置验证；超时继续采用 Fake 或低风险方式，不构造负载、不执行 WAITFOR/写操作/KILL。检查连接关闭和日志脱敏，再运行现有质量检查。

该流程不验证 Windows 本机 ODBC 实际安装，也不验证 SQL Server 2008 R2；报告中必须分别标记 Linux Docker 实测范围和仍未验证范围。

显式运行 `tests/integration/run_docker_verification.ps1` 才会执行该流程，普通 pytest 不调用。客户端使用 Microsoft 的 Python 3.12 Debian 镜像，安装依赖只发生在客户端镜像中。结束后停止并删除本次创建的两个容器及独立网络，保留镜像缓存和 `.tmp/docker-verification.json` 脱敏记录。遇到同名现有资源时拒绝替换。

官方容器说明：[Microsoft SQL Server Docker quickstart](https://learn.microsoft.com/en-us/sql/linux/quickstart-install-connect-docker)。
