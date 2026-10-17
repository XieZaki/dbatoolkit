# Explicit V0.7.1 verification against existing oraclefree. Never run in ordinary pytest.
# Administrative SQL creates/removes only uniquely named test users and their test table.
$ErrorActionPreference = 'Stop'
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCommand) { throw 'docker CLI not found on PATH.' }
$dockerTool = $dockerCommand.Source
$verificationRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$oracleName = 'oraclefree'
$clientName = 'dba-toolkit-oracle-client'
$monitorName = 'DBA_TOOLKIT_V071_MON'
$actorName = 'DBA_TOOLKIT_V071_ACT'
$monitorPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)) + 'aA1!'
$actorPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)) + 'aA1!'
$clientCreated = $false
$monitorCreated = $false
$actorCreated = $false
$cleanupErrors = [System.Collections.Generic.List[string]]::new()

function Invoke-AdminSql {
    param([string]$Sql)
    $output = $Sql | & $dockerTool exec -i $oracleName sqlplus -s / as sysdba 2>&1
    if ($LASTEXITCODE -ne 0 -or ($output -match 'ORA-[0-9]+|SP2-[0-9]+')) {
        throw 'Oracle test initialization/cleanup failed; SQL output suppressed.'
    }
    return $output
}

function Remove-TestUser {
    param([string]$UserName)
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        try {
            $sql = @"
whenever sqlerror exit 5
alter session set container=FREEPDB1;
drop user $UserName cascade;
exit
"@
            Invoke-AdminSql -Sql $sql | Out-Null
            return
        }
        catch { Start-Sleep -Seconds 1 }
    }
    $cleanupErrors.Add("Could not remove temporary test user $UserName")
}

try {
    & $dockerTool info --format '{{.ServerVersion}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker engine unavailable.' }
    $status = & $dockerTool inspect $oracleName --format '{{.State.Status}} {{.State.Health.Status}}'
    if ($LASTEXITCODE -ne 0 -or $status -ne 'running healthy') {
        throw 'Existing oraclefree test container is not healthy.'
    }
    $serviceProbe = @'
set heading off feedback off pagesize 0
whenever sqlerror exit 5
select name from v$services where lower(name)='freepdb1';
exit
'@
    if (-not ((Invoke-AdminSql -Sql $serviceProbe) -match 'freepdb1')) {
        throw 'Confirmed freepdb1 service is unavailable.'
    }
    $existing = @'
set heading off feedback off pagesize 0
whenever sqlerror exit 5
alter session set container=FREEPDB1;
select username from dba_users where username in ('DBA_TOOLKIT_V071_MON','DBA_TOOLKIT_V071_ACT');
exit
'@
    if ((Invoke-AdminSql -Sql $existing) -match 'DBA_TOOLKIT_V071_(MON|ACT)') {
        throw 'A V0.7.1 test user already exists; manual review required.'
    }
    $names = & $dockerTool ps -a --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0 -or $names -contains $clientName) {
        throw 'Oracle client container name already exists; manual review required.'
    }
    $createArgs = @('create', '--name', $clientName, '--network', "container:$oracleName",
        '--mount', "type=bind,source=$verificationRoot,target=/workspace,readonly",
        'dba-toolkit-mysql-client:v06', 'sleep', 'infinity')
    & $dockerTool @createArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create temporary Python client.' }
    $clientCreated = $true
    & $dockerTool start $clientName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not start temporary Python client.' }
    & $dockerTool exec $clientName python --version
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 client unavailable.' }
    & $dockerTool exec -u root $clientName pip install --no-cache-dir --disable-pip-version-check --index-url https://pypi.org/simple 'oracledb>=2,<4' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Temporary client driver installation failed.' }
    & $dockerTool exec $clientName python -c 'import oracledb, yaml; print("oracledb", oracledb.__version__)'
    if ($LASTEXITCODE -ne 0) { throw 'Client dependencies unavailable.' }

    $createMonitor = @"
whenever sqlerror exit 5
alter session set container=FREEPDB1;
create user $monitorName identified by "$monitorPassword";
exit
"@
    Invoke-AdminSql -Sql $createMonitor | Out-Null
    $monitorCreated = $true
    $createActor = @"
whenever sqlerror exit 5
alter session set container=FREEPDB1;
create user $actorName identified by "$actorPassword";
exit
"@
    Invoke-AdminSql -Sql $createActor | Out-Null
    $actorCreated = $true
    $setup = @"
whenever sqlerror exit 5
alter session set container=FREEPDB1;
grant create session to $monitorName;
grant create session, create table to $actorName;
alter user $actorName quota 1m on USERS;
"@
    foreach ($view in @('V_$INSTANCE','V_$DATABASE','V_$SESSION','V_$SQL','V_$TRANSACTION',
                         'V_$LOCK','V_$LOCKED_OBJECT','V_$PROCESS','V_$SESSION_WAIT')) {
        $setup += [Environment]::NewLine + "grant select on SYS.$view to $monitorName;"
    }
    $setup += [Environment]::NewLine + @"
create table $actorName.DBA_TOOLKIT_LOCK_TEST (id number primary key, marker number);
insert into $actorName.DBA_TOOLKIT_LOCK_TEST values (1,0);
insert into $actorName.DBA_TOOLKIT_LOCK_TEST values (2,0);
commit;
exit
"@
    Invoke-AdminSql -Sql $setup | Out-Null
    $runtimeInput = @{
        host = '127.0.0.1'
        port = 1521
        service_name = 'freepdb1'
        monitor_password = $monitorPassword
        actor_password = $actorPassword
    } | ConvertTo-Json -Compress
    $result = $runtimeInput | & $dockerTool exec -i $clientName python /workspace/tests/integration/verify_oracle.py
    $verificationCode = $LASTEXITCODE
    if ($verificationCode -ne 0) {
        $result
        throw 'Oracle CLI end-to-end verification failed; see sanitized status.'
    }
    if (($result -join '') -match [regex]::Escape($monitorPassword) -or
        ($result -join '') -match [regex]::Escape($actorPassword)) {
        throw 'Verification output unexpectedly contained a credential.'
    }
    New-Item -ItemType Directory -Force (Join-Path $verificationRoot '.tmp') | Out-Null
    $result | Set-Content -LiteralPath (Join-Path $verificationRoot '.tmp/oracle-v071-verification.json') -Encoding utf8
    $result
}
finally {
    $runtimeInput = $null
    if ($clientCreated) {
        & $dockerTool stop $clientName | Out-Null
        & $dockerTool rm $clientName | Out-Null
    }
    if ($actorCreated) { Remove-TestUser -UserName $actorName }
    if ($monitorCreated) { Remove-TestUser -UserName $monitorName }
    $monitorPassword = $actorPassword = $null
    if ($cleanupErrors.Count -gt 0) { throw ($cleanupErrors -join '; ') }
    if ($clientCreated) {
        Write-Output 'Temporary Oracle client and test users removed; oraclefree retained.'
    }
}
