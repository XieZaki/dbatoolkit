[CmdletBinding()]
param([string]$PackageRoot = 'dist\DBAToolkit-0.9.0-Windows-x64-Portable')

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$packageCandidate = if ([IO.Path]::IsPathRooted($PackageRoot)) { $PackageRoot } else { Join-Path $projectRoot $PackageRoot }
$root = (Resolve-Path -LiteralPath $packageCandidate).Path
$python = Join-Path $root 'runtime\python.exe'
$entry = Join-Path $root 'app\portable_entry.py'
foreach ($path in @($python, $entry, (Join-Path $root '启动.bat'),
                    (Join-Path $root 'drivers\mysql\state.json'),
                    (Join-Path $root 'drivers\postgresql\state.json'),
                    (Join-Path $root 'drivers\oracle\state.json'),
                    (Join-Path $root 'drivers\sqlserver\state.json'))) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Portable package component missing: $path" }
}
$oldHome = $env:DBA_TOOLKIT_HOME
$oldPath = $env:PATH
try {
    $env:DBA_TOOLKIT_HOME = $root
    $env:PATH = Join-Path $env:SystemRoot 'System32'
    & $python -I $entry --version
    if ($LASTEXITCODE -ne 0) { throw 'Bundled runtime version command failed.' }
    & $python -I $entry validate --json
    if ($LASTEXITCODE -ne 0) { throw 'Bundled runtime SQL Registry validation failed.' }
    $driverOutput = & $python -I $entry driver check --json
    if ($LASTEXITCODE -ne 0) { throw 'Bundled runtime driver checks failed.' }
    $check = $driverOutput | ConvertFrom-Json
    foreach ($item in $check.checks) {
        if ($item.database -eq 'sqlserver') {
            if (-not $item.managed -or -not $item.driver_version) {
                throw 'Bundled pyodbc package did not load from its managed package.'
            }
            if (-not $item.selected_native_driver) {
                Write-Warning 'No Microsoft ODBC Driver is installed; this package still runs and other database drivers remain usable.'
            }
        } elseif ($item.status -ne 'READY') {
            throw "Managed driver check failed for $($item.database): $($item.status)"
        }
    }
    Write-Host 'Portable smoke checks passed using only runtime\python.exe; no database or network was used.'
} finally {
    $env:DBA_TOOLKIT_HOME = $oldHome
    $env:PATH = $oldPath
}
