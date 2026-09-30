param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'toolchain.ps1')
Push-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
try {
    & '.\.venv\Scripts\python.exe' -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Regression tests failed' }
    & '.\.venv\Scripts\python.exe' scripts/validate_twin.py
    if ($LASTEXITCODE -ne 0) { throw 'CAD mapping validation failed' }
    if (-not $SkipBuild) {
        Push-Location web
        try {
            & node '.\node_modules\vite\bin\vite.js' build
            if ($LASTEXITCODE -ne 0) { throw 'Production build failed' }
        } finally { Pop-Location }
    }
    & '.\.venv\Scripts\python.exe' scripts/doctor.py
    if ($LASTEXITCODE -ne 0) { throw 'Environment self-check failed' }
} finally { Pop-Location }


