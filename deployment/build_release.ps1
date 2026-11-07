[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$PythonEmbedZip = 'dist\portable-build-inputs\python-3.13.17-embed-amd64.zip',
    [string]$Wheelhouse = 'dist\portable-build-inputs\wheelhouse',
    [string]$BuildPython = '',
    [string]$BuildId = '',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $BuildPython) { $BuildPython = (Get-Command python -ErrorAction Stop).Source }
if (-not $OutputDirectory) { $OutputDirectory = "dist\release-$Version" }
if (-not $BuildId -and $env:GITHUB_SHA) { $BuildId = $env:GITHUB_SHA.Substring(0, [Math]::Min(40, $env:GITHUB_SHA.Length)) }
$outputPath = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $projectRoot $OutputDirectory }
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$fullScript = Join-Path $PSScriptRoot 'build_full.ps1'
$updateScript = Join-Path $PSScriptRoot 'build_update.ps1'
& $fullScript -Version $Version -PythonEmbedZip $PythonEmbedZip -Wheelhouse $Wheelhouse `
    -BuildPython $BuildPython -BuildId $BuildId -OutputDirectory $outputPath
if ($LASTEXITCODE -ne 0) { throw 'Full release package build failed.' }
$fullRoot = Join-Path $outputPath ("DBAToolkit-$Version-Windows-x64-Full")
& $updateScript -Version $Version -FullPackageRoot $fullRoot -BuildPython $BuildPython `
    -OutputDirectory $outputPath
if ($LASTEXITCODE -ne 0) { throw 'Update release package build failed.' }

$fullZip = Join-Path $outputPath ("DBAToolkit-$Version-Windows-x64-Full.zip")
$updateZip = Join-Path $outputPath ("DBAToolkit-$Version-Windows-x64-Update.zip")
$manualName = "DBAToolkit-$Version-User-Guide.md"
$manual = Join-Path $outputPath $manualName
$manualSource = Join-Path $projectRoot '使用说明.md'
if (-not (Test-Path -LiteralPath $manualSource -PathType Leaf)) { throw 'Chinese quick-start manual is missing.' }
Copy-Item -LiteralPath $manualSource -Destination $manual
$sumPath = Join-Path $outputPath ("DBAToolkit-$Version-SHA256SUMS.txt")
$lines = foreach ($path in @($fullZip, $updateZip, $manual)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Release ZIP was not built: $path" }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $(Split-Path -Leaf $path)"
}
[IO.File]::WriteAllLines($sumPath, [string[]]$lines, [Text.UTF8Encoding]::new($false))
$verify = Join-Path $PSScriptRoot 'verify_release.ps1'
& $verify -Version $Version -ArtifactDirectory $outputPath -BuildPython $BuildPython
if ($LASTEXITCODE -ne 0) { throw 'Release artifact verification failed.' }
Write-Host "Full, Update and SHA256SUMS artifacts verified in $outputPath"
