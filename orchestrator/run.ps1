param(
    [ValidateSet('auto', 'daily-chain', 'wuwa-daily', 'wuwa-daily-retry', 'farm-echo', 'weekly-garden', 'wuwa-cleanup', 'validate', 'integration-smoke')]
    [string]$Mode = 'auto',
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $repoRoot 'config\automation.psd1'
$config = Import-PowerShellDataFile -LiteralPath $configPath
$runtimeRoot = Join-Path $repoRoot 'runtime\orchestrator'
$modeRequestPath = Join-Path $runtimeRoot 'next-run.mode'
$runMode = if ($Mode -eq 'auto') { 'daily-chain' } else { $Mode }
$runId = Get-Date -Format 'yyyyMMdd_HHmmss'
$runRoot = Join-Path $runtimeRoot $runId
$logPath = Join-Path $runRoot 'orchestrator.log'
$resultPath = Join-Path $runRoot 'result.json'
$uv = Get-Command uv -ErrorAction Stop
$mutex = New-Object System.Threading.Mutex($false, $config.MutexName)
$lockTaken = $false

New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

if ($Mode -eq 'auto' -and (Test-Path -LiteralPath $modeRequestPath)) {
    $requestedMode = (Get-Content -LiteralPath $modeRequestPath -Raw).Trim()
    if ($requestedMode -notin @('wuwa-daily', 'farm-echo', 'weekly-garden', 'wuwa-cleanup')) {
        throw "unsupported one-shot mode: $requestedMode"
    }
    $runMode = $requestedMode
    Remove-Item -LiteralPath $modeRequestPath -Force
}

function Write-OrchestratorLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Write-Host $line
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Enter-OrchestratorMutex {
    param([int]$WaitMinutes = 0)

    $wait = [TimeSpan]::FromMinutes([Math]::Max(0, $WaitMinutes))
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    if ($WaitMinutes -gt 0) {
        Write-OrchestratorLog "waiting up to $WaitMinutes minutes for the global lock"
    }
    try {
        $acquired = $mutex.WaitOne($wait)
    }
    catch [System.Threading.AbandonedMutexException] {
        # An abandoned mutex is acquired by the waiter that observes it.
        $acquired = $true
        Write-OrchestratorLog 'acquired an abandoned global lock'
    }
    $watch.Stop()
    if ($acquired -and $WaitMinutes -gt 0) {
        Write-OrchestratorLog "global lock acquired after $([Math]::Round($watch.Elapsed.TotalSeconds, 1)) seconds"
    }
    return [bool]$acquired
}

function Get-AppRoot {
    param([Parameter(Mandatory = $true)][string]$AppName)
    $relativePath = $config.Apps[$AppName].RelativePath
    $path = Join-Path $repoRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        throw "$AppName project not found: $path"
    }
    return $path
}

function Invoke-AppCommand {
    param(
        [Parameter(Mandatory = $true)][string]$AppName,
        [Parameter(Mandatory = $true)][string]$CommandName,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $projectRoot = Get-AppRoot -AppName $AppName
    [string[]]$arguments = $config.Apps[$AppName].Commands[$CommandName]
    if (-not $arguments) {
        throw "command contract not found: $AppName.$CommandName"
    }

    Write-OrchestratorLog "start $Label"
    if ($DryRun) {
        Write-OrchestratorLog "dry-run cwd=$projectRoot command=uv $($arguments -join ' ')"
        Write-OrchestratorLog "finish $Label exit=0"
        return 0
    }

    try {
        $escapedArguments = @($arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' })
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $uv.Source
        $startInfo.Arguments = $escapedArguments -join ' '
        $startInfo.WorkingDirectory = $projectRoot
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $process = [System.Diagnostics.Process]::Start($startInfo)
        $process.WaitForExit()
        $code = $process.ExitCode
        if ($null -eq $code) {
            throw "$Label returned no process exit code"
        }
    }
    catch {
        Add-Content -LiteralPath $logPath -Value $_.Exception.ToString() -Encoding UTF8
        $code = 99
    }
    Write-OrchestratorLog "finish $Label exit=$code"
    return [int]$code
}

function Remove-RetiredTasks {
    if ($DryRun) { return }
    foreach ($taskName in $config.RemovedTasks) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
            Write-OrchestratorLog "removed retired task $taskName"
        }
    }
}

function Invoke-DesktopPreflight {
    if ($DryRun -or $runMode -eq 'validate') { return }

    $firewallScript = Join-Path $repoRoot 'apps\wuwa\scripts\configure_codex_firewall.ps1'
    if (-not (Test-Path -LiteralPath $firewallScript -PathType Leaf)) {
        throw "desktop preflight script not found: $firewallScript"
    }

    Write-OrchestratorLog 'start desktop firewall preflight'
    & $firewallScript 2>&1 | ForEach-Object {
        Write-OrchestratorLog "desktop firewall preflight: $_"
    }
    Write-OrchestratorLog 'finish desktop firewall preflight'
}

$startedAt = (Get-Date).ToString('o')
$starRailCode = 99
$starRailCleanupCode = 99
$wuwaCode = 99
$wuwaCleanupCode = 0
$finalCode = 99

try {
    $lockWaitMinutes = 0
    if ($runMode -eq 'weekly-garden') {
        $lockWaitMinutes = [int]$config.Tasks.WeeklyGarden.LockWaitMinutes
    }
    elseif ($runMode -eq 'wuwa-daily-retry') {
        $lockWaitMinutes = [int]$config.Tasks.WuwaDailyRetry.LockWaitMinutes
    }
    $lockTaken = Enter-OrchestratorMutex -WaitMinutes $lockWaitMinutes
    if (-not $lockTaken) {
        Write-OrchestratorLog "global lock wait expired after $lockWaitMinutes minutes"
        $finalCode = 75
    }
    else {
        Write-OrchestratorLog "run mode=$runMode dry_run=$([bool]$DryRun)"
        Invoke-DesktopPreflight
        switch ($runMode) {
            'daily-chain' {
                Remove-RetiredTasks
                $starRailCode = Invoke-AppCommand -AppName StarRail -CommandName Daily -Label 'Star Rail daily'
                $starRailCleanupCode = Invoke-AppCommand -AppName StarRail -CommandName Cleanup -Label 'Star Rail cleanup'

                if ($starRailCleanupCode -ne 0) {
                    Invoke-AppCommand -AppName StarRail -CommandName UuStop -Label 'Star Rail UU fallback stop' | Out-Null
                    $starRailCleanupCode = Invoke-AppCommand -AppName StarRail -CommandName Cleanup -Label 'Star Rail cleanup verification'
                }

                if ($starRailCleanupCode -eq 0) {
                    $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName Daily -Label 'Wuthering Waves daily'
                }
                else {
                    Write-OrchestratorLog 'Wuthering Waves skipped because the desktop handoff was unsafe'
                }
            }
            'farm-echo' {
                $starRailCode = 0
                $starRailCleanupCode = 0
                $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName FarmEcho -Label 'Wuthering Waves farm echo'
            }
            'wuwa-daily' {
                $starRailCode = 0
                $starRailCleanupCode = 0
                $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName Daily -Label 'Wuthering Waves daily'
            }
            'wuwa-daily-retry' {
                $starRailCode = 0
                $starRailCleanupCode = 0
                $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName DailyRetry -Label 'Wuthering Waves daily retry'
            }
            'weekly-garden' {
                $starRailCode = 0
                $starRailCleanupCode = 0
                $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName WeeklyGarden -Label 'Wuthering Waves weekly garden'
            }
            'wuwa-cleanup' {
                $starRailCode = 0
                $starRailCleanupCode = 0
                $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName Cleanup -Label 'Wuthering Waves cleanup only'
            }
            'validate' {
                $starRailCode = Invoke-AppCommand -AppName StarRail -CommandName Health -Label 'Star Rail CLI health'
                $starRailCleanupCode = 0
                $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName Health -Label 'Wuthering Waves CLI health'
            }
            'integration-smoke' {
                Remove-RetiredTasks
                $starRailCode = Invoke-AppCommand -AppName StarRail -CommandName UuStart -Label 'Star Rail UU real adapter'
                if ($starRailCode -eq 0) {
                    $starRailCode = Invoke-AppCommand -AppName StarRail -CommandName Smoke -Label 'Star Rail core substitute'
                }
                $starRailCleanupCode = Invoke-AppCommand -AppName StarRail -CommandName Cleanup -Label 'Star Rail real cleanup'
                if ($starRailCleanupCode -ne 0) {
                    Invoke-AppCommand -AppName StarRail -CommandName UuStop -Label 'Star Rail UU fallback stop' | Out-Null
                }
                if ($starRailCode -eq 0 -and $starRailCleanupCode -eq 0) {
                    $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName UuStart -Label 'Wuthering Waves UU real adapter'
                    if ($wuwaCode -eq 0) {
                        $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName Smoke -Label 'Wuthering Waves core substitute'
                    }
                    $wuwaCleanupCode = Invoke-AppCommand -AppName Wuwa -CommandName Cleanup -Label 'Wuthering Waves real cleanup'
                }
                else {
                    Write-OrchestratorLog 'Wuthering Waves smoke skipped because Star Rail handoff failed'
                    $wuwaCode = 30
                }
            }
        }

        if ($wuwaCleanupCode -ne 0) { $finalCode = 40 }
        elseif ($starRailCleanupCode -ne 0) { $finalCode = 30 }
        elseif ($starRailCode -ne 0) { $finalCode = 10 }
        elseif ($wuwaCode -ne 0) { $finalCode = 20 }
        else { $finalCode = 0 }
    }
}
catch {
    Write-OrchestratorLog "orchestrator exception: $($_.Exception.Message)"
    Add-Content -LiteralPath $logPath -Value $_.Exception.ToString() -Encoding UTF8
    $finalCode = 99
}
finally {
    $payload = [ordered]@{
        run_id = $runId
        run_mode = $runMode
        dry_run = [bool]$DryRun
        started_at = $startedAt
        finished_at = (Get-Date).ToString('o')
        starrail_exit_code = $starRailCode
        starrail_cleanup_exit_code = $starRailCleanupCode
        wuwa_exit_code = $wuwaCode
        wuwa_cleanup_exit_code = $wuwaCleanupCode
        exit_code = $finalCode
        log_path = $logPath
    }
    $payload | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    if ($lockTaken) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}

exit $finalCode
