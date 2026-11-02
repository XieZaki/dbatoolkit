# Windows 便携版升级与回退指南

版本策略：`0.9.0` Full 是首次安装基线；正式 `0.10.0` 及后续版本同时提供 Full 和 Update。Update 包是独立、完整的目标程序/runtime/SQL/模板/默认驱动载荷，不是依赖其他补丁的增量包。

## 首次安装、换电脑和日常升级

- 首次安装或换电脑：下载当前 Full ZIP，核对 GitHub Release 的 SHA256，解压后运行 `启动.bat`。
- 日常升级：下载当前 Update ZIP，核对 SHA256，将一个 Update ZIP 放到现有便携目录的 `updates/`，关闭 Toolkit，双击 `升级.bat`。
- 0.9.0 到支持范围内的任意新版本可直接升级；无需逐个安装 0.10、0.11 等中间版本。实际允许范围由 Update manifest 的 `minimum_supported_version` / `maximum_supported_version` 决定。
- Update 不能用于向低版本降级；manifest/schema 不兼容会在程序文件切换前拒绝。
- GitHub Release 同时提供独立的 `使用说明.md` 快速入门文件；Full 和 Update 包内也各自带有最新版中文手册。

可用 PowerShell 校验发布文件（与发布页 SHA256SUMS 中记录逐项比对）：

```powershell
$Full = '.\DBAToolkit-0.10.0-Windows-x64-Full.zip'
$Update = '.\DBAToolkit-0.10.0-Windows-x64-Update.zip'
Get-FileHash -LiteralPath $Full -Algorithm SHA256
Get-FileHash -LiteralPath $Update -Algorithm SHA256
```

## 一键升级流程

1. 关闭 Toolkit CLI/报告相关进程。
2. 将一个匹配 Windows x64 的完整 Update ZIP 放到安装目录 `updates/`；目录中有多个匹配 ZIP 时会拒绝运行。
3. 双击根目录 `升级.bat`。它启动随包的 PowerShell 更新引导器，再运行暂存目录中的 `update-tools/apply-update.ps1`；切换过程中不使用旧 Python Runtime 更新自身。
4. 更新器验证 ZIP 安全路径、清单、SHA256、文件大小、x64/Python runtime、支持版本、数据与驱动 schema 及可用磁盘空间。然后在同一卷暂存完整载荷并先做 Runtime/Toolkit/SQL Registry 检查。
5. 更新器保留 `backup/program-backup-*`，再切换 `runtime/`、`app/`、`docs/`、启动器和清单文件。新版本启动检查失败时尝试恢复旧程序及此次更新的默认驱动状态。

验证失败或安装目录中 Toolkit 仍在运行时，升级停止，不执行数据库命令、不修改 Inventory/Credential Store/报告。更新器不会删除备份或历史报告。

## 被保留的用户数据与驱动

不属于程序替换清单的内容：

- `config/`：Inventory 和站点设置。
- `data/`：用户本地数据，包括用户显式配置的 SQLite Credential Store（具体路径可在 Store 命令中指定）。
- `reports/`、`logs/`：报告历史和日志。
- Windows Credential Manager 条目。
- `drivers/` 当前驱动选择和用户安装版本；`driver-packages/` 本地包。
- `updates/`、`backup/`。

只有更新前活动驱动的身份可以证明与此前随包默认 bundle 完全一致时，才安装更新后的默认 bundle。自定义/无法确认的活动版本不会自动替换。若该驱动不兼容新 Runtime，保留现状并要求 DBA 单独评估并手工更换。

升级程序不修改或迁移真实凭据。不要在 Update ZIP 中放入 Inventory、Credential Store、数据库密码、用户报告或自定义驱动。

## 手动回退

先关闭 Toolkit，再在安装根目录 PowerShell 中执行以下命令。它选择最新 `program-backup-*`；若要选择更早备份，请先将 `$Backup` 指向对应目录：

```powershell
$InstallRoot = (Get-Location).Path
$Backup = Get-ChildItem -LiteralPath (Join-Path $InstallRoot 'backup') -Directory -Filter 'program-backup-*' |
    Sort-Object LastWriteTimeUtc | Select-Object -Last 1
if (-not $Backup) { throw '没有找到程序回退备份。' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $InstallRoot 'deployment\windows\rollback-portable.ps1') `
    -InstallRoot $InstallRoot -BackupDirectory $Backup.FullName
```

回退只恢复该备份记录的程序路径和本次更新保存的默认驱动状态；不还原、删除或覆盖 Inventory、凭据、历史报告、日志及其他用户数据。更新后新建的用户数据仍保留。回退失败时，保留 `backup/` 和 `updates/.rollback-*` 现场，先备份目录并人工排查，不要手动覆盖用户数据。

## 安全和兼容限制

- 外部 SHA256SUMS 与包内 manifest 哈希用于识别意外损坏，不构成发布者数字签名；只从批准渠道取包。
- 需要结束文件占用时会停止并提示，不自动强杀进程。
- 网络共享、非 Windows 系统、同用户恶意篡改和父目录替换不在支持保证内。
- 进程崩溃恢复测试不能代替物理断电或磁盘缓存故障测试。
- 当前数据 schema 为 v1；未来 schema 变化必须先实现并验证明确迁移/回滚路径。

## 构建人员

仅在匹配的 Windows x64 构建机、锁定 Runtime/wheelhouse 均已准备时运行：

```powershell
.\deployment\build_full.ps1 -Version 0.9.0
.\deployment\build_update.ps1 -Version 0.9.0
.\deployment\verify_release.ps1 -Version 0.9.0
```

当前源码版本保持 `0.9.0`；构建脚本拒绝与 `pyproject.toml` 或 `dba.__version__` 不一致的版本。开发 Update 不能伪装为未来正式版本。GitHub Actions 的 Release 需已有匹配版本 tag；工作流不创建 tag。
