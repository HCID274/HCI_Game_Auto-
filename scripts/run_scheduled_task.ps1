param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("main", "universe", "cleanup")]
    [string]$Task,

    [Parameter(Mandatory = $false)]
    [int]$Timeout
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$uv = Get-Command uv -ErrorAction Stop

Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] running scheduled task: $Task"
Write-Host "project root: $projectRoot"
Write-Host "uv path: $($uv.Source)"

if ($Task -eq "cleanup") {
    & $uv.Source run starrail-auto cleanup
} elseif ($Task -eq "main") {
    & $uv.Source run starrail-auto daily --timeout $Timeout
} else {
    & $uv.Source run starrail-auto universe --timeout $Timeout
}
exit $LASTEXITCODE
