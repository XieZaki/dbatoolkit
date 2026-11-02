[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$StageRoot,
    [string]$BuildTimestamp = ''
)

$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
. (Join-Path $PSScriptRoot 'restore-update.ps1')

$programPaths = @(
    'runtime', 'app', 'docs',
    'deployment\windows\startup_check.py',
    'deployment\windows\upgrade-portable.ps1',
    'deployment\windows\rollback-portable.ps1',
    '升级.bat', 'upgrade.bat', '启动.bat', 'dba.bat', 'README.md', '使用说明.md', 'portable-manifest.json'
)
$movedOld = [Collections.Generic.List[string]]::new()
$movedNew = [Collections.Generic.List[string]]::new()
$driverStatePaths = [Collections.Generic.List[string]]::new()
$backup = ''
$root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$stage = [IO.Path]::GetFullPath($StageRoot).TrimEnd('\')
$payload = Join-Path $stage 'payload'
$updateManifestPath = Join-Path $stage 'update-manifest.json'

function Read-JsonFile([string]$Path, [int]$MaximumBytes = 4MB) {
    $info = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($info.Length -gt $MaximumBytes) { throw 'A required update manifest is too large.' }
    try { return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -ErrorAction Stop) }
    catch { throw 'A required update manifest is invalid.' }
}

function Assert-ManifestFiles($Manifest, [string]$RootPath) {
    $expected = @{}
    foreach ($item in @($Manifest.file_manifest)) {
        $relative = [string]$item.path
        if (-not $relative -or $relative.Contains('\') -or $relative.Contains(':') -or
            $relative.StartsWith('/') -or ($relative.Split('/') | Where-Object { $_ -in @('', '.', '..') })) {
            throw 'Update file manifest contains an unsafe path.'
        }
        $scope = $false
        foreach ($prefix in @('payload/runtime/', 'payload/app/', 'payload/docs/')) {
            if ($relative.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { $scope = $true }
        }
        foreach ($exact in @('payload/deployment/windows/startup_check.py',
                              'payload/deployment/windows/upgrade-portable.ps1',
                              'payload/deployment/windows/rollback-portable.ps1',
                              'payload/启动.bat', 'payload/升级.bat', 'payload/upgrade.bat',
                              'payload/dba.bat', 'payload/README.md', 'payload/使用说明.md', 'payload/portable-manifest.json',
                              'update-tools/apply-update.ps1', 'update-tools/restore-update.ps1')) {
            if ($relative.Equals($exact, [StringComparison]::OrdinalIgnoreCase)) { $scope = $true }
        }
        if (-not $scope -or $expected.ContainsKey($relative.ToLowerInvariant()) -or
            [int64]$item.size -lt 0 -or [string]$item.sha256 -notmatch '^[A-Fa-f0-9]{64}$') {
            throw 'Update file manifest contains an unsupported or duplicate entry.'
        }
        $expected[$relative.ToLowerInvariant()] = $item
        $path = [IO.Path]::GetFullPath((Join-Path $RootPath ($relative -replace '/', [IO.Path]::DirectorySeparatorChar)))
        $rootPrefix = [IO.Path]::GetFullPath($RootPath).TrimEnd('\') + '\'
        if (-not $path.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw 'Update payload is missing a manifest file.'
        }
        $file = Get-Item -LiteralPath $path -Force
        if ($file.Length -ne [int64]$item.size -or
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne ([string]$item.sha256)) {
            throw 'Update payload hash or size verification failed.'
        }
    }
    $actual = @{}
    foreach ($base in @((Join-Path $RootPath 'payload'), (Join-Path $RootPath 'update-tools'))) {
        foreach ($file in @(Get-ChildItem -LiteralPath $base -File -Recurse -Force)) {
            $relative = $file.FullName.Substring($RootPath.TrimEnd('\').Length + 1).Replace('\', '/')
            $actual[$relative.ToLowerInvariant()] = $true
        }
    }
    if ($actual.Count -ne $expected.Count) { throw 'Update staging files do not match the manifest.' }
    foreach ($key in $actual.Keys) {
        if (-not $expected.ContainsKey($key)) { throw 'Update staging files do not match the manifest.' }
    }
}

function Convert-Version([string]$Value) {
    if ($Value -notmatch '^\d+\.\d+\.\d+$') { throw 'A package version is invalid.' }
    return [version]$Value
}

function Invoke-NativeCaptured([string]$Executable, [string[]]$Arguments) {
    $previousPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 may promote native stderr to a terminating
        # ErrorRecord under Stop. Capture it without echoing package output.
        $ErrorActionPreference = 'Continue'
        $output = @(& $Executable @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return [pscustomobject]@{ ExitCode = [int]$exitCode; Output = $output }
}

function Get-DriverPackageIdentity([string]$Path, [string]$ExpectedDatabase) {
    try {
        $archive = [IO.Compression.ZipFile]::OpenRead($Path)
        try {
            $entry = $archive.GetEntry('driver.json')
            if (-not $entry -or $entry.Length -gt 64KB) { return $null }
            $reader = [IO.StreamReader]::new($entry.Open(), [Text.Encoding]::UTF8)
            try { $driver = $reader.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop }
            finally { $reader.Dispose() }
        } finally { $archive.Dispose() }
        if ($driver.database -ne $ExpectedDatabase -or
            [string]$driver.driver_version -notmatch '^[A-Za-z0-9.+_-]{1,80}$' -or
            [string]$driver.python_abi -notmatch '^cp\d{2,3}$') { return $null }
        $digest = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        return [ordered]@{
            id = ([string]$driver.driver_version + '-' + [string]$driver.python_abi + '-' + $digest.Substring(0, 16))
            database = $ExpectedDatabase
            driver_version = [string]$driver.driver_version
            python_abi = [string]$driver.python_abi
            sha256 = $digest
        }
    } catch { return $null }
}

function Get-DefaultDriverIds([string]$PortableRoot, [string]$Database) {
    $ids = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $candidates = @(
        (Join-Path $PortableRoot ("app\default-driver-packages\" + $Database + '.zip')),
        (Join-Path $PortableRoot ("driver-packages\" + $Database + '.zip'))
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $identity = Get-DriverPackageIdentity $candidate $Database
            if ($identity) { [void]$ids.Add([string]$identity.id) }
        }
    }
    return ,$ids
}

function Invoke-Portable([string]$Python, [string]$Entry, [string[]]$Arguments, [string]$FailureText) {
    $nativeArguments = @('-I', $Entry) + @($Arguments)
    $result = Invoke-NativeCaptured -Executable $Python -Arguments $nativeArguments
    if ($result.ExitCode -ne 0) { throw $FailureText }
    return @($result.Output)
}

try {
    $manifest = Read-JsonFile $updateManifestPath
    Assert-ManifestFiles $manifest $stage
    if ($manifest.package_type -ne 'Update' -or $manifest.manifest_version -ne 1 -or
        $manifest.architecture -ne 'x64') { throw 'Update package schema is unsupported.' }
    $targetVersion = Convert-Version ([string]$manifest.application_version)
    $minimumVersion = Convert-Version ([string]$manifest.minimum_supported_version)
    $maximumVersion = $null
    if ($manifest.maximum_supported_version) {
        $maximumVersion = Convert-Version ([string]$manifest.maximum_supported_version)
    }
    if ($manifest.data_schema_version -ne 1 -or $manifest.driver_manifest_version -ne 1) {
        throw 'Update requires a data or driver schema that this updater does not support.'
    }
    foreach ($relative in @('runtime\python.exe', 'app\portable_entry.py', 'app\sql',
                            'portable-manifest.json', 'app\default-driver-packages\mysql.zip',
                            'app\default-driver-packages\postgresql.zip',
                            'app\default-driver-packages\oracle.zip',
                            'app\default-driver-packages\sqlserver.zip')) {
        if (-not (Test-Path -LiteralPath (Join-Path $payload $relative))) {
            throw "Update package is missing required content: $relative"
        }
    }
    $declaredDrivers = @($manifest.default_driver_packages)
    if ($declaredDrivers.Count -ne 4 -or
        (@($declaredDrivers | ForEach-Object { [string]$_.database } | Sort-Object -Unique) -join ',') -ne 'mysql,oracle,postgresql,sqlserver') {
        throw 'Update manifest must identify the four default driver packages.'
    }
    foreach ($driverItem in $declaredDrivers) {
        $database = [string]$driverItem.database
        $expectedPath = "app/default-driver-packages/$database.zip"
        if ($database -notin @('mysql', 'postgresql', 'oracle', 'sqlserver') -or
            [string]$driverItem.path -ne $expectedPath -or
            [string]$driverItem.sha256 -notmatch '^[A-Fa-f0-9]{64}$') {
            throw 'Update default driver manifest entry is invalid.'
        }
        $bundlePath = Join-Path $payload ($expectedPath -replace '/', [IO.Path]::DirectorySeparatorChar)
        $identity = Get-DriverPackageIdentity $bundlePath $database
        if (-not $identity -or $identity.sha256 -ne ([string]$driverItem.sha256).ToLowerInvariant() -or
            $identity.driver_version -ne [string]$driverItem.driver_version -or
            $identity.python_abi -ne [string]$driverItem.python_abi) {
            throw "Update default driver identity or hash is invalid: $database"
        }
    }
    $targetPortable = Read-JsonFile (Join-Path $payload 'portable-manifest.json')
    if ($targetPortable.package_type -ne 'Full' -or
        $targetPortable.application_version -ne $manifest.application_version -or
        $targetPortable.build_id -ne $manifest.build_id) {
        throw 'Update and portable manifests do not agree.'
    }
    $currentManifestPath = Join-Path $root 'portable-manifest.json'
    foreach ($relative in @('runtime\python.exe', 'app\portable_entry.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $relative))) {
            throw 'The selected installation is not a supported portable Toolkit folder.'
        }
    }
    foreach ($launcher in @('升级.bat', 'upgrade.bat', '启动.bat', 'dba.bat',
                            'deployment\windows\upgrade-portable.ps1',
                            'deployment\windows\rollback-portable.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $payload $launcher) -PathType Leaf)) {
            throw "Update package is missing required launcher or recovery content: $launcher"
        }
    }
    if (-not (Test-Path -LiteralPath $currentManifestPath -PathType Leaf)) {
        throw 'The selected installation is missing its portable version manifest.'
    }
    $currentPortable = Read-JsonFile $currentManifestPath
    $currentVersionText = [string]($currentPortable.application_version)
    if (-not $currentVersionText) { $currentVersionText = [string]$currentPortable.product_version }
    $currentVersion = Convert-Version $currentVersionText
    if ($currentVersion -lt $minimumVersion -or ($maximumVersion -and $currentVersion -gt $maximumVersion)) {
        throw 'This Update package does not support the installed Toolkit version.'
    }
    if ($targetVersion -lt $currentVersion) { throw 'Downgrade is not allowed by an Update package.' }
    if ($targetVersion -eq $currentVersion -and
        [string]$currentPortable.build_id -eq [string]$manifest.build_id) {
        throw 'This Update package is already installed.'
    }
    $currentDataSchema = if ($currentPortable.data_schema_version) { [int]$currentPortable.data_schema_version } else { 1 }
    if ($currentDataSchema -ne [int]$manifest.data_schema_version) {
        throw 'Data schema migration is required and is not included in this Update package.'
    }
    $runtimeVersion = [string]$manifest.runtime_version
    Write-Host 'Checking the staged Python runtime and Toolkit entry point.'
    $runtimeExecutable = Join-Path $payload 'runtime\python.exe'
    # Avoid nested quotes in the native -c argument: Windows PowerShell 5.1
    # strips them when a command is forwarded through an argument array.
    $runtimeCode = 'import sys,platform; print(sys.version_info.major,sys.version_info.minor,sys.maxsize > 2**32,platform.machine().casefold())'
    $runtimeResult = Invoke-NativeCaptured -Executable $runtimeExecutable -Arguments @('-I', '-c', $runtimeCode)
    if ($runtimeResult.ExitCode -ne 0) { throw 'Staged Python Runtime failed its startup probe.' }
    $runtimeParts = ((@($runtimeResult.Output) -join ' ').Trim() -split '\s+')
    if ($runtimeParts.Count -ne 4) { throw 'Staged Python Runtime probe output is invalid.' }
    $runtimeDetected = $runtimeParts[0] + '.' + $runtimeParts[1]
    if ($runtimeDetected -ne $runtimeVersion -or $runtimeParts[2] -ne 'True' -or
        $runtimeParts[3] -notin @('amd64', 'x86_64', 'x64')) {
        throw 'Staged Python Runtime does not match the Update manifest.'
    }
    $stageEntry = Join-Path $payload 'app\portable_entry.py'
    $stagePython = Join-Path $payload 'runtime\python.exe'
    Write-Host 'Checking staged Toolkit version and SQL Registry.'
    [void](Invoke-Portable $stagePython $stageEntry @('--version') 'Staged Toolkit version check failed.')
    # Update payloads intentionally exclude user configuration. Validate the
    # staged Registry with a disposable empty Inventory instead of resolving
    # config/inventory.yaml from either the staging tree or the installation.
    $validationInventory = Join-Path $stage 'validation-inventory.yaml'
    [IO.File]::WriteAllText($validationInventory, "databases: []`n", [Text.UTF8Encoding]::new($false))
    try {
        [void](Invoke-Portable $stagePython $stageEntry `
            @('--inventory', $validationInventory, 'validate', '--json') `
            'Staged SQL Registry validation failed.')
    } finally {
        Remove-Item -LiteralPath $validationInventory -Force -ErrorAction SilentlyContinue
    }

    $driverUpdateCandidates = [Collections.Generic.List[object]]::new()
    foreach ($database in @('mysql', 'postgresql', 'oracle', 'sqlserver')) {
        $statePath = Join-Path $root ("drivers\" + $database + '\state.json')
        if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { continue }
        $state = Read-JsonFile $statePath 16KB
        $active = [string]$state.active
        if (-not $active) { continue }
        $knownDefaults = Get-DefaultDriverIds $root $database
        if (-not $knownDefaults.Contains($active)) { continue }
        $newBundle = Join-Path $payload ("app\default-driver-packages\" + $database + '.zip')
        $newIdentity = Get-DriverPackageIdentity $newBundle $database
        if (-not $newIdentity) { throw "Default driver package is invalid: $database" }
        $driverUpdateCandidates.Add([pscustomobject]@{
            database = $database
            state_path = $statePath
            new_bundle = $newBundle
            new_id = [string]$newIdentity.id
        })
    }

    $backupFolder = Join-Path $root 'backup'
    if (-not (Test-Path -LiteralPath $backupFolder)) { New-Item -ItemType Directory -Path $backupFolder | Out-Null }
    $label = if ($BuildTimestamp) { $BuildTimestamp } else { [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff') }
    $backup = Join-Path $backupFolder ("program-backup-" + $label + '-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path (Join-Path $backup 'program') | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $backup 'driver-state') | Out-Null
    foreach ($candidate in $driverUpdateCandidates) {
        $relative = $candidate.database + '\state.json'
        $stateBackup = Join-Path $backup ('driver-state\' + $relative)
        New-Item -ItemType Directory -Path (Split-Path -Parent $stateBackup) -Force | Out-Null
        Copy-Item -LiteralPath $candidate.state_path -Destination $stateBackup
        $driverStatePaths.Add($relative)
    }

    $hadOriginal = @{}
    foreach ($relative in $programPaths) {
        $targetPath = Join-Path $root $relative
        $sourcePath = Join-Path $payload $relative
        if (-not (Test-Path -LiteralPath $sourcePath)) { throw "Update package is missing program file: $relative" }
        $hadOriginal[$relative] = Test-Path -LiteralPath $targetPath
    }
    $rollbackManifest = [ordered]@{
        schema_version = 1
        installed_version = $currentVersionText
        update_version = [string]$manifest.application_version
        update_build_id = [string]$manifest.build_id
        program_paths = @($programPaths | ForEach-Object { [ordered]@{ path = $_; had_original = [bool]$hadOriginal[$_] } })
        driver_state_paths = @($driverStatePaths)
    }
    [IO.File]::WriteAllText((Join-Path $backup 'rollback-manifest.json'),
        ($rollbackManifest | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))

    try {
        foreach ($relative in $programPaths) {
            $destination = Join-Path $root $relative
            $saved = Join-Path $backup ('program\' + $relative)
            $source = Join-Path $payload $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            if (Test-Path -LiteralPath $destination) {
                New-Item -ItemType Directory -Path (Split-Path -Parent $saved) -Force | Out-Null
                Move-Item -LiteralPath $destination -Destination $saved
                $movedOld.Add($relative)
            }
            Move-Item -LiteralPath $source -Destination $destination
            $movedNew.Add($relative)
        }

        foreach ($candidate in $driverUpdateCandidates) {
            Write-Host ("Updating the managed default driver for " + $candidate.database + '.')
            $entry = Join-Path $root 'app\portable_entry.py'
            $python = Join-Path $root 'runtime\python.exe'
            $installedBundle = Join-Path $root ("app\default-driver-packages\" + $candidate.database + '.zip')
            $driverArguments = @('-I', $entry, 'driver', 'install', '--file', $installedBundle, '--json')
            $driverResult = Invoke-NativeCaptured -Executable $python -Arguments $driverArguments
            if ($driverResult.ExitCode -ne 0) { throw "Default driver update failed for $($candidate.database)." }
            $state = Read-JsonFile $candidate.state_path 16KB
            if ([string]$state.active -ne $candidate.new_id) {
                throw "Default driver state verification failed for $($candidate.database)."
            }
        }

        $startup = Join-Path $root 'deployment\windows\startup_check.py'
        $startupResult = Invoke-NativeCaptured -Executable (Join-Path $root 'runtime\python.exe') `
            -Arguments @('-I', $startup)
        if ($startupResult.ExitCode -ne 0) { throw 'Post-upgrade startup check failed.' }
    } catch {
        $cause = $_.Exception.Message
        try {
            Restore-PortableUpdateState -InstallRoot $root -BackupRoot $backup `
                -QuarantineRoot (Join-Path $stage 'failed-program') `
                -MovedOld @($movedOld) -MovedNew @($movedNew) `
                -DriverStatePaths @($driverStatePaths)
        } catch {
            throw "Update failed and automatic rollback was incomplete. Preserve the backup at $backup and recover manually."
        }
        throw "Update failed and the prior program was restored. Backup retained at $backup."
    }
    Write-Host "Update completed: $currentVersionText -> $($manifest.application_version)."
    Write-Host "Program backup retained at $backup. User data and custom driver selections were preserved."
    if ($driverUpdateCandidates.Count -lt 4) {
        Write-Host 'One or more active drivers were user-selected or unmanaged and were left unchanged.'
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
