# Windows 绿色便携版与数据库驱动

本手册说明 Windows x64 离线便携 ZIP、驱动检查和替换。当前产品版本仍为 `0.9.0`。便携包使用 CPython Embeddable Runtime 和普通可导入的 `dba` 应用目录；四种 Python 数据库驱动位于独立、带版本号的 `drivers/<database>/versions/`，通过本地 `driver.json` ZIP 显式安装。程序本身不下载、不调用 pip，也不自动执行系统安装器。

## 首次使用

1. 从经批准的分发位置下载 `DBAToolkit-0.9.0-Windows-x64-Portable.zip` 和 `.sha256` 文件。
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
.\dba.bat driver install --file D:\Drivers\mysql-driver.zip
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

推荐把新版本 ZIP 解压到另一个目录，完成只读 smoke check 后再切换使用目录；旧目录可保留以便整体回退。需要原目录原地更新时，使用：

```powershell
.\deployment\windows\upgrade-portable.ps1 `
  -PackageZip D:\Releases\DBAToolkit-0.9.0-Windows-x64-Portable.zip `
  -InstallDirectory D:\DBA\DBAToolkit
```

脚本先将新包解压到同卷隔离 staging 并检查运行时、Registry 和驱动，再切换 `runtime`、`app` 与启动器；更新前程序目录保留在带时间戳的备份目录。脚本不会覆盖或删除既有 `drivers`、`driver-packages`、`config`、`data`、`reports`、`logs`，也不读取/修改 Credential Store。切换中途失败会尝试恢复旧程序目录；若恢复失败，旧目录备份仍保留，需 DBA 手工恢复。不要将任意文件夹内容直接覆盖到已有便携目录。

运行时源码与包内文件受用户目录 ACL 控制。这不是抵御同一 Windows 用户恶意篡改的安全边界；同一账户下可写包目录的进程也可以改动 Toolkit 或驱动。不要把便携目录放到多人可写共享盘。

## 构建和离线验证

Windows 手工构建需要 Windows x64 CPython 3.13 Embeddable ZIP，以及为 CPython 3.13 / Windows x64 目标准备、并覆盖核心依赖和四个 driver bundle 的本地 wheelhouse。构建宿主使用受支持的 CPython 3.11–3.13 x64；锁定的 wheel 目标 ABI 必须与嵌入式运行时一致：

```powershell
New-Item -ItemType Directory -Path .\deployment\wheelhouse -Force | Out-Null
foreach ($lock in @('base.lock', 'mysql.lock', 'postgresql.lock', 'oracle.lock', 'sqlserver.lock')) {
    python -m pip download --no-deps --only-binary=:all: --require-hashes `
        --platform win_amd64 --python-version 3.13 --implementation cp --abi cp313 `
        --dest .\deployment\wheelhouse `
        --requirement (Join-Path '.\deployment\locks\windows-cp313-x64' $lock)
}
.\deployment\build_portable.ps1 `
  -PythonEmbedZip D:\BuildInputs\python-3.13.17-embed-amd64.zip `
  -Wheelhouse .\deployment\wheelhouse `
  -BuildPython .\.venv\Scripts\python.exe
.\deployment\verify_portable.ps1
.\deployment\package_portable.ps1
```

构建脚本只从指定 wheelhouse 使用 `pip --no-index`，不会在目标端调用 pip；它显式复制产品源码、SQL、必要文档和禁用示例，不复制本机 `.venv`、Inventory、配置、凭据、诊断报告或 Git 数据。默认产物为 `dist/DBAToolkit-0.9.0-Windows-x64-Portable.zip` 和 SHA256。手动触发的 GitHub Actions workflow 构建 Windows 工件并短期上传，不创建 GitHub Release 或 Tag。

```powershell
Get-FileHash .\dist\DBAToolkit-0.9.0-Windows-x64-Portable.zip -Algorithm SHA256
```

验证脚本直接调用 ZIP 内 `runtime/python.exe`，并把 PATH 限制到 Windows System32；它运行版本命令、`dba validate` 和受管驱动的离线导入检查，不连接数据库、不发起网络请求。未安装 SQL Server ODBC Driver 时会单独警告，不会阻止其他三库验证。

CPython Embeddable Runtime 使用 `._pth` 控制导入路径；它与完整 Python 安装和 pip 部署的运行语义不同。本项目只在便携验收范围内配置这些路径，不把该压缩包当作通用 Python 安装包。

官方资料：[Python on Windows — Embeddable package](https://docs.python.org/3/using/windows.html)。

## 本轮未承诺的边界

- Linux、Windows ARM64、网络共享文件系统未包含在便携包范围。
- SQL Server ODBC 原生安装、Oracle 11g Thick Mode 不自动提供。
- driver.json 没有独立代码签名；必须通过可信渠道分发。
- 进程退出恢复测试不等于物理断电/存储设备缓存验收。
- 用户有写权限时仍可能替换应用/驱动文件；便携布局不防同用户恶意篡改。
- 便携包验证不代替四库真实数据库 E2E 或现有按版本/场景 Acceptance 证据。
