param([switch]$SkipTests)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$required = @(
    'config\automation.psd1',
    'orchestrator\run.ps1',
    'pyproject.toml',
    'uv.lock',
    'packages\game-automation-core\pyproject.toml',
    'apps\starrail\pyproject.toml',
    'apps\wuwa\pyproject.toml'
)
foreach ($relativePath in $required) {
    $path = Join-Path $repoRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "required file missing: $path"
    }
}

$parseErrors = @()
Get-ChildItem -LiteralPath (Join-Path $repoRoot 'orchestrator'), (Join-Path $repoRoot 'scripts') -Filter '*.ps1' -File -Recurse | ForEach-Object {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors)
    $parseErrors += $errors
}
if ($parseErrors.Count -gt 0) {
    throw ($parseErrors | Out-String)
}

if (-not $SkipTests) {
    Push-Location $repoRoot
    try {
        & uv sync --all-packages --all-groups --locked
        if ($LASTEXITCODE -ne 0) { throw 'workspace dependency validation failed' }
        $pytestRoot = Join-Path $repoRoot 'runtime\pytest'
        New-Item -ItemType Directory -Force -Path $pytestRoot | Out-Null
        $pytestTemp = Join-Path $pytestRoot ([Guid]::NewGuid().ToString())
        & uv run pytest -q --basetemp $pytestTemp packages/game-automation-core/tests apps/starrail/tests apps/wuwa/tests
        if ($LASTEXITCODE -ne 0) { throw 'workspace tests failed' }
    }
    finally {
        Pop-Location
    }
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'orchestrator\run.ps1') -Mode validate
if ($LASTEXITCODE -ne 0) { throw "orchestrator health validation failed: $LASTEXITCODE" }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'orchestrator\run.ps1') -Mode daily-chain -DryRun
if ($LASTEXITCODE -ne 0) { throw "daily-chain dry-run failed: $LASTEXITCODE" }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'orchestrator\run.ps1') -Mode integration-smoke -DryRun
if ($LASTEXITCODE -ne 0) { throw "integration-smoke contract failed: $LASTEXITCODE" }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'scripts\test_weekly_lock_wait.ps1')
if ($LASTEXITCODE -ne 0) { throw "weekly lock-wait validation failed: $LASTEXITCODE" }

Write-Host 'monorepo validation passed'
