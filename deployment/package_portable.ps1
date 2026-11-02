[CmdletBinding()]
param(
    [string]$PackageRoot = 'dist\DBAToolkit-0.9.0-Windows-x64-Portable',
    [string]$OutputDirectory = 'dist'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$packageCandidate = if ([IO.Path]::IsPathRooted($PackageRoot)) { $PackageRoot } else { Join-Path $projectRoot $PackageRoot }
$packagePath = (Resolve-Path -LiteralPath $packageCandidate).Path
$outputPath = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
$expectedParent = [IO.Path]::GetFullPath($outputPath)
if (-not $packagePath.StartsWith($expectedParent + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) { throw 'Package root must be inside the selected output directory.' }
if (-not (Test-Path -LiteralPath (Join-Path $packagePath 'runtime\python.exe')) -or
    -not (Test-Path -LiteralPath (Join-Path $packagePath '启动.bat'))) { throw 'Portable package tree is incomplete.' }
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$zipPath = Join-Path $outputPath 'DBAToolkit-0.9.0-Windows-x64-Portable.zip'
$hashPath = "$zipPath.sha256"
if ((Test-Path -LiteralPath $zipPath) -or (Test-Path -LiteralPath $hashPath)) {
    throw 'Output ZIP or SHA256 already exists; choose another output directory.'
}
Compress-Archive -Path (Join-Path $packagePath '*') -DestinationPath $zipPath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $hashPath -Value "$hash  $(Split-Path -Leaf $zipPath)" -Encoding ascii
Write-Host "Created $zipPath"
Write-Host "SHA256 $hash"
