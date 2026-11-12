[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Inventory,
    [Parameter(Mandatory)][ValidateSet('Baseline', 'A', 'B', 'C', 'D')][string]$Scenario,
    [string]$Settings,
    [switch]$PreflightOnly,
    [int]$WorkloadSessionId,
    [int]$WaitforSessionId,
    [int]$RootSessionId,
    [int]$VictimSessionId,
    [string]$BaselineJson
)

$ErrorActionPreference = 'Stop'
$hostPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if ($PSVersionTable.PSEdition -ne 'Desktop' -or (Get-Process -Id $PID).Path -ne $hostPowerShell) {
    throw 'Run from the authorized Windows host PowerShell.'
}
if (-not $PreflightOnly -and $Scenario -eq 'B' -and $WorkloadSessionId -le 0) {
    throw 'B requires the test workload SPID.'
}
if (-not $PreflightOnly -and $Scenario -eq 'C' -and
    ($RootSessionId -le 0 -or $VictimSessionId -le 0 -or $RootSessionId -eq $VictimSessionId)) {
    throw 'C requires distinct test root and victim SPIDs.'
}
if (-not $PreflightOnly -and $Scenario -eq 'D' -and -not $BaselineJson) {
    throw 'D requires the paired A or C baseline JSON.'
}

$approvedInventory = (Resolve-Path -LiteralPath $Inventory).Path
$baseline = if ($BaselineJson) { Get-Content -Raw -Encoding UTF8 -LiteralPath $BaselineJson | ConvertFrom-Json }
if (-not $PreflightOnly -and $Scenario -eq 'D' -and $baseline.report.instance.id -ne 'sqlserver2022') {
    throw 'The baseline must be from the same test asset.'
}
$approvedSettings = if ($Settings) { (Resolve-Path -LiteralPath $Settings).Path }
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Push-Location -LiteralPath $repo
try {
    $dba = (Resolve-Path -LiteralPath '.\.venv\Scripts\dba.exe').Path
    $python = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
    $pythonPath = & $python -E -c 'import sys; assert sys.platform == ''win32''; print(sys.executable)'
    if ($LASTEXITCODE -ne 0 -or $pythonPath -ne $python) { throw 'Project Windows Python could not be confirmed.' }

    $dbaOptions = @('--inventory', $approvedInventory, '--sql-root', (Resolve-Path -LiteralPath '.\sql').Path)
    if ($approvedSettings) { $dbaOptions += @('--settings', $approvedSettings) }
    $asset = & $dba @dbaOptions show sqlserver2022 --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $asset.enabled -or $asset.environment -ne 'test' -or
        $asset.db_type -ne 'sqlserver' -or $asset.host -ne '127.0.0.1' -or $asset.port -ne 1433 -or
        $asset.effective_connection.target -ne 'master' -or -not $asset.credential_alias) {
        throw 'The approved local SQL Server 2022 test asset could not be confirmed.'
    }
    $session = & $dba credentials status --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $session.status -ne 'UNLOCKED') { throw 'Confirm the existing monitor credential session.' }
    $monitor = & $dba credentials show $asset.credential_alias --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $monitor.status -ne 'STORED' -or $monitor.username -cne 'dba_monitor') {
        throw 'The selected alias must resolve to the approved dba_monitor account.'
    }
    $connection = & $dba @dbaOptions test-connection sqlserver2022 --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $connection.success -or $connection.version -ne '16.0.4305.5' -or
        $connection.connection.database -ne 'master' -or $connection.account_security.classification -ne 'NON_PRIVILEGED') {
        throw 'The connection or expected SQL Server build could not be confirmed.'
    }
    $check = & $dba @dbaOptions check sqlserver2022 --json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $check.status -ne 'READY') { throw 'The approved test asset is not READY.' }
    if ($PreflightOnly) {
        [pscustomobject]@{
            Asset = 'sqlserver2022'
            Version = $connection.version
            Database = $connection.connection.database
            MonitorAccount = $monitor.username
            Status = 'READY'
        }
        return
    }

    $output = Join-Path (Resolve-Path -LiteralPath '.\reports').Path ('slow-sqlserver-e2e-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $output | Out-Null
    $htmlPath = Join-Path $output "$Scenario.html"
    $jsonPath = Join-Path $output "$Scenario.json"
    & $dba @dbaOptions diagnose sqlserver2022 slow --json --report $htmlPath |
        Set-Content -Encoding UTF8 -LiteralPath $jsonPath
    if ($LASTEXITCODE -ne 0) { throw "Collection failed. Inspect the sanitized JSON at $jsonPath." }
    $snapshot = Get-Content -Raw -Encoding UTF8 -LiteralPath $jsonPath | ConvertFrom-Json
    $page = Get-Content -Raw -Encoding UTF8 -LiteralPath $htmlPath
    $details = $snapshot.report.details
    $requests = @($details.all_requests | Where-Object { $null -ne $_ })
    if ($snapshot.report.instance.id -ne 'sqlserver2022' -or $snapshot.report.instance.credential_alias -ne '[REDACTED]' -or
        $snapshot.incident_summary.data_completeness -ne 'COMPLETE') { throw "Snapshot is incomplete or mismatched. See $output." }

    $collectorCounts = [ordered]@{}
    $collectorRows = [ordered]@{
        active_requests = 'all_requests'
        active_sessions = 'all_sessions'
        transactions = 'transactions'
        connections = 'sources'
    }
    foreach ($collector in $collectorRows.Keys) {
        $sample = @($details.samples | Where-Object collector -eq $collector)
        $rows = $details.PSObject.Properties[$collectorRows[$collector]]
        if ($sample.Count -ne 1 -or -not $sample[0].success -or $sample[0].truncated -or
            $null -eq $sample[0].row_count -or $null -eq $rows -or
            $sample[0].row_count -ne @($rows.Value | Where-Object { $null -ne $_ }).Count) {
            throw "Collector $collector is incomplete or its count disagrees with JSON. See $output."
        }
        $collectorCounts[$collector] = $sample[0].row_count
    }

    $pairVerifier = @'
from html import unescape
import json
from pathlib import Path
import re
import sys

from dba.reporting import DEFAULT_HUMAN_REPORT_POLICY
from dba.reporting.normalizers import display_label, display_slow_label, display_slow_value

snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
page = Path(sys.argv[2]).read_text(encoding="utf-8")
summary = snapshot["incident_summary"]
requests = snapshot["report"]["details"]["all_requests"]
consistency = snapshot["snapshot_consistency"]
assert consistency == "BEST_EFFORT", "Missing sequential snapshot evidence"
assert summary["impact"]["active_requests"] == len(requests), "Incident request count disagrees with JSON"
status = re.search(r'class="status-pill [^"]+">.*?</i>(.*?)</span>', page, re.S)
assert status and unescape(status[1]) == " \u00b7 ".join(
    display_label(summary[key]) for key in ("status", "severity")), "HTML status or severity differs"
description = re.search(r'class="hero-description">(.*?)</p>', page)
expected = [
    display_label(summary["primary_problem"]), display_label(summary["observed_now"]),
    display_slow_value("\u91c7\u96c6\u5b8c\u6574\u6027", summary["data_completeness"]),
    display_slow_label(consistency),
]
assert description and [
    field.partition("\uff1a")[2] for field in unescape(description[1]).split(" \u00b7 ")
] == expected, "HTML diagnosis, completeness or snapshot consistency differs"

section = page.split('id="current-requests">', 1)[1].split('id="blocking-chain"', 1)[0]
cards = re.split(r'\bid="current-request-(\d+)"', section)[1:]
assert len(cards) // 2 == min(len(requests), DEFAULT_HUMAN_REPORT_POLICY.max_related_sessions)
for position, (key, card) in enumerate(zip(cards[::2], cards[1::2], strict=True)):
    assert int(key) == position + 1, "HTML request order differs"
    row = requests[position]
    statement = (row.get("statement_sql_text") or "").replace("\r\n", "\n").replace("\r", "\n")
    body = re.search(r"<code>(.*?)</code>", card, re.S)
    copy = re.search(r'data-copy="([^"]*)"', card)
    assert (unescape(body[1]) if body else "") == statement, f"HTML SQL differs for request {key}"
    assert (unescape(copy[1]) if copy else "") == statement, f"SQL copy differs for request {key}"
    assert ("<dd>\u5f53\u524d\u8bed\u53e5\u6587\u672c\u5df2\u622a\u65ad\u3002</dd>" in card) == bool(
        row.get("statement_sql_text_truncated")), f"SQL truncation flag differs for request {key}"
'@
    $pairVerifier | & $python -E - $jsonPath $htmlPath
    if ($LASTEXITCODE -ne 0) { throw "Same-capture JSON/HTML comparison failed. See $output." }

    $classifiedWaits = @($requests | Where-Object request_kind -eq 'ACTIVE_WAIT')
    $slowRequests = @($details.long_running | Where-Object { $null -ne $_ })
    $activeWaitSpids = @($classifiedWaits | ForEach-Object session_id)
    if (@($slowRequests | Where-Object request_kind -eq 'ACTIVE_WAIT').Count) {
        throw "A normal active wait appears in the long-running requests. See $output."
    }

    switch ($Scenario) {
        'Baseline' {
            if (@($details.all_sessions | Where-Object program_name -eq 'DBAToolkit-LoadTest').Count -or
                @($requests | Where-Object { $_.statement_sql_text -match 'DBA_TOOLKIT_LOAD:SQLSERVER:' }).Count) {
                throw "LoadTest is still visible in the baseline. See $output."
            }
        }
        'A' {
            $runnable = @($requests | Where-Object status -eq 'runnable').Count
            $running = @($requests | Where-Object status -eq 'running').Count
            if (-not $page.Contains('id="diagnosis-sql-concurrency"') -or
                -not $page.Contains("RUNNABLE $runnable ") -or -not $page.Contains("RUNNING $running ")) {
                throw "Concurrency section or status counts did not match. See $output."
            }
            foreach ($template in @('Q2_ORDER_JOIN', 'Q3_DETAIL_SELF_JOIN', 'Q4_CUSTOMER_AGG')) {
                $matching = @($requests | Where-Object { $_.statement_sql_text -match "\b$template\b" })
                if (-not $matching.Count -or @($matching | Where-Object {
                    $_.statement_sql_text_truncated -or $_.statement_sql_text -notmatch 'OPTION\s*\(HASH JOIN, MAXDOP 1\)\s*;?\s*$'
                }).Count) { throw "Template $template was absent or incomplete. See $output." }
            }
            $aggregate = [regex]::Match($page, '(?s)id="diagnosis-sql-concurrency"[^>]*>(.*?)</section>').Groups[1].Value
            $groups = [regex]::Matches($aggregate, '(?s)<tr><td>.*?</tr>')
            $expectedGroups = @($requests | Group-Object database_id, sql_handle, statement_start_offset, statement_end_offset)
            if ($groups.Count -lt 3 -or $groups.Count -ne [math]::Min(10, $expectedGroups.Count)) {
                throw "The visible SQL group count does not match the native identities. See $output."
            }
            $seenGroups = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
            foreach ($group in $groups) {
                $cells = [regex]::Matches($group.Value, '(?s)<td>(.*?)</td>')
                $anchor = [regex]::Match($cells[5].Value, '#current-request-(\d+)')
                if (-not $anchor.Success) { throw "A visible group has no representative request link. See $output." }
                $representative = $requests[[int]$anchor.Groups[1].Value - 1]
                if (-not $representative.database_id -or -not $representative.sql_handle -or
                    $null -eq $representative.statement_start_offset -or $null -eq $representative.statement_end_offset) {
                    throw "A visible group lacks native statement identity. See $output."
                }
                $key = "$($representative.database_id):$($representative.sql_handle):" +
                    "$($representative.statement_start_offset):$($representative.statement_end_offset)"
                $shownSession = [regex]::Replace($cells[5].Groups[1].Value, '<[^>]+>', '')
                if (-not $seenGroups.Add($key) -or $shownSession -ne [string]$representative.session_id) {
                    throw "A visible group is duplicated or the representative link targets a different session. See $output."
                }
                $members = @($requests | Where-Object {
                    $_.database_id -eq $representative.database_id -and $_.sql_handle -eq $representative.sql_handle -and
                    $_.statement_start_offset -eq $representative.statement_start_offset -and
                    $_.statement_end_offset -eq $representative.statement_end_offset
                })
                [long]$cpu = 0
                [long]$reads = 0
                foreach ($member in $members) {
                    if ($null -eq $member.cpu_ms -or $null -eq $member.logical_reads) {
                        throw "A visible group lacks complete resource counters. See $output."
                    }
                    $cpu += $member.cpu_ms
                    $reads += $member.logical_reads
                }
                $groupRunnable = @($members | Where-Object status -eq 'runnable').Count
                $shownCpu = [regex]::Match($cells[2].Groups[1].Value, '^[\d,]+').Value -replace ',', ''
                $shownReads = [regex]::Match($cells[3].Groups[1].Value, '^[\d,]+').Value -replace ',', ''
                if ($cells[1].Groups[1].Value -ne [string]$members.Count -or $shownCpu -ne [string]$cpu -or
                    $shownReads -ne [string]$reads -or $cells[4].Groups[1].Value -ne [string]$groupRunnable) {
                    throw "Visible group totals disagree with JSON. See $output."
                }
            }
        }
        'B' {
            $long = @($details.long_running | Where-Object {
                $_.session_id -eq $WorkloadSessionId -and $_.elapsed_ms -gt 10000 -and
                $_.blocking_session_id -eq 0 -and $_.command -ne 'WAITFOR' -and $_.request_kind -ne 'ACTIVE_WAIT' -and
                $_.statement_sql_text -and -not $_.statement_sql_text_truncated
            })
            if (-not $long.Count) { throw "The selected non-blocked business SQL was not sampled past 10s. See $output." }
            if ($WaitforSessionId -gt 0 -and $WaitforSessionId -notin $activeWaitSpids) {
                throw "The independent normal WAITFOR was not observed with the long request. See $output."
            }
        }
        'C' {
            $root = @($details.roots | Where-Object {
                $_.session_id -eq $RootSessionId -and $_.transaction_identity_verified -and $_.transaction_id
            })
            $waitfor = @($requests | Where-Object { $_.session_id -eq $RootSessionId -and $_.command -eq 'WAITFOR' })
            $victim = @($requests | Where-Object {
                $_.session_id -eq $VictimSessionId -and $_.blocking_session_id -eq $RootSessionId -and
                $_.wait_type -like 'LCK_M_*' -and $_.wait_ms -gt 0
            })
            if (-not $root.Count -or -not $waitfor.Count -or -not $victim.Count -or
                $RootSessionId -notin @($details.blocking_graph.roots)) { throw "Expected mixed blocking was not verified. See $output." }
        }
        'D' {
            $previous = $baseline.report.details
            $knownLoad = @($previous.all_requests | Where-Object { $_.statement_sql_text -match 'DBA_TOOLKIT_LOAD:SQLSERVER:' })
            if ($knownLoad.Count -and @($requests | Where-Object { $_.statement_sql_text -match 'DBA_TOOLKIT_LOAD:SQLSERVER:' }).Count) {
                throw "Load-test SQL is still visible. See $output."
            }
            if ($knownLoad.Count -and $requests.Count -ge @($previous.all_requests).Count) {
                throw "The total active-request count did not decrease from A. See $output."
            }
            if ($knownLoad.Count -and @($details.all_sessions | Where-Object program_name -eq 'DBAToolkit-LoadTest').Count) {
                throw "LoadTest sessions are still visible. See $output."
            }
            $verifiedRoots = @($previous.roots | Where-Object { $_.transaction_identity_verified -and $_.transaction_id })
            if (-not $knownLoad.Count -and -not $verifiedRoots.Count) { throw 'Use a successful A or C baseline.' }
            foreach ($root in $verifiedRoots) {
                if ($root.session_id -in @($details.blocking_graph.roots) -or
                    $root.transaction_id -in @($details.transactions | ForEach-Object { $_.transaction_id })) {
                    throw "Previous blocking root or transaction is still visible. See $output."
                }
                $previousVictims = @($previous.all_requests | Where-Object blocking_session_id -eq $root.session_id |
                    ForEach-Object session_id)
                $previousSessions = @($previous.all_sessions | Where-Object {
                    $_.session_id -eq $root.session_id -or $_.session_id -in $previousVictims
                })
                foreach ($oldSession in $previousSessions) {
                    if ($oldSession.login_time -and @($details.all_sessions | Where-Object {
                        $_.session_id -eq $oldSession.session_id -and $_.login_time -eq $oldSession.login_time
                    }).Count) { throw "A previous test session is still connected. See $output." }
                }
            }
        }
    }
    $verification = [pscustomobject]@{
        Evidence = 'WINDOWS_LOCAL_REAL_E2E'
        Status = 'PASSED'
        Scenario = $Scenario
        Target = "$($asset.host):$($asset.port)"
        Version = $connection.version
        Database = $connection.connection.database
        MonitorAccount = $monitor.username
        ActiveRequests = $requests.Count
        Running = @($requests | Where-Object status -eq 'running').Count
        Runnable = @($requests | Where-Object status -eq 'runnable').Count
        CollectorCounts = [pscustomobject]$collectorCounts
        CollectionCompleteness = $snapshot.incident_summary.data_completeness
        SnapshotConsistency = $snapshot.snapshot_consistency
        VisibleSqlParity = 'PASS'
        Json = $jsonPath
        Html = $htmlPath
    }
    $verification | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $output 'verification.json')
    $verification
}
finally {
    Pop-Location
}
