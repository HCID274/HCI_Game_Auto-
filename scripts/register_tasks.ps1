param(
    [string]$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$config = Import-PowerShellDataFile -LiteralPath (Join-Path $repoRoot 'config\automation.psd1')
$runnerScript = Join-Path $repoRoot 'orchestrator\run.ps1'
$powerShellExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath $runnerScript -PathType Leaf)) {
    throw "orchestrator not found: $runnerScript"
}

function Register-AutomationTask {
    param(
        [Parameter(Mandatory = $true)]$Definition,
        [Parameter(Mandatory = $true)]$Trigger,
        [string]$Mode
    )

    $argumentParts = @(
        '-NoProfile',
        '-WindowStyle', 'Hidden',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $runnerScript)
    )
    if ($Mode) { $argumentParts += @('-Mode', $Mode) }
    $argument = $argumentParts -join ' '
    $action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $argument -WorkingDirectory $repoRoot
    $principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours $Definition.ExecutionLimitHours) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable

    Register-ScheduledTask `
        -TaskName $Definition.Name `
        -Description $Definition.Description `
        -Action $action `
        -Trigger $Trigger `
        -Principal $principal `
        -Settings $settings `
        -Force | Out-Null
}

$daily = $config.Tasks.Daily
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At (Get-Date $daily.At)
Register-AutomationTask -Definition $daily -Trigger $dailyTrigger

$weekly = $config.Tasks.WeeklyGarden
$weeklyTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek $weekly.DayOfWeek `
    -At (Get-Date $weekly.At)
Register-AutomationTask -Definition $weekly -Trigger $weeklyTrigger -Mode 'weekly-garden'

foreach ($taskName in $config.LegacyTasks) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -ne 'Disabled') {
        Disable-ScheduledTask -TaskName $taskName | Out-Null
    }
}

foreach ($taskName in @($daily.Name, $weekly.Name)) {
    $task = Get-ScheduledTask -TaskName $taskName
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Host "registered: $taskName"
    Write-Host "  state: $($task.State)"
    Write-Host "  next run: $($info.NextRunTime)"
    Write-Host "  action: $($task.Actions.Execute) $($task.Actions.Arguments)"
}
