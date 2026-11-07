# Windows Full 与 Update 便携包

Full 与 Update 包均附带中文操作文档。请先阅读根目录的 `使用说明.md`；完整手册、驱动指南、升级指南和故障排查指南位于 `docs/`。GitHub Release 还会单独附上内容为中文的 `DBAToolkit-<版本>-User-Guide.md` 快速入门，并将其 SHA256 列入校验清单。

当前源码及软件包版本为 `0.9.1`。`0.9.0` Full ZIP 是安装基线。`0.9.1` Release 提供 Full ZIP、自包含 Update ZIP、SHA256SUMS 校验清单和独立中文快速入门。Update ZIP 包含完整目标程序、Runtime、SQL、模板和默认驱动载荷，不依赖中间 Update。更新器支持从清单指定的最低版本 `0.9.0` 直接升级。

## 安装与升级

1. 首次安装或更换电脑时下载 Full ZIP。使用 Release 中的 `SHA256SUMS.txt` 核对哈希，解压到较短的本地路径，然后双击 `启动.bat`。
2. 升级现有安装时先关闭所有 Toolkit 窗口。下载最新 Update ZIP 并按 `SHA256SUMS.txt` 核对，将唯一一个 `DBAToolkit-*-Windows-x64-Update.zip` 放入安装目录的 `updates/`。
3. 双击 `升级.bat`。PowerShell 更新器从 `deployment/windows` 运行，该目录位于将被替换的 `runtime/` 和 `app/` 之外；替换期间不会启动旧 Python Runtime。如果无法确认 Toolkit 进程已停止，更新器会拒绝继续。

更新器会在 `updates/` 下创建独立暂存目录，校验清单、完整文件列表、文件大小与 SHA256、源版本和目标版本、架构、Runtime、数据结构版本及可用磁盘空间。切换程序路径前会检查暂存的 Runtime 和 SQL Registry。文件在同一卷内移动，并保留 `backup/program-backup-*` 目录。如果更新后的启动检查失败，更新器会恢复旧程序及本次更新修改的默认驱动选择。暂存目录和备份会保留供检查，更新器不会删除用户文件。

程序替换范围明确限定为 Runtime、应用、文档、启动器、便携包清单和启动/旧版恢复脚本。`config/`、`data/`、`reports/`、`logs/`、`updates/`、`backup/`、Windows Credential Manager 和活动 `drivers/` 目录均不在替换范围内。根目录的 `driver-packages/` 也会保留。新版默认驱动包位于 `app/default-driver-packages/`；只有确认当前受管驱动与旧版随包默认驱动完全一致时才会替换。用户选择或自定义的活动驱动保持不变。如果它与新 Runtime 不兼容，需要另行人工更新。

升级成功后会保留程序备份。手动回退时先关闭 Toolkit，再使用选定的备份目录运行 `deployment/windows/rollback-portable.ps1`。回退仅恢复记录的程序路径和该次升级保存的默认驱动状态，不会恢复或覆盖 Inventory、Credential Store、报告、日志或其他用户数据。确认升级后的安装可用前请保留备份。

从早期 0.9.0 Portable ZIP 创建的安装目录尚无 `升级.bat`。首次过渡时，将 0.9.1 Full 包解压到另一个较短路径，然后使用该目录中的 `deployment/windows/upgrade-portable.ps1`，同时指定 Full ZIP 和现有安装目录。此兼容入口会保留现有用户目录并添加长期使用的 Update 启动器和引导程序。之后的 Update ZIP 使用上述一键流程。

## 兼容性与数据结构版本

Update 清单包含包类型、应用版本、支持的源版本、Runtime、x64 架构、数据结构版本、驱动清单结构版本、Build ID、完整载荷文件的哈希与大小，以及默认驱动包哈希。目标版本低于已安装版本时会被拒绝。只有 Build ID 不同时才允许同应用版本更新，以支持开发候选包而不把相同构建误认为升级。当前用户数据结构版本为 `1`；本次发布不迁移数据。未来若修改数据结构版本，必须先实现有序、幂等且具备回退路径的显式迁移，否则更新器会拒绝该安装包。

包内清单用于发现文件缺失或损坏，Release 校验清单用于发现 ZIP 意外损坏；这些检查不构成发布者数字签名。仅从经批准的 GitHub Release 渠道下载。工作流不会自动创建 Tag；源码包版本和已推送的 `vX.Y.Z` Tag 必须一致。

## 构建与验证

在经授权的 Windows x64 构建机上准备固定版本 CPython 3.13.17 Embeddable ZIP 和匹配的本地 wheelhouse。目标版本必须同时匹配 `pyproject.toml` 和 `dba.__version__`。

```powershell
.\deployment\build_full.ps1 -Version 0.9.1
.\deployment\build_update.ps1 -Version 0.9.1
.\deployment\verify_release.ps1 -Version 0.9.1
```

要一次性构建两个安装包和校验清单，请使用 `deployment/build_release.ps1`。参数包括 `-PythonEmbedZip`、`-Wheelhouse`、`-BuildPython`、`-BuildId` 和 `-OutputDirectory`；默认输入目录为 `dist/portable-build-inputs/`。只有输入 wheel、Runtime 和源码版本一致时，Release 构建才可复现；每个安装包都会记录 Build ID。

`build_update.ps1` 不会重新标记源码版本：请求的包版本必须与项目内两个版本声明一致。0.9.0 基线仅发布 Full；0.9.1 正式版同时发布 Full 和 Update。

## GitHub Actions

推送到 `main` 时会运行 pytest、Ruff 和 MyPy，构建并验证 Full 与 Update 候选包，再上传短期保留的构建产物。推送 `vX.Y.Z` Tag 时，Tag 必须与项目内两个版本声明一致；工作流完成同一组测试和安装包检查后，先创建草稿 Release，核实四项附件及 SHA256，再将 Release 正式公开。只有发布作业具有 `contents: write` 权限；普通构建作业为只读。手动运行可构建候选包；只有在匹配版本的 Tag 上并启用发布选项时才可发布。工作流不会创建或推送 Tag。

## 适用限制

- `v0.9.1` Release 仅在工作流验证四项发布附件及 SHA256 后公开。
- 更新和回退检查不模拟整机断电或存储硬件故障。
- 同一用户恶意替换文件、替换父目录以及发布凭据泄漏不在此机制的保证范围内。
- Windows 更新器不支持网络共享和非 Windows 平台。
- 安装包构建、更新验证和回退过程都不会连接数据库。
# Windows Full / Update package and rollback
