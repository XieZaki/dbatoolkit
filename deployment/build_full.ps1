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
if (-not $OutputDirectory) { $OutputDirectory = "dist\release-$Version" }
if (-not $BuildPython) { $BuildPython = (Get-Command python -ErrorAction Stop).Source }
foreach ($inputPath in @($PythonEmbedZip, $Wheelhouse, $BuildPython)) {
    $candidate = if ([IO.Path]::IsPathRooted($inputPath)) { $inputPath } else { Join-Path $projectRoot $inputPath }
    if (-not (Test-Path -LiteralPath $candidate)) { throw "A required local build input is unavailable: $inputPath" }
}
$outputPath = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $projectRoot $OutputDirectory }
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$rootPrefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $outputPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output directory must remain inside the project workspace.'
}
$relativeOutput = $outputPath.Substring($rootPrefix.Length)
$buildScript = Join-Path $PSScriptRoot 'build_portable.ps1'
$packageScript = Join-Path $PSScriptRoot 'package_portable.ps1'
& $buildScript -PythonEmbedZip $PythonEmbedZip -Wheelhouse $Wheelhouse -BuildPython $BuildPython `
    -Version $Version -BuildId $BuildId -OutputDirectory $relativeOutput
if ($LASTEXITCODE -ne 0) { throw 'Full package tree build failed.' }
$packageRoot = Join-Path $relativeOutput ("DBAToolkit-$Version-Windows-x64-Full")
& $packageScript -Version $Version -PackageRoot $packageRoot -OutputDirectory $relativeOutput
if ($LASTEXITCODE -ne 0) { throw 'Full package ZIP creation failed.' }
Write-Host "Full package ready in $outputPath"
