[CmdletBinding()]
param([string]$VenvPath = '.venv')
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path ([IO.Path]::GetFullPath((Join-Path $projectRoot $VenvPath))) 'Scripts\python.exe'
if (-not (Test-Path $python)) { throw "Runtime missing: $python. Run bootstrap.ps1 first." }
& $python -m dba doctor
exit $LASTEXITCODE
