Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$wuwaRoot = Split-Path -Parent $PSScriptRoot
$projectsRoot = Split-Path -Parent $wuwaRoot
$starRailRoot = Join-Path $projectsRoot "starRail"
$runtimeRoot = Join-Path $wuwaRoot "runtime\chain"
$runId = Get-Date -Format "yyyyMMdd_HHmmss"
$runRoot = Join-Path $runtimeRoot $runId
$logPath = Join-Path $runRoot "chain.log"
$resultPath = Join-Path $runRoot "result.json"
$uv = Get-Command uv -ErrorAction Stop
$mutex = New-Object System.Threading.Mutex($false, "Global\HCID274_GameDailyChain")
$lockTaken = $false

New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

function Write-ChainLog {
    param([string]$Message)
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Write-Host $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Invoke-Automation {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Write-ChainLog "start $Label"
    Push-Location $ProjectRoot
    try {
        & $uv.Source @Arguments *>> $logPath
        $code = $LASTEXITCODE
    }
    catch {
        Add-Content -LiteralPath $logPath -Value $_.Exception.ToString() -Encoding UTF8
        $code = 99
    }
    finally {
        Pop-Location
    }
    Write-ChainLog "finish $Label exit=$code"
    return [int]$code
}

$startedAt = (Get-Date).ToString("o")
$starRailCode = 99
$starRailCleanupCode = 99
$wuwaCode = 99
$finalCode = 99

try {
    $lockTaken = $mutex.WaitOne(0)
    if (-not $lockTaken) {
        Write-ChainLog "another daily chain owns the global lock"
        $finalCode = 75
        exit 75
    }
    if (-not (Test-Path $starRailRoot)) {
        throw "Star Rail project not found: $starRailRoot"
    }

    $starRailCode = Invoke-Automation `
        -ProjectRoot $starRailRoot `
        -Arguments @("run", "starrail-auto", "daily", "--timeout", "1800") `
        -Label "Star Rail daily"

    # Cleanup is a required hand-off boundary, even when the Star Rail run failed.
    $starRailCleanupCode = Invoke-Automation `
        -ProjectRoot $starRailRoot `
        -Arguments @("run", "starrail-auto", "cleanup") `
        -Label "Star Rail cleanup"

    if ($starRailCleanupCode -ne 0) {
        Invoke-Automation `
            -ProjectRoot $starRailRoot `
            -Arguments @("run", "starrail-auto", "uu", "stop") `
            -Label "Star Rail UU fallback stop" | Out-Null
        $starRailCleanupCode = Invoke-Automation `
            -ProjectRoot $starRailRoot `
            -Arguments @("run", "starrail-auto", "cleanup") `
            -Label "Star Rail cleanup verification"
    }

    if ($starRailCleanupCode -eq 0) {
        # A Star Rail business failure does not make Wuwa miss its own daily run.
        $wuwaCode = Invoke-Automation `
            -ProjectRoot $wuwaRoot `
            -Arguments @("run", "wuwa-auto", "daily") `
            -Label "Wuthering Waves daily"
    }
    else {
        Write-ChainLog "Wuwa skipped because the Star Rail desktop was not safely cleaned"
    }

    if ($starRailCleanupCode -ne 0) {
        $finalCode = 30
    }
    elseif ($starRailCode -ne 0) {
        $finalCode = 10
    }
    elseif ($wuwaCode -ne 0) {
        $finalCode = 20
    }
    else {
        $finalCode = 0
    }
}
catch {
    Write-ChainLog "chain exception: $($_.Exception.Message)"
    Add-Content -LiteralPath $logPath -Value $_.Exception.ToString() -Encoding UTF8
    $finalCode = 99
}
finally {
    $payload = [ordered]@{
        run_id = $runId
        started_at = $startedAt
        finished_at = (Get-Date).ToString("o")
        starrail_exit_code = $starRailCode
        starrail_cleanup_exit_code = $starRailCleanupCode
        wuwa_exit_code = $wuwaCode
        exit_code = $finalCode
        log_path = $logPath
    }
    $payload | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    if ($lockTaken) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}

exit $finalCode
