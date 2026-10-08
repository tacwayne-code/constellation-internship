param([switch]$SkipBuild, [ValidateSet('wms', 'lab')][string]$Mode = 'wms')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'toolchain.ps1')
Push-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
try {
    & '.\.venv\Scripts\python.exe' -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Regression tests failed' }
    & node --test tests/test_wms_paging.mjs
    if ($LASTEXITCODE -ne 0) { throw 'WMS pagination regression tests failed' }
    if ($Mode -eq 'lab') {
        & '.\.venv\Scripts\python.exe' scripts/validate_twin.py
        if ($LASTEXITCODE -ne 0) { throw 'CAD mapping validation failed; lab mode requires the engineering CAD extraction data.' }
    }
    if (-not $SkipBuild) {
        Push-Location web
        try {
            & node '.\node_modules\vite\bin\vite.js' build
            if ($LASTEXITCODE -ne 0) { throw 'Production build failed' }
        } finally { Pop-Location }
    }
    $doctorScript = if ($Mode -eq 'wms') { 'scripts/doctor-wms.py' } else { 'scripts/doctor.py' }
    & '.\.venv\Scripts\python.exe' $doctorScript
    if ($LASTEXITCODE -ne 0) { throw 'Environment self-check failed' }
} finally { Pop-Location }


