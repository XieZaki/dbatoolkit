[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $PSScriptRoot '..\..'),
    [string]$PackageZip = ''
)

$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Assert-OrdinaryPath([string]$Path, [string]$Label) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must not be a symbolic link or reparse point."
    }
}

function ConvertTo-SafeArchivePath([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name) -or $Name.Contains('\') -or $Name.Contains(':') -or
        $Name.StartsWith('/') -or $Name.StartsWith('\')) {
        throw 'Update archive contains an unsafe path.'
    }
    $parts = $Name.Split('/')
    if ($parts | Where-Object { $_ -eq '' -or $_ -eq '.' -or $_ -eq '..' }) {
        throw 'Update archive contains an unsafe path.'
    }
    return ($parts -join [IO.Path]::DirectorySeparatorChar)
}

function Test-ToolkitRunning([string]$Root) {
    $normalized = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    try {
        # Get-CimInstance depends on WMI permissions that are not guaranteed for
        # a per-user portable install. Toolkit launches always use the bundled
        # python/pythonw executable, so inspect only those processes instead.
        $processes = @(Get-Process -Name python,pythonw -ErrorAction SilentlyContinue)
    } catch {
        throw 'Could not verify that the portable Toolkit is stopped; close it and retry.'
    }
    foreach ($process in $processes) {
        try { $exe = [string]$process.Path }
        catch { throw 'Could not verify that the portable Toolkit is stopped; close it and retry.' }
        if (-not $exe) {
            throw 'Could not verify that the portable Toolkit is stopped; close it and retry.'
        }
        if ($exe.StartsWith((Join-Path $normalized 'runtime') + '\',
                [StringComparison]::OrdinalIgnoreCase)) {
            throw 'A Toolkit process is still running. Close it before upgrading.'
        }
    }
}

function Read-ZipManifest([IO.Compression.ZipArchive]$Archive) {
    $entry = $Archive.GetEntry('update-manifest.json')
    if (-not $entry -or $entry.Length -gt 4MB) { throw 'Update manifest is missing or too large.' }
    $reader = [IO.StreamReader]::new($entry.Open(), [Text.Encoding]::UTF8)
    try { return ($reader.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop) }
    finally { $reader.Dispose() }
}

function Expand-VerifiedUpdate([string]$ZipPath, [string]$StagePath, $Manifest) {
    $archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $entries = @{}
        $totalBytes = [int64]0
        $manifestSeen = $false
        foreach ($entry in $archive.Entries) {
            $archiveName = $entry.FullName.TrimEnd('/')
            $relative = ConvertTo-SafeArchivePath $archiveName
            if (-not $entry.Name) { continue }
            $key = $relative.ToLowerInvariant()
            if ($key -eq 'update-manifest.json') {
                if ($manifestSeen) { throw 'Update archive contains duplicate paths.' }
                if ($entry.Length -gt 4MB) { throw 'Update manifest is missing or too large.' }
                $manifestSeen = $true
                $destination = Join-Path $StagePath 'update-manifest.json'
                $input = $entry.Open()
                $output = [IO.File]::Open($destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                try {
                    $input.CopyTo($output)
                    $output.Flush()
                } finally {
                    $output.Dispose()
                    $input.Dispose()
                }
                continue
            }
            if ($entries.ContainsKey($key)) { throw 'Update archive contains duplicate paths.' }
            $unixType = (($entry.ExternalAttributes -shr 16) -band 0xF000)
            if ($unixType -eq 0xA000) { throw 'Update archive contains a symbolic link.' }
            $entries[$key] = $entry
            $totalBytes += $entry.Length
        }
        $declared = @{}
        foreach ($item in @($Manifest.file_manifest)) {
            $relative = ConvertTo-SafeArchivePath ([string]$item.path)
            $key = $relative.ToLowerInvariant()
            if ($declared.ContainsKey($key) -or $key -eq 'update-manifest.json' -or
                [int64]$item.size -lt 0 -or [string]$item.sha256 -notmatch '^[a-fA-F0-9]{64}$') {
                throw 'Update file manifest is invalid.'
            }
            $declared[$key] = $item
        }
        if (-not $manifestSeen -or $declared.Count -eq 0 -or $declared.Count -ne $entries.Count -or
            [int64]$Manifest.payload_total_bytes -ne $totalBytes) {
            throw 'Update archive entries do not match its file manifest.'
        }
        foreach ($key in $declared.Keys) {
            if (-not $entries.ContainsKey($key)) { throw 'Update archive is missing a manifest file.' }
            $entry = $entries[$key]
            $item = $declared[$key]
            if ([int64]$entry.Length -ne [int64]$item.size) { throw 'Update file size verification failed.' }
            $relative = ConvertTo-SafeArchivePath ([string]$item.path)
            $destination = [IO.Path]::GetFullPath((Join-Path $StagePath $relative))
            $prefix = [IO.Path]::GetFullPath($StagePath).TrimEnd('\') + '\'
            if (-not $destination.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Update archive path escapes the staging directory.'
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            $input = $entry.Open()
            $output = [IO.File]::Open($destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $input.CopyTo($output)
                $output.Flush()
            } finally {
                $output.Dispose()
                $input.Dispose()
            }
            $actualHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actualHash -ne ([string]$item.sha256).ToLowerInvariant()) {
                throw 'Update payload hash verification failed.'
            }
        }
    } finally {
        $archive.Dispose()
    }
}

$root = [IO.Path]::GetFullPath($InstallRoot)
if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw 'Portable installation directory was not found.' }
Assert-OrdinaryPath $root 'Installation directory'
$updates = Join-Path $root 'updates'
if (-not (Test-Path -LiteralPath $updates)) { New-Item -ItemType Directory -Path $updates | Out-Null }
Assert-OrdinaryPath $updates 'Updates directory'

if ($PackageZip) {
    $zip = (Resolve-Path -LiteralPath $PackageZip).Path
} else {
    $candidates = @(Get-ChildItem -LiteralPath $updates -Filter 'DBAToolkit-*-Windows-x64-Update.zip' -File)
    if ($candidates.Count -ne 1) { throw 'Place exactly one DBAToolkit Windows x64 Update ZIP in updates and retry.' }
    $zip = $candidates[0].FullName
}
$file = Get-Item -LiteralPath $zip -Force
Assert-OrdinaryPath $zip 'Update archive'
Test-ToolkitRunning $root

$archiveForManifest = [IO.Compression.ZipFile]::OpenRead($zip)
try { $manifest = Read-ZipManifest $archiveForManifest }
finally { $archiveForManifest.Dispose() }
if ($manifest.manifest_version -ne 1 -or $manifest.package_type -ne 'Update' -or
    $manifest.architecture -ne 'x64' -or $manifest.application_version -notmatch '^\d+\.\d+\.\d+$' -or
    [int]$manifest.data_schema_version -lt 1 -or [int]$manifest.driver_manifest_version -lt 1) {
    throw 'Update manifest version, package type, architecture, or schema is unsupported.'
}
$minimumSpace = [int64]$manifest.payload_total_bytes + 64MB
$drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))
if ($drive.AvailableFreeSpace -lt $minimumSpace) { throw 'Insufficient free disk space for a staged, recoverable upgrade.' }

$stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
$stage = Join-Path $updates ('.staging-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    Expand-VerifiedUpdate $zip $stage $manifest
    $executor = Join-Path $stage 'update-tools\apply-update.ps1'
    if (-not (Test-Path -LiteralPath $executor -PathType Leaf)) { throw 'Update package has no external update executor.' }
    & $executor -InstallRoot $root -StageRoot $stage -BuildTimestamp $stamp
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Update executor reported failure.' }
} catch {
    [Console]::Error.WriteLine('Update did not complete. The staging files and any program backup were retained for recovery.')
    throw
}
