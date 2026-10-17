# Explicit disposable-container fixture only; never production.
$ErrorActionPreference = 'Stop'
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCommand) { throw 'docker CLI not found on PATH.' }
$dockerTool = $dockerCommand.Source
$verificationRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$networkName = 'dba-toolkit-v06-isolated'
$serverName = 'dba-toolkit-v06-mysql'
$clientName = 'dba-toolkit-v06-client'

function Invoke-DockerChecked {
    param([string[]]$Arguments)
    & $dockerTool @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker verification step failed; credentials suppressed.' }
}

& $dockerTool info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker engine unavailable; no verification resources created.' }
$existingNames = & $dockerTool ps -a --format '{{.Names}}'
if ($LASTEXITCODE -ne 0 -or $existingNames -contains $serverName -or $existingNames -contains $clientName) {
    throw 'Verification container name exists or cannot be checked; manual review required.'
}
$existingNetworks = & $dockerTool network ls --format '{{.Name}}'
if ($LASTEXITCODE -ne 0 -or $existingNetworks -contains $networkName) {
    throw 'Verification network exists or cannot be checked; manual review required.'
}

Invoke-DockerChecked -Arguments @('build', '-t', 'dba-toolkit-mysql-client:v06',
    '-f', (Join-Path $PSScriptRoot 'Dockerfile.mysql'), $PSScriptRoot)
$administratorPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)) + 'aA1!'
$monitorPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)) + 'aA1!'
$workloadPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)) + 'aA1!'
$env:MYSQL_ROOT_PASSWORD = $administratorPassword
$env:MYSQL_PASSWORD = $workloadPassword
$createdServer = $false
$createdClient = $false
$createdNetwork = $false
try {
    Invoke-DockerChecked -Arguments @('network', 'create', '--internal', $networkName)
    $createdNetwork = $true
    Invoke-DockerChecked -Arguments @('create', '--name', $serverName, '--network', $networkName,
        '-e', 'MYSQL_ROOT_PASSWORD', '-e', 'MYSQL_ROOT_HOST=%', '-e', 'MYSQL_DATABASE=toolkit_fixture',
        '-e', 'MYSQL_USER=toolkit_workload', '-e', 'MYSQL_PASSWORD', 'mysql:8.0')
    $createdServer = $true
    Invoke-DockerChecked -Arguments @('start', $serverName)
    Invoke-DockerChecked -Arguments @('create', '--name', $clientName, '--network', $networkName,
        '--mount', "type=bind,source=$verificationRoot,target=/workspace,readonly",
        'dba-toolkit-mysql-client:v06', 'sleep', 'infinity')
    $createdClient = $true
    Invoke-DockerChecked -Arguments @('start', $clientName)
    $verificationInput = @{
        host = $serverName
        administrator_password = $administratorPassword
        monitor_password = $monitorPassword
        workload_password = $workloadPassword
    } | ConvertTo-Json -Compress
    $output = $verificationInput | & $dockerTool exec -i $clientName python /workspace/tests/integration/verify_mysql.py
    $verificationCode = $LASTEXITCODE
    New-Item -ItemType Directory -Force (Join-Path $verificationRoot '.tmp') | Out-Null
    $output | Set-Content -LiteralPath (Join-Path $verificationRoot '.tmp/docker-mysql-verification.json') -Encoding utf8
    $output
    if ($verificationCode -ne 0) { throw 'Real MySQL verification failed; see sanitized report.' }
}
finally {
    Remove-Item Env:MYSQL_ROOT_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:MYSQL_PASSWORD -ErrorAction SilentlyContinue
    $administratorPassword = $monitorPassword = $workloadPassword = $verificationInput = $null
    if ($createdClient) {
        & $dockerTool stop $clientName | Out-Null
        & $dockerTool rm $clientName | Out-Null
    }
    if ($createdServer) {
        & $dockerTool stop $serverName | Out-Null
        & $dockerTool rm $serverName | Out-Null
    }
    if ($createdNetwork) {
        & $dockerTool network rm $networkName | Out-Null
        Write-Output 'Disposable MySQL verification resources removed.'
    }
}
