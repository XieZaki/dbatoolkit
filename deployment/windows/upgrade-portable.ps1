[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageZip,
    [Parameter(Mandatory = $true)][string]$InstallDirectory
)

$ErrorActionPreference = 'Stop'

function Get-Sha256Hex([string]$Path) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
        } finally {
            $sha256.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

$zip = (Resolve-Path -LiteralPath $PackageZip).Path
$install = (Resolve-Path -LiteralPath $InstallDirectory).Path
$hashPath = "$zip.sha256"
if (-not (Test-Path -LiteralPath $hashPath -PathType Leaf)) {
    throw 'Package SHA256 sidecar is missing.'
}
$hashLine = (Get-Content -LiteralPath $hashPath -Raw).Trim()
$hashMatch = [regex]::Match($hashLine, '^([A-Fa-f0-9]{64})\s+\*?(.+)$')
if (-not $hashMatch.Success -or $hashMatch.Groups[2].Value -ne [IO.Path]::GetFileName($zip)) {
    throw 'Package SHA256 sidecar is invalid.'
}
$sidecarPath = (Resolve-Path -LiteralPath $hashPath).Path
if ([IO.Path]::GetFullPath($sidecarPath) -eq [IO.Path]::GetFullPath($zip)) {
    throw 'Package and SHA256 sidecar paths are invalid.'
}
$expectedHash = $hashMatch.Groups[1].Value.ToLowerInvariant()
$actualHash = Get-Sha256Hex $zip
if ($actualHash -ne $expectedHash) { throw 'Package SHA256 verification failed.' }
foreach ($required in @('runtime\python.exe', 'app\portable_entry.py', '启动.bat')) {
    if (-not (Test-Path -LiteralPath (Join-Path $install $required))) {
        throw 'InstallDirectory is not an existing DBA Toolkit portable installation.'
    }
}
$parent = [IO.Directory]::GetParent($install).FullName
$leaf = [IO.Path]::GetFileName($install.TrimEnd([IO.Path]::DirectorySeparatorChar))
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$token = [guid]::NewGuid().ToString('N')
$stage = Join-Path $parent ("." + $leaf + "-upgrade-$stamp-$token")
$backup = Join-Path $parent ($leaf + "-program-backup-$stamp-$token")
if ((Test-Path -LiteralPath $stage) -or (Test-Path -LiteralPath $backup)) { throw 'Unique stage or backup path already exists.' }
New-Item -ItemType Directory -Path $stage | Out-Null
New-Item -ItemType Directory -Path $backup | Out-Null

try {
    Expand-Archive -LiteralPath $zip -DestinationPath $stage
    foreach ($required in @('runtime\python.exe', 'app\portable_entry.py', '启动.bat',
                            'dba.bat', 'deployment\windows\startup_check.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $stage $required))) {
            throw "New portable package is incomplete: $required"
        }
    }
    $verifyScript = Join-Path $PSScriptRoot '..\..\deployment\verify_portable.ps1'
    & $verifyScript -PackageRoot $stage
    if ($LASTEXITCODE -ne 0) { throw 'Staged portable smoke validation failed.' }

    $items = @(
        'runtime', 'app', 'docs',
        '启动.bat', '升级.bat', 'upgrade.bat', 'dba.bat', 'README.md', '使用说明.md', 'portable-manifest.json',
        'deployment\windows\startup_check.py', 'deployment\windows\update-bootstrap.ps1',
        'deployment\windows\upgrade-portable.ps1', 'deployment\windows\rollback-portable.ps1'
    )
    try {
        foreach ($relative in $items) {
            $destination = Join-Path $install $relative
            $source = Join-Path $stage $relative
            $saved = Join-Path $backup $relative
            $destinationParent = Split-Path -Parent $destination
            New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
            New-Item -ItemType Directory -Path (Split-Path -Parent $saved) -Force | Out-Null
            if (Test-Path -LiteralPath $destination) { Move-Item -LiteralPath $destination -Destination $saved }
            Move-Item -LiteralPath $source -Destination $destination
        }
    } catch {
        $rollbackItems = @($items)
        [Array]::Reverse($rollbackItems)
        foreach ($relative in $rollbackItems) {
            $destination = Join-Path $install $relative
            $saved = Join-Path $backup $relative
            $source = Join-Path $stage $relative
            if ((Test-Path -LiteralPath $destination) -and
                ((Test-Path -LiteralPath $saved) -or -not (Test-Path -LiteralPath $source))) {
                $failed = Join-Path $stage ("failed-" + ($relative -replace '[\\/:]', '_'))
                Move-Item -LiteralPath $destination -Destination $failed
            }
            if (Test-Path -LiteralPath $saved) { Move-Item -LiteralPath $saved -Destination $destination }
        }
        throw "Portable upgrade rolled back. Program backup retained at $backup."
    }
    Write-Host "Portable program upgraded. Previous runtime/app/launchers retained at $backup"
    Write-Host 'Existing drivers, driver ZIPs, Inventory, user data, reports and logs were not replaced.'
    Write-Host "Staging retained at $stage for review."
} catch {
    Write-Error $_
    Write-Host "No user data directories were modified. Stage and any backup are retained: $stage ; $backup"
    throw
}
