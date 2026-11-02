[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$BackupDirectory
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$backup = (Resolve-Path -LiteralPath $BackupDirectory).Path.TrimEnd('\')
$backupBase = [IO.Path]::GetFullPath((Join-Path $root 'backup')).TrimEnd('\') + '\'
if (-not $backup.StartsWith($backupBase, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Rollback backup must be inside this installation backup directory.'
}
$manifestPath = Join-Path $backup 'rollback-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Rollback manifest is missing.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
if ($manifest.schema_version -ne 1 -or -not $manifest.program_paths) { throw 'Rollback manifest is unsupported.' }
try { $processes = @(Get-Process -Name 'python' -ErrorAction SilentlyContinue) }
catch { throw 'Could not verify that the portable Toolkit is stopped; close it and retry.' }
foreach ($process in $processes) {
    try { $exe = [string]$process.Path } catch { $exe = '' }
    if ($exe -and $exe.StartsWith((Join-Path $root 'runtime') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'A Toolkit process is still running. Close it before rollback.'
    }
}
$allowed = @('runtime', 'app', 'docs', 'deployment\windows\startup_check.py',
             'deployment\windows\upgrade-portable.ps1', 'deployment\windows\rollback-portable.ps1',
             '升级.bat', 'upgrade.bat', '启动.bat', 'dba.bat', 'README.md', '使用说明.md', 'portable-manifest.json')
$allowedPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($allowedPath in $allowed) { [void]$allowedPaths.Add($allowedPath.Replace('/', '\')) }
$entries = @($manifest.program_paths)
foreach ($entry in $entries) {
    $relativePath = ([string]$entry.path).Replace('/', '\')
    if (-not $allowedPaths.Contains($relativePath)) { throw 'Rollback manifest contains an unsupported program path.' }
    if ($entry.had_original -and -not (Test-Path -LiteralPath (Join-Path $backup ('program\' + $entry.path)))) {
        throw 'Rollback program backup is incomplete; no files were changed.'
    }
}
$quarantine = Join-Path $root ("updates\.rollback-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $quarantine | Out-Null
$movedCurrent = [Collections.Generic.List[string]]::new()
$restoredOld = [Collections.Generic.List[string]]::new()
try {
    foreach ($entry in @($entries)[($entries.Count - 1)..0]) {
        $relative = [string]$entry.path
        $destination = Join-Path $root $relative
        if (Test-Path -LiteralPath $destination) {
            $savedCurrent = Join-Path $quarantine $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $savedCurrent) -Force | Out-Null
            Move-Item -LiteralPath $destination -Destination $savedCurrent
            $movedCurrent.Add($relative)
        }
        if ($entry.had_original) {
            $old = Join-Path $backup ('program\' + $relative)
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Move-Item -LiteralPath $old -Destination $destination
            $restoredOld.Add($relative)
        }
    }
    foreach ($relative in @($manifest.driver_state_paths)) {
        $saved = Join-Path $backup ('driver-state\' + $relative)
        if (-not (Test-Path -LiteralPath $saved)) { throw 'Driver state backup is incomplete.' }
        $destination = Join-Path $root ('drivers\' + $relative)
        $temporary = $destination + '.rollback-' + [guid]::NewGuid().ToString('N')
        Copy-Item -LiteralPath $saved -Destination $temporary
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    }
} catch {
    throw "Rollback stopped. Preserve both backup and current program files; recovery area: $quarantine"
}
Write-Host "Program rollback completed to version $($manifest.installed_version)."
Write-Host "Current program files retained at $quarantine; user data was not modified."
