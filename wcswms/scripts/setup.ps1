param([string]$PythonExe = '', [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'toolchain.ps1')
$root = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $root
try {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        if (-not $PythonExe) {
            $bundled = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
            if (Test-Path -LiteralPath $bundled) { $PythonExe = $bundled }
            elseif (Get-Command python -ErrorAction SilentlyContinue) { $PythonExe = (Get-Command python).Source }
            else { throw 'Python 3.12+ required. Pass -PythonExe with the installed executable path.' }
        }
        & $PythonExe -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python environment' }
    }
    if (-not $SkipInstall) {
        & '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
        Push-Location web
        try {
            & pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
        } finally { Pop-Location }
    }
    Push-Location web
    try {
        & node '.\node_modules\vite\bin\vite.js' build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
    & '.\.venv\Scripts\python.exe' scripts/doctor.py
    if ($LASTEXITCODE -ne 0) { throw 'Environment self-check failed' }
} finally { Pop-Location }

