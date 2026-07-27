$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
& (Join-Path $repoRoot 'scripts\install.ps1')
exit $LASTEXITCODE

