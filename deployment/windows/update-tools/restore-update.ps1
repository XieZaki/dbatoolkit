function Restore-PortableUpdateState {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [Parameter(Mandatory = $true)][string]$BackupRoot,
        [Parameter(Mandatory = $true)][string]$QuarantineRoot,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$MovedOld,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$MovedNew,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$DriverStatePaths
    )

    $ErrorActionPreference = 'Stop'
    New-Item -ItemType Directory -Path $QuarantineRoot -Force | Out-Null
    for ($index = $MovedNew.Count - 1; $index -ge 0; $index--) {
        $relative = $MovedNew[$index]
        $destination = Join-Path $InstallRoot $relative
        if (Test-Path -LiteralPath $destination) {
            $failed = Join-Path $QuarantineRoot $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $failed) -Force | Out-Null
            if (Test-Path -LiteralPath $failed) { throw 'Rollback quarantine path already exists.' }
            Move-Item -LiteralPath $destination -Destination $failed
        }
    }
    for ($index = $MovedOld.Count - 1; $index -ge 0; $index--) {
        $relative = $MovedOld[$index]
        $saved = Join-Path $BackupRoot ('program\' + $relative)
        $destination = Join-Path $InstallRoot $relative
        if (-not (Test-Path -LiteralPath $saved)) { throw 'Program backup is incomplete; manual recovery is required.' }
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Move-Item -LiteralPath $saved -Destination $destination
    }
    foreach ($relative in $DriverStatePaths) {
        $saved = Join-Path $BackupRoot ('driver-state\' + $relative)
        $destination = Join-Path $InstallRoot ('drivers\' + $relative)
        if (-not (Test-Path -LiteralPath $saved)) { throw 'Driver state backup is incomplete; manual recovery is required.' }
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        $temporary = $destination + '.rollback-' + [guid]::NewGuid().ToString('N')
        Copy-Item -LiteralPath $saved -Destination $temporary
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    }
}
