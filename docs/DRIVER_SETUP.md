# Windows 绿色便携版与数据库驱动

中文操作手册：[用户操作手册](用户操作手册.md)；升级与回退：[UPDATE_GUIDE.md](UPDATE_GUIDE.md)；故障处理：[故障排查.md](故障排查.md)。

本手册说明 Windows x64 离线 Full ZIP、独立 Update ZIP、驱动检查和替换。当前产品版本仍为 `0.9.0`。Full 包使用 CPython Embeddable Runtime 和普通可导入的 `dba` 应用目录；四种 Python 数据库驱动位于独立、带版本号的 `drivers/<database>/versions/`，通过本地 `driver.json` ZIP 显式安装。程序本身不下载、不调用 pip，也不自动执行系统安装器。

## 首次使用

1. 从经批准的分发位置下载 `DBAToolkit-0.9.0-Windows-x64-Full.zip` 和对应 `SHA256SUMS.txt`。
2. 在 Windows PowerShell 使用 `Get-FileHash -Algorithm SHA256` 核对 ZIP，再解压到本机目录。不要解压到网络共享目录。
3. 双击 `启动.bat`。也可从终端运行 `dba.bat --version`、`dba.bat validate` 或任意已有 `dba` 命令。
4. 在 `config/inventory.yaml` 或 `config/inventory/` 配置本站 Inventory；便携包初始 Inventory 为空，不包含开发机或真实资产。`examples/inventory/` 中的示例均禁用；复制到站点配置前仍需按本站要求编辑。
5. 初次启动检查 Python、核心依赖、受管驱动和 SQL Registry。缺失单一数据库驱动会提示，但不会阻止其余数据库的本地命令运行。

便携包不需要目标电脑安装 Python、pip、虚拟环境或互联网连接。程序文件、驱动和便携用户数据位于解压目录；Credential Store 路径继续遵循现有 Windows Credential Manager/Toolkit 用户级规则，不搬入或重写便携目录。

## 驱动清单和命令

默认驱动保持现有选择：MySQL 使用 PyMySQL，PostgreSQL 使用 psycopg 3，Oracle 使用 python-oracledb Thin，SQL Server 使用 pyodbc。初始 ZIP 为每种关系数据库附带各自独立的 driver bundle。驱动包的 Python 版本、ABI、Windows 架构、模块/分发版本、最低 Toolkit 版本、依赖和原生组件会在安装前校验；模块必须从选中的包目录导入，模块自报版本若存在也必须与分发元数据一致。

```powershell
.\dba.bat driver list
.\dba.bat driver check
.\dba.bat driver install --file '.\driver-packages\mysql-driver.zip'
.\dba.bat driver rollback --database mysql
```

驱动 ZIP 必须来自可信、经审核的离线分发渠道；驱动是可执行 Python/native 代码，清单和哈希本身不是发布者数字签名。安装程序会拒绝不匹配的 ABI/架构、错误清单、路径越界、重复路径、链接、特殊文件、损坏导入及超限包。每个数据库的版本目录独立保存，只有单个原子状态文件切换 active/backup；失败安装不会切换当前版本。`rollback` 只切换到上一版，不删除已保存包。无需访问数据库，也不会接触凭据、Inventory、SQL 或报告。

仅当从便携包运行时，`DBA_TOOLKIT_HOME` 指向解压目录并启用驱动管理写入。普通源码/虚拟环境运行不会默认启用本地 driver store；显式设置该环境变量才启用。

## SQL Server 原生 ODBC

`pyodbc` Python 模块随 Toolkit driver bundle 提供。它不包含 Microsoft SQL Server ODBC Driver。DBA/系统管理员需通过组织批准渠道单独安装 Microsoft ODBC Driver 18（首选）或 17（兼容回退），然后运行：

```powershell
.\dba.bat driver check --database sqlserver
.\dba.bat driver-info
.\dba.bat doctor --database sqlserver
```

Toolkit 不下载、运行 MSI、不修改 PATH/注册表、不安装/删除 ODBC Driver。Driver 17 与 18 的 TLS 默认及兼容行为不同；连接仍由实例设置显式控制 `Encrypt` 与 `TrustServerCertificate`。便携包不含 Microsoft ODBC Driver 安装器。

官方资料：[Microsoft ODBC Driver for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)。

## Oracle Thin / Thick

便携包默认使用 python-oracledb Thin Mode，不要求 Oracle Instant Client，也不会自动加载 Thick Mode。python-oracledb 当前文档将 Thin Mode 数据库最低版本限定在 Oracle Database 12.1；Oracle 11g 连接需要另行安装兼容的 Oracle Client 并明确启用 Thick Mode，该路径不属于此便携 MVP，也没有在本便携包中验证。不能因驱动导入成功就推导 Oracle 版本的 Toolkit Profile 或 E2E 已验证。

官方资料：[python-oracledb 安装与兼容性](https://python-oracledb.readthedocs.io/en/latest/user_guide/installation.html)。

## 更新与回退

首次安装使用 Full ZIP。正式版本 0.10.0 起提供 Full 和独立、完整的 Update ZIP。将唯一的最新 Update ZIP 放入安装目录 `updates/`，关闭 Toolkit 后双击 `升级.bat`。它使用 Windows PowerShell 外部更新器，不运行旧 Python 来覆盖自身 Runtime。详细步骤、版本兼容、首个 0.9.0 基线迁移和回滚命令见 [便携版升级手册](PORTABLE_UPDATES.md)。

升级器验证清单、版本范围、完整文件列表与 SHA256、磁盘空间及新 Runtime/SQL Registry；在同卷 staging 中准备后再切换。升级失败会尝试恢复旧程序并保留备份。只在活动驱动确认为旧 Toolkit 默认包时更新默认驱动；用户选择的自定义驱动、活动驱动目录、`driver-packages`、Inventory、Credential Store、报告和日志均保留。不要将 ZIP 内容直接覆盖到已有便携目录。

运行时源码与包内文件受用户目录 ACL 控制。这不是抵御同一 Windows 用户恶意篡改的安全边界；同一账户下可写包目录的进程也可以改动 Toolkit 或驱动。不要把便携目录放到多人可写共享盘。

## 构建和离线验证

Windows 手工构建入口为 `deployment/build_full.ps1 -Version <version>`、`deployment/build_update.ps1 -Version <version>` 和 `deployment/verify_release.ps1 -Version <version>`。版本必须匹配 `pyproject.toml` 与 `dba.__version__`。构建宿主使用 Windows x64 CPython，目标使用项目固定的 CPython 3.13.17 Embeddable Runtime 与哈希锁定 wheelhouse。构建只从本地 wheelhouse 使用 `pip --no-index`；目标端不需要 Python、pip 或联网。

```powershell
.\deployment\build_full.ps1 -Version 0.9.0
.\deployment\build_update.ps1 -Version 0.9.0
.\deployment\verify_release.ps1 -Version 0.9.0
```

在当前 0.9.0 基线上，Full 是首次安装基线；同版本 Update 只作为不同 build ID 的开发候选。0.10.0 及以后正式版本才发布 Update。GitHub Actions 的 main 构建上传候选 Full/Update artifacts；匹配项目版本的正式 `vX.Y.Z` tag 才触发 Release 创建。工作流不创建 tag。完整升级说明见 [便携版升级手册](PORTABLE_UPDATES.md)。

CPython Embeddable Runtime 使用 `._pth` 控制导入路径；它与完整 Python 安装和 pip 部署的运行语义不同。本项目只在便携验收范围内配置这些路径，不把该压缩包当作通用 Python 安装包。

官方资料：[Python on Windows — Embeddable package](https://docs.python.org/3/using/windows.html)。

## 本轮未承诺的边界

- Linux、Windows ARM64、网络共享文件系统未包含在便携包范围。
- SQL Server ODBC 原生安装、Oracle 11g Thick Mode 不自动提供。
- driver.json 没有独立代码签名；必须通过可信渠道分发。
- 进程退出恢复测试不等于物理断电/存储设备缓存验收。
- 用户有写权限时仍可能替换应用/驱动文件；便携布局不防同用户恶意篡改。
- 便携包验证不代替四库真实数据库 E2E 或现有按版本/场景 Acceptance 证据。
