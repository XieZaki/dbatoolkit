[CmdletBinding()]
param(
    [ValidateSet('all-db','sqlserver','postgresql','mysql','oracle','mongodb','redis')]
    [string]$Database = 'all-db',
    [string]$Python = 'python',
    [string]$VenvPath = '.venv'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$bootstrap = Join-Path $projectRoot 'deployment\windows\bootstrap.ps1'

Write-Host 'Upgrade uses the current local checkout and its local wheelhouse.'
Write-Host 'Review the release notes and preserve local Inventory and Credential Manager entries.'
& $bootstrap -Offline -Database $Database -Python $Python -VenvPath $VenvPath
if ($LASTEXITCODE -ne 0) { throw 'Runtime bootstrap failed; upgrade validation stopped.' }

$venvPython = Join-Path (Join-Path $projectRoot $VenvPath) 'Scripts\python.exe'
& $venvPython -m dba doctor
if ($LASTEXITCODE -ne 0) { throw 'dba doctor failed.' }
& $venvPython -m dba validate
if ($LASTEXITCODE -ne 0) { throw 'dba validate failed.' }
Write-Host 'Upgrade checks passed. Local Inventory and OS Credential Store were not modified.'
