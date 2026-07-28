param(
    [switch]$HoldLock,
    [int]$HoldMilliseconds = 1500,
    [string]$ReadyEventName = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$config = Import-PowerShellDataFile -LiteralPath (Join-Path $repoRoot 'config\automation.psd1')

if ($HoldLock) {
    if (-not $ReadyEventName) { throw 'ReadyEventName is required in holder mode' }
    $mutex = New-Object System.Threading.Mutex($false, $config.MutexName)
    $ready = New-Object System.Threading.EventWaitHandle(
        $false,
        [System.Threading.EventResetMode]::ManualReset,
        $ReadyEventName
    )
    $lockTaken = $false
    try {
        try {
            $lockTaken = $mutex.WaitOne(0)
        }
        catch [System.Threading.AbandonedMutexException] {
            $lockTaken = $true
        }
        if (-not $lockTaken) { throw 'test holder could not acquire the global lock' }
        [void]$ready.Set()
        Start-Sleep -Milliseconds $HoldMilliseconds
    }
    finally {
        if ($lockTaken) { $mutex.ReleaseMutex() }
        $ready.Dispose()
        $mutex.Dispose()
    }
    exit 0
}

$eventName = "Global\HCID274_GameAutomation_LockTest_$PID"
$ready = New-Object System.Threading.EventWaitHandle(
    $false,
    [System.Threading.EventResetMode]::ManualReset,
    $eventName
)
$powershellExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$holderArguments = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', ('"{0}"' -f $PSCommandPath),
    '-HoldLock',
    '-HoldMilliseconds', '1500',
    '-ReadyEventName', ('"{0}"' -f $eventName)
)
$holder = Start-Process `
    -FilePath $powershellExe `
    -ArgumentList $holderArguments `
    -WindowStyle Hidden `
    -PassThru

try {
    if (-not $ready.WaitOne([TimeSpan]::FromSeconds(10))) {
        throw 'timed out waiting for the lock holder'
    }
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    & $powershellExe `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File (Join-Path $repoRoot 'orchestrator\run.ps1') `
        -Mode weekly-garden `
        -DryRun
    $code = $LASTEXITCODE
    $watch.Stop()
    if ($code -ne 0) { throw "weekly lock-wait dry-run failed: $code" }
    if ($watch.Elapsed.TotalSeconds -lt 1.0) {
        throw 'weekly workflow did not wait for the occupied global lock'
    }
    if (-not $holder.WaitForExit(5000)) {
        throw 'test lock holder did not exit'
    }
    if ($holder.ExitCode -ne 0) {
        throw "test lock holder failed: $($holder.ExitCode)"
    }
}
finally {
    $ready.Dispose()
    if (-not $holder.HasExited) {
        $holder.Kill()
        [void]$holder.WaitForExit(5000)
    }
    $holder.Dispose()
}

Write-Host 'weekly lock-wait validation passed'
