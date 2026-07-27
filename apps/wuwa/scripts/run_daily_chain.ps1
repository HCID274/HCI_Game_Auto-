param(
    [ValidateSet('auto', 'daily-chain', 'farm-echo', 'weekly-garden')]
    [string]$Mode = 'auto',
    [switch]$DryRun
)

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$runner = Join-Path $repoRoot 'orchestrator\run.ps1'
$arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner, '-Mode', $Mode)
if ($DryRun) { $arguments += '-DryRun' }
& powershell.exe @arguments
exit $LASTEXITCODE
