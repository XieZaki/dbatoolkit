[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonEmbedZip,
    [Parameter(Mandatory = $true)][string]$Wheelhouse,
    [Parameter(Mandatory = $true)][string]$BuildPython,
    [string]$Version = '0.9.1',
    [string]$BuildId = '',
    [string]$MinimumSupportedVersion = '0.9.0',
    [string]$OutputDirectory = 'dist'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$embedZip = (Resolve-Path -LiteralPath $PythonEmbedZip).Path
$wheelRoot = (Resolve-Path -LiteralPath $Wheelhouse).Path
$buildPythonPath = (Resolve-Path -LiteralPath $BuildPython).Path
$outputRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
$packageName = "DBAToolkit-$Version-Windows-x64-Full"
$packageRoot = Join-Path $outputRoot $packageName
$expectedEmbedSha256 = '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'
$actualEmbedSha256 = (Get-FileHash -LiteralPath $embedZip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualEmbedSha256 -ne $expectedEmbedSha256) {
    throw 'CPython 3.13.17 embeddable ZIP failed the official SHA256 check.'
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Portable builds require Windows x64.' }
if (Test-Path -LiteralPath $packageRoot) { throw "Output already exists; choose a new output directory: $packageRoot" }
if ($Version -notmatch '^\d+\.\d+\.\d+$' -or $MinimumSupportedVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw 'Release versions must use numeric major.minor.patch form.'
}
$projectVersion = [regex]::Match((Get-Content -LiteralPath (Join-Path $projectRoot 'pyproject.toml') -Raw),
    '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"').Groups[1].Value
$sourceVersion = [regex]::Match((Get-Content -LiteralPath (Join-Path $projectRoot 'dba\__init__.py') -Raw),
    '(?m)^__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"').Groups[1].Value
if (-not $projectVersion -or $projectVersion -ne $sourceVersion -or $Version -ne $projectVersion) {
    throw 'Requested package version must match both pyproject.toml and dba.__version__.'
}
if (-not $BuildId) { $BuildId = "local-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))" }
if ($BuildId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') { throw 'Build ID contains unsupported characters.' }
if (@(Get-ChildItem -LiteralPath $wheelRoot -Filter '*.whl' -File).Count -eq 0) {
    throw 'The local wheelhouse is empty; prepare matching Windows x64 CPython wheels first.'
}
$hostRuntimeJson = & $buildPythonPath -c 'import json,platform,struct,sys; print(json.dumps({"implementation": platform.python_implementation(), "major": sys.version_info.major, "minor": sys.version_info.minor, "bits": struct.calcsize("P") * 8, "machine": platform.machine().casefold()}))'
if ($LASTEXITCODE -ne 0) { throw 'Build Python runtime inspection failed.' }
$hostRuntime = $hostRuntimeJson | ConvertFrom-Json
if ($hostRuntime.implementation -ne 'CPython' -or $hostRuntime.major -ne 3 -or
    $hostRuntime.minor -notin @(11, 12, 13) -or $hostRuntime.bits -ne 64 -or
    $hostRuntime.machine -notin @('amd64', 'x86_64', 'x64')) {
    throw 'Build Python must be supported CPython x64.'
}
$runtimeVersion = '3.13'

New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'runtime') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'app') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'app\vendor') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'driver-packages') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'app\default-driver-packages') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'docs') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'app\deployment') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'deployment\windows') -Force | Out-Null

Expand-Archive -LiteralPath $embedZip -DestinationPath (Join-Path $packageRoot 'runtime')
$runtimeDir = Join-Path $packageRoot 'runtime'
$embeddedPython = Join-Path $runtimeDir 'python.exe'
if (-not (Test-Path -LiteralPath $embeddedPython)) { throw 'Embeddable archive does not contain python.exe.' }
$embeddedRuntimeJson = & $embeddedPython -c 'import json,platform,struct,sys; print(json.dumps({"implementation": platform.python_implementation(), "major": sys.version_info.major, "minor": sys.version_info.minor, "bits": struct.calcsize("P") * 8, "machine": platform.machine().casefold()}))'
if ($LASTEXITCODE -ne 0) { throw 'Embeddable runtime inspection failed.' }
$embeddedRuntime = $embeddedRuntimeJson | ConvertFrom-Json
if ($embeddedRuntime.implementation -ne 'CPython' -or $embeddedRuntime.bits -ne 64 -or
    $embeddedRuntime.machine -notin @('amd64', 'x86_64', 'x64') -or
    "$($embeddedRuntime.major).$($embeddedRuntime.minor)" -ne $runtimeVersion) {
    throw 'Embeddable runtime must be matching CPython x64.'
}
$pythonMinor = $runtimeVersion
$pth = Get-ChildItem -LiteralPath $runtimeDir -Filter 'python*._pth' -File | Select-Object -First 1
if (-not $pth) { throw 'Embeddable runtime ._pth file is missing.' }
$pythonZip = Get-ChildItem -LiteralPath $runtimeDir -Filter 'python*.zip' -File | Select-Object -First 1
if (-not $pythonZip) { throw 'Embeddable runtime standard library archive is missing.' }
@($pythonZip.Name, '.', '..\app', '..\app\vendor') | Set-Content -LiteralPath $pth.FullName -Encoding ascii

$appRoot = Join-Path $packageRoot 'app'
Copy-Item -LiteralPath (Join-Path $projectRoot 'dba') -Destination $appRoot -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'sql') -Destination (Join-Path $appRoot 'sql') -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\runtime-manifest.yaml') -Destination (Join-Path $appRoot 'deployment\runtime-manifest.yaml') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'app\portable_entry.py') -Destination $appRoot -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\windows\PORTABLE_README.md') -Destination (Join-Path $packageRoot 'README.md') -Force
foreach ($document in @('DEPLOYMENT.md', 'CREDENTIAL_SECURITY.md', 'DATABASE_PERMISSIONS.md',
                        'DRIVER_SETUP.md', 'USAGE.md', 'VERSION_PROFILES.md',
                        'REPLICATION_DIAGNOSIS.md', 'PORTABLE_UPDATES.md',
                        '用户操作手册.md', 'UPDATE_GUIDE.md', '故障排查.md')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ("docs\" + $document)) `
        -Destination (Join-Path $packageRoot 'docs') -Force
}
Copy-Item -LiteralPath (Join-Path $projectRoot '使用说明.md') -Destination (Join-Path $packageRoot '使用说明.md') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\windows\startup_check.py') -Destination (Join-Path $packageRoot 'deployment\windows') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\verify_portable.ps1') `
    -Destination (Join-Path $packageRoot 'deployment') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\windows\upgrade-portable.ps1') `
    -Destination (Join-Path $packageRoot 'deployment\windows') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\windows\update-bootstrap.ps1') `
    -Destination (Join-Path $packageRoot 'deployment\windows\update-bootstrap.ps1') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\windows\rollback-portable.ps1') `
    -Destination (Join-Path $packageRoot 'deployment\windows\rollback-portable.ps1') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\windows\upgrade-launch.bat') `
    -Destination (Join-Path $packageRoot '升级.bat') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\windows\upgrade-launch.bat') `
    -Destination (Join-Path $packageRoot 'upgrade.bat') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\windows\portable-launch.bat') -Destination (Join-Path $packageRoot '启动.bat') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'deployment\windows\portable-cli.bat') -Destination (Join-Path $packageRoot 'dba.bat') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'examples') -Destination $packageRoot -Recurse
@('config', 'data', 'reports', 'logs') | ForEach-Object {
    New-Item -ItemType Directory -Path (Join-Path $packageRoot $_) -Force | Out-Null
}
[IO.File]::WriteAllText((Join-Path $packageRoot 'config\inventory.yaml'), 'databases: []', [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $packageRoot 'config\runtime.yaml.example'),
    '# Optional site-specific runtime settings. Do not put passwords here.', [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $packageRoot 'data\README.txt'),
    'Local user data and the explicitly selected encrypted Credential Store belong here.',
    [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $packageRoot 'reports\README.txt'),
    'Generated diagnostic reports belong here. Application upgrades preserve this directory.',
    [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $packageRoot 'logs\README.txt'),
    'Local runtime logs belong here. Application upgrades preserve this directory.',
    [Text.UTF8Encoding]::new($false))

$baseLock = Join-Path $projectRoot 'deployment\locks\windows-cp313-x64\base.lock'
& $buildPythonPath -m pip install --no-index --find-links $wheelRoot --only-binary=:all: `
    --no-deps --require-hashes --platform win_amd64 --python-version 3.13 `
    --implementation cp --abi cp313 --target (Join-Path $appRoot 'vendor') --requirement $baseLock
if ($LASTEXITCODE -ne 0) { throw 'Core runtime package resolution from the local wheelhouse failed.' }

$drivers = @(
    @{ Database='mysql'; Lock='mysql.lock' },
    @{ Database='postgresql'; Lock='postgresql.lock' },
    @{ Database='oracle'; Lock='oracle.lock' },
    @{ Database='sqlserver'; Lock='sqlserver.lock' }
)
foreach ($item in $drivers) {
    $stage = Join-Path $packageRoot (".driver-stage\" + $item.Database)
    $sitePackages = Join-Path $stage 'site-packages'
    New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null
    $driverLock = Join-Path $projectRoot ("deployment\locks\windows-cp313-x64\" + $item.Lock)
    & $buildPythonPath -m pip install --no-index --find-links $wheelRoot --only-binary=:all: `
        --no-deps --require-hashes --platform win_amd64 --python-version 3.13 `
        --implementation cp --abi cp313 --target $sitePackages --requirement $driverLock
    if ($LASTEXITCODE -ne 0) { throw "Driver wheel resolution failed for $($item.Database)." }
    $bundle = Join-Path $packageRoot ("driver-packages\" + $item.Database + '.zip')
    & $embeddedPython -I (Join-Path $projectRoot 'deployment\windows\write_driver_bundle.py') `
        --site-packages $sitePackages --database $item.Database --output $bundle `
        --minimum-toolkit-version '0.9.0' --project-root $projectRoot
    if ($LASTEXITCODE -ne 0) { throw "Driver bundle creation failed for $($item.Database)." }
    & $embeddedPython -I (Join-Path $appRoot 'portable_entry.py') driver install --file $bundle --json
    if ($LASTEXITCODE -ne 0) { throw "Portable driver activation failed for $($item.Database)." }
    Copy-Item -LiteralPath $bundle -Destination (Join-Path $packageRoot ("app\default-driver-packages\" + $item.Database + '.zip')) -Force
}
& $embeddedPython -I (Join-Path $packageRoot 'deployment\windows\startup_check.py')
if ($LASTEXITCODE -ne 0) { throw 'Portable startup verification failed.' }
& $embeddedPython -I (Join-Path $appRoot 'portable_entry.py') validate --json
if ($LASTEXITCODE -ne 0) { throw 'Portable SQL Registry validation failed.' }
# Build-time driver and validation commands write a local runtime log. It is
# generated build data, not part of the portable application payload.
$buildLog = Join-Path $packageRoot 'logs\dba.jsonl'
if (Test-Path -LiteralPath $buildLog -PathType Leaf) { Remove-Item -LiteralPath $buildLog -Force }
if (@(Get-ChildItem -LiteralPath (Join-Path $packageRoot 'logs') -Force -File |
        Where-Object { $_.Name -ne 'README.txt' }).Count -gt 0) {
    throw 'Portable package staging contains unexpected log output.'
}
$bytecodeDirectories = @(Get-ChildItem -LiteralPath $packageRoot -Directory -Filter '__pycache__' -Recurse -Force)
foreach ($cacheDirectory in $bytecodeDirectories) {
    Remove-Item -LiteralPath $cacheDirectory.FullName -Recurse -Force
}
Get-ChildItem -LiteralPath $packageRoot -File -Recurse -Force |
    Where-Object { $_.Extension -in @('.pyc', '.pyo') } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

$manifest = [ordered]@{
    package_type = 'Full'
    application_version = $Version
    product_version = $Version
    minimum_supported_version = $MinimumSupportedVersion
    maximum_supported_version = $null
    runtime_version = $embeddedRuntime.major.ToString() + '.' + $embeddedRuntime.minor.ToString()
    python_runtime = $pythonMinor.Split(':')[0]
    architecture = 'x64'
    data_schema_version = 1
    driver_manifest_version = 1
    build_id = $BuildId
    package_mode = 'CPython Embeddable Runtime + external versioned driver packages'
    driver_packages = @('mysql', 'postgresql', 'oracle', 'sqlserver')
    native_external = @('Microsoft ODBC Driver 18 or 17 for SQL Server')
    network_required_at_runtime = $false
}
[IO.File]::WriteAllText((Join-Path $packageRoot 'portable-manifest.json'),
    ($manifest | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
Remove-Item -LiteralPath (Join-Path $packageRoot '.driver-stage') -Recurse -Force
Write-Host "Portable package tree created: $packageRoot"
Write-Host 'Run deployment\package_portable.ps1 to create the distributable ZIP and SHA256 file.'
