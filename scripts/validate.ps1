param([switch]$SkipTests)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$required = @(
    'config\automation.psd1',
    'orchestrator\run.ps1',
    'apps\starrail\pyproject.toml',
    'apps\starrail\uv.lock',
    'apps\wuwa\pyproject.toml',
    'apps\wuwa\uv.lock'
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
    $projects = @(
        @{ Name = 'Star Rail'; Path = (Join-Path $repoRoot 'apps\starrail') },
        @{ Name = 'Wuthering Waves'; Path = (Join-Path $repoRoot 'apps\wuwa') }
    )
    foreach ($project in $projects) {
        Push-Location $project.Path
        try {
            & uv sync --locked
            if ($LASTEXITCODE -ne 0) { throw "$($project.Name) dependency validation failed" }
            $pytestRoot = Join-Path $project.Path 'runtime\pytest'
            New-Item -ItemType Directory -Force -Path $pytestRoot | Out-Null
            $pytestTemp = Join-Path $pytestRoot ([Guid]::NewGuid().ToString())
            & uv run pytest -q --basetemp $pytestTemp
            if ($LASTEXITCODE -ne 0) { throw "$($project.Name) tests failed" }
        }
        finally {
            Pop-Location
        }
    }
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'orchestrator\run.ps1') -Mode validate
if ($LASTEXITCODE -ne 0) { throw "orchestrator health validation failed: $LASTEXITCODE" }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'orchestrator\run.ps1') -Mode daily-chain -DryRun
if ($LASTEXITCODE -ne 0) { throw "daily-chain dry-run failed: $LASTEXITCODE" }

Write-Host 'monorepo validation passed'
