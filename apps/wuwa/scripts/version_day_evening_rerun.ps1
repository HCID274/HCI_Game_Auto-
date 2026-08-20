$schtasksName = 'HCID274_GameAutomation_WuwaVersionDayEvening'
schtasks /Delete /TN $schtasksName /F 2>$null | Out-Null
& "$PSScriptRoot\..\..\orchestrator\run.ps1" -Mode wuwa-daily
exit $LASTEXITCODE
