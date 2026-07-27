$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
& (Join-Path $repoRoot 'scripts\register_tasks.ps1')
exit $LASTEXITCODE

