param(
    [string]$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$taskName = "Game_Daily_0530"
$runnerScript = Join-Path $PSScriptRoot "run_daily_chain.ps1"
$powerShellExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path $runnerScript)) {
    throw "chain runner not found: $runnerScript"
}

$argument = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"{0}"' -f $runnerScript)
) -join " "

$action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $argument
$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date "05:30")
$principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $taskName `
    -Description "05:30 Star Rail -> safe cleanup -> Wuthering Waves daily chain" `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings | Out-Null

foreach ($oldTaskName in @("StarRail_Main_0600", "StarRail_Cleanup_0800")) {
    $oldTask = Get-ScheduledTask -TaskName $oldTaskName -ErrorAction SilentlyContinue
    if ($oldTask -and $oldTask.State -ne "Disabled") {
        Disable-ScheduledTask -TaskName $oldTaskName | Out-Null
        Write-Host "disabled conflicting task: $oldTaskName"
    }
}

$task = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host "registered: $taskName"
Write-Host "  user: $UserId"
Write-Host "  next run: $($info.NextRunTime)"
Write-Host "  run level: $($task.Principal.RunLevel)"
Write-Host "  logon type: $($task.Principal.LogonType)"
Write-Host "  action: $powerShellExe $argument"
