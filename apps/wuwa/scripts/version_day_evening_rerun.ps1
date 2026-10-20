# Do not delete this scheduled task from here: deleting a running task makes
# Windows terminate this very instance (2026-09-30 incident). The -Once trigger
# fires only once, and the next version day re-registers it with -Force.
# apps\wuwa\scripts -> repo root; the monorepo move left this one level short (found 1021).
& "$PSScriptRoot\..\..\..\orchestrator\run.ps1" -Mode wuwa-daily
exit $LASTEXITCODE
