[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$FullPackageRoot = '',
    [string]$BuildPython = '',
    [string]$BuildId = '',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $OutputDirectory) { $OutputDirectory = "dist\release-$Version" }
if (-not $FullPackageRoot) { $FullPackageRoot = Join-Path $OutputDirectory ("DBAToolkit-$Version-Windows-x64-Full") }
if (-not $BuildPython) { $BuildPython = (Get-Command python -ErrorAction Stop).Source }
$fullRoot = if ([IO.Path]::IsPathRooted($FullPackageRoot)) { $FullPackageRoot } else { Join-Path $projectRoot $FullPackageRoot }
$manifestPath = Join-Path $fullRoot 'portable-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Full package tree is missing its portable manifest.' }
$fullManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -ErrorAction Stop
if ($fullManifest.application_version -ne $Version -or $fullManifest.package_type -ne 'Full') {
    throw 'Requested Update version must match the Full package tree.'
}
$pythonPath = if ([IO.Path]::IsPathRooted($BuildPython)) { $BuildPython } else { Join-Path $projectRoot $BuildPython }
$outputPath = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $projectRoot $OutputDirectory }
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$zipName = "DBAToolkit-$Version-Windows-x64-Update.zip"
$zipPath = Join-Path $outputPath $zipName
$helper = Join-Path $PSScriptRoot 'windows\release_package.py'
$arguments = @('-I', $helper, 'create-update', '--full-root', $fullRoot, '--output', $zipPath,
               '--build-id', $(if ($BuildId) { $BuildId } else { [string]$fullManifest.build_id }))
& $pythonPath @arguments
if ($LASTEXITCODE -ne 0) { throw 'Update package creation failed validation.' }
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath ($zipPath + '.sha256') -Value "$hash  $zipName" -Encoding ascii
Write-Host "Update package created: $zipPath"
Write-Host "SHA256 $hash"
