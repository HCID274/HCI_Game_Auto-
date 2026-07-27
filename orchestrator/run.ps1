param(
    [ValidateSet('auto', 'daily-chain', 'farm-echo', 'weekly-garden', 'validate')]
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
    if ($requestedMode -notin @('farm-echo', 'weekly-garden')) {
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

function Disable-LegacyTasks {
    if ($DryRun) { return }
    foreach ($taskName in $config.LegacyTasks) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task -and $task.State -ne 'Disabled') {
            Disable-ScheduledTask -TaskName $taskName | Out-Null
            Write-OrchestratorLog "disabled conflicting task $taskName"
        }
    }
}

$startedAt = (Get-Date).ToString('o')
$starRailCode = 99
$starRailCleanupCode = 99
$wuwaCode = 99
$finalCode = 99

try {
    $lockTaken = $mutex.WaitOne(0)
    if (-not $lockTaken) {
        Write-OrchestratorLog 'another automation workflow owns the global lock'
        $finalCode = 75
    }
    else {
        Write-OrchestratorLog "run mode=$runMode dry_run=$([bool]$DryRun)"
        switch ($runMode) {
            'daily-chain' {
                Disable-LegacyTasks
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
            'weekly-garden' {
                $starRailCode = 0
                $starRailCleanupCode = 0
                $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName WeeklyGarden -Label 'Wuthering Waves weekly garden'
            }
            'validate' {
                $starRailCode = Invoke-AppCommand -AppName StarRail -CommandName Health -Label 'Star Rail CLI health'
                $starRailCleanupCode = 0
                $wuwaCode = Invoke-AppCommand -AppName Wuwa -CommandName Health -Label 'Wuthering Waves CLI health'
            }
        }

        if ($starRailCleanupCode -ne 0) { $finalCode = 30 }
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
        exit_code = $finalCode
        log_path = $logPath
    }
    $payload | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    if ($lockTaken) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}

exit $finalCode
