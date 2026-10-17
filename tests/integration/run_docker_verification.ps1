# Explicitly approved disposable-container initialization only; never production.
param([switch]$Slow, [switch]$Plan)
$ErrorActionPreference = 'Stop'
if ($Slow -and $Plan) { throw 'Choose one verification scenario.' }
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCommand) { throw 'docker CLI not found on PATH.' }
$dockerTool = $dockerCommand.Source
$verificationRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$networkName = 'dba-toolkit-v021-isolated'
$serverName = 'dba-toolkit-v021-sqlserver'
$clientName = 'dba-toolkit-v021-client'

function Invoke-DockerChecked {
    param([string[]]$Arguments)
    & $dockerTool @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker verification step failed; no credentials are printed.' }
}

# Do not replace or delete any existing container/network with these names.
$engineVersion = & $dockerTool info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker engine is unavailable; no verification resources were created.' }
$existingNames = & $dockerTool ps -a --format '{{.Names}}'
if ($LASTEXITCODE -ne 0) { throw 'Cannot check existing verification containers.' }
if ($existingNames -contains $serverName -or $existingNames -contains $clientName) {
    throw 'Verification container name already exists; manual review required.'
}
$existingNetworks = & $dockerTool network ls --format '{{.Name}}'
if ($LASTEXITCODE -ne 0) { throw 'Cannot check existing verification networks.' }
if ($existingNetworks -contains $networkName) { throw 'Verification network already exists.' }

Invoke-DockerChecked -Arguments @('build', '-t', 'dba-toolkit-verification-client:v021', '-f',
    (Join-Path $PSScriptRoot 'Dockerfile.verify'), $PSScriptRoot)

$initialPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)) + 'aA1!'
$monitorPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)) + 'aA1!'
$env:MSSQL_SA_PASSWORD = $initialPassword
$createdServer = $false
$createdClient = $false
$createdNetwork = $false
try {
    Invoke-DockerChecked -Arguments @('network', 'create', '--internal', $networkName)
    $createdNetwork = $true
    Invoke-DockerChecked -Arguments @('create', '--name', $serverName, '--network', $networkName,
        '-p', '127.0.0.1:11433:1433', '-e', 'ACCEPT_EULA=Y', '-e', 'MSSQL_PID=Developer',
        '-e', 'MSSQL_SA_PASSWORD', 'mcr.microsoft.com/mssql/server:2022-latest')
    $createdServer = $true
    Invoke-DockerChecked -Arguments @('start', $serverName)
    Invoke-DockerChecked -Arguments @('create', '--name', $clientName, '--network', $networkName,
        '--mount', "type=bind,source=$verificationRoot,target=/workspace,readonly",
        'dba-toolkit-verification-client:v021', 'sleep', 'infinity')
    $createdClient = $true
    Invoke-DockerChecked -Arguments @('start', $clientName)
    # Credentials flow only through stdin, not files or command arguments.
    $bootstrapInput = @{host=$serverName; administrator_password=$initialPassword; monitor_password=$monitorPassword} | ConvertTo-Json -Compress
    $bootstrapInput | & $dockerTool exec -i $clientName python /workspace/tests/integration/verify_sqlserver.py bootstrap
    if ($LASTEXITCODE -ne 0) {
        # Diagnose only this disposable server: password is read in its shell,
        # never passed as a command argument or included in output.
        $nativeProbeOutput = & $dockerTool exec $serverName bash -c 'export SQLCMDPASSWORD="$MSSQL_SA_PASSWORD"; /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C -l 5 -Q "SELECT 1;"' 2>&1
        Write-Output "Native bootstrap authentication exit code: $LASTEXITCODE"
        $nativeProbeOutput = $null
        throw 'Isolated account initialization failed.'
    }
    $verificationScript = '/workspace/tests/integration/verify_sqlserver.py'
    $reportName = 'docker-verification.json'
    $verificationData = @{host=$serverName; monitor_password=$monitorPassword}
    if ($Slow) {
        # Explicit V0.3 fixture workload exception, only this new empty container.
        $verificationData.administrator_password = $initialPassword
        $verificationData.workload_password = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)) + 'aA1!'
        $verificationScript = '/workspace/tests/integration/verify_slow_sqlserver.py'
        $reportName = 'docker-slow-verification.json'
    }
    if ($Plan) {
        # Approved fixture initialization in this freshly created container only.
        $verificationData.administrator_password = $initialPassword
        $verificationData.workload_password = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)) + 'aA1!'
        $verificationScript = '/workspace/tests/integration/verify_plan_sqlserver.py'
        $reportName = 'docker-plan-verification.json'
    }
    $verificationInput = $verificationData | ConvertTo-Json -Compress
    $output = $verificationInput | & $dockerTool exec -i $clientName python $verificationScript verify
    $verificationCode = $LASTEXITCODE
    # Runner emits summaries only after checking its captured streams for leakage.
    New-Item -ItemType Directory -Force (Join-Path $verificationRoot '.tmp') | Out-Null
    $output | Set-Content -LiteralPath (Join-Path $verificationRoot ".tmp/$reportName") -Encoding utf8
    $output
    if ($verificationCode -ne 0) { throw 'Real verification found failures; see sanitized report.' }
}
finally {
    Remove-Item Env:MSSQL_SA_PASSWORD -ErrorAction SilentlyContinue
    $initialPassword = $monitorPassword = $bootstrapInput = $verificationInput = $verificationData = $null
    # Only stop containers created by this run. Do not touch other containers.
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
        Write-Output 'Disposable verification containers stopped and removed; sanitized report retained.'
    }
}
