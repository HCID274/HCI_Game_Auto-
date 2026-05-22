param(
    [string]$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runnerScript = Join-Path $PSScriptRoot "run_scheduled_task.ps1"
$powerShellExe = Join-Path $PSHOME "powershell.exe"

if (-not (Test-Path $runnerScript)) {
    throw "runner script not found: $runnerScript"
}

function Register-StarRailTask {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskName,

        [Parameter(Mandatory = $true)]
        [ValidateSet("main", "universe", "cleanup")]
        [string]$Job,

        [Parameter(Mandatory = $true)]
        [string]$TimeOfDay,

        [Parameter(Mandatory = $true)]
        [int]$Timeout,

        [bool]$Enabled = $true
    )

    $argument = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $runnerScript),
        "-Task", $Job,
        "-Timeout", $Timeout
    ) -join " "

    $action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $argument
    $trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date $TimeOfDay)
    $principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Seconds ([int][math]::Ceiling($Timeout * 1.5))) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Description "Star Rail automation: $Job" `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings | Out-Null

    if (-not $Enabled) {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
    }

    $task = Get-ScheduledTask -TaskName $TaskName
    $info = Get-ScheduledTaskInfo -TaskName $TaskName

    Write-Host ""
    Write-Host "registered: $TaskName"
    Write-Host "  user: $UserId"
    Write-Host "  time: $TimeOfDay"
    Write-Host "  command: $powerShellExe $argument"
    Write-Host "  state: $($task.State)"
    Write-Host "  run level: $($task.Principal.RunLevel)"
    Write-Host "  logon type: $($task.Principal.LogonType)"
    Write-Host "  next run: $($info.NextRunTime)"
}

Register-StarRailTask -TaskName "StarRail_Main_0700" -Job "main" -TimeOfDay "07:00" -Timeout 1800
Register-StarRailTask -TaskName "StarRail_Cleanup_0800" -Job "cleanup" -TimeOfDay "08:00" -Timeout 600
Register-StarRailTask -TaskName "StarRail_Universe_2330" -Job "universe" -TimeOfDay "23:30" -Timeout 7200 -Enabled $false
