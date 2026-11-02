[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$ArtifactDirectory = '',
    [string]$BuildPython = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ArtifactDirectory) { $ArtifactDirectory = "dist\release-$Version" }
if (-not $BuildPython) { $BuildPython = (Get-Command python -ErrorAction Stop).Source }
$directory = if ([IO.Path]::IsPathRooted($ArtifactDirectory)) { $ArtifactDirectory } else { Join-Path $projectRoot $ArtifactDirectory }
$pythonPath = if ([IO.Path]::IsPathRooted($BuildPython)) { $BuildPython } else { Join-Path $projectRoot $BuildPython }
$helper = Join-Path $PSScriptRoot 'windows\release_package.py'
& $pythonPath -I $helper verify-release --directory $directory --version $Version
if ($LASTEXITCODE -ne 0) { throw 'Full/Update ZIP release verification failed.' }
