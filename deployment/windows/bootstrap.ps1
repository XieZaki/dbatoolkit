[CmdletBinding()]
param(
    [ValidateSet('all-db','sqlserver','postgresql','mysql','oracle','mongodb','redis')]
    [string]$Database = 'all-db',
    [switch]$Offline,
    [string]$Python = 'python',
    [string]$VenvPath = '.venv'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$wheelhouse = Join-Path $projectRoot 'deployment\wheelhouse'
$venv = [IO.Path]::GetFullPath((Join-Path $projectRoot $VenvPath))
$venvPython = Join-Path $venv 'Scripts\python.exe'

$hostRuntime = & $Python -c "import sys,struct; assert (3,11) <= sys.version_info[:2] < (3,14), 'Python >=3.11,<3.14 required'; print('{}.{}:{}'.format(sys.version_info.major,sys.version_info.minor,struct.calcsize('P')*8))"
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python runtime.' }
Write-Host "Host Python runtime: $hostRuntime"

if (Test-Path $venv) {
    if (-not (Test-Path $venvPython)) {
        throw "Existing $venv is not a compatible Windows venv. Move it aside and rebuild explicitly."
    }
    $venvRuntime = & $venvPython -c "import sys,struct; print('{}.{}:{}'.format(sys.version_info.major,sys.version_info.minor,struct.calcsize('P')*8))"
    if ($venvRuntime -ne $hostRuntime) {
        throw "Existing venv runtime $venvRuntime does not match host $hostRuntime. Move it aside and rebuild explicitly."
    }
    Write-Host "Reusing existing venv: $venv"
} else {
    & $Python -m venv $venv
}

if (-not (Test-Path $wheelhouse)) { throw "Wheelhouse missing: $wheelhouse" }
$wheels = @(Get-ChildItem -LiteralPath $wheelhouse -Filter '*.whl' -File)
if ($wheels.Count -eq 0) {
    throw "Wheelhouse contains no wheels. Prepare packages for this OS, Python ABI and architecture."
}

# Local-only in every mode. -Offline documents/enforces deployment intent; neither mode downloads.
$projectSpec = ('{0}[{1}]' -f $projectRoot, $Database)
& $venvPython -m pip install --no-index --find-links $wheelhouse -e $projectSpec
if ($LASTEXITCODE -ne 0) {
    throw "Offline install failed. Check missing package/version/platform wheels in $wheelhouse."
}

& $venvPython -m dba doctor
Write-Host "Next: $venvPython -m dba validate"
Write-Host 'Native installers are never run automatically. Review deployment\packages if ODBC is missing.'
$packageDir = Join-Path $projectRoot 'deployment\packages'
$approvedInstallers = @(Get-ChildItem -LiteralPath $packageDir -Filter '*.msi' -File -ErrorAction SilentlyContinue)
foreach ($installer in $approvedInstallers) {
    Write-Host "Approved installer staged for administrator review: $($installer.FullName)"
    Write-Host "Manual elevated example: msiexec /i `"$($installer.FullName)`""
}
if ($Offline) { Write-Host 'Offline mode completed without package-index access.' }
