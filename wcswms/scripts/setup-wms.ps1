param([string]$PythonExe = '', [string]$ListenHost = '', [string]$PlcHost = '', [int]$WebPort = 8770, [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'toolchain.ps1')
$env:PYTHONUTF8 = '1'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        if (-not $PythonExe) {
            $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
            if (Test-Path -LiteralPath $bundledPython) { $PythonExe = $bundledPython }
            if (-not $PythonExe -and (Get-Command py -ErrorAction SilentlyContinue)) {
                $candidate = & py -3.12 -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0) { $PythonExe = $candidate | Select-Object -Last 1 }
            }
            if (-not $PythonExe -and (Get-Command python -ErrorAction SilentlyContinue)) { $PythonExe = (Get-Command python).Source }
        }
        if (-not $PythonExe) { throw 'Install Python 3.12 x64 first, or pass -PythonExe with its executable path.' }
        & $PythonExe -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12+ required'; assert sys.maxsize > 2**32, '64-bit Python required'"
        if ($LASTEXITCODE -ne 0) { throw 'Python version or architecture check failed.' }
        & $PythonExe -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
    }
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not $SkipInstall) {
        & $pythonPath -m pip install -r requirements.lock.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check internet/proxy configuration, then run this script again.' }
    }
    & $pythonPath -c "import fastapi, uvicorn, snap7; from pathlib import Path; assert Path('web/dist/wms.html').is_file(), 'Prebuilt WMS frontend is missing'"
    if ($LASTEXITCODE -ne 0) { throw 'Runtime or prebuilt frontend check failed.' }
    $existing = $null
    if (Test-Path -LiteralPath 'config\wms.json') { $existing = Get-Content -LiteralPath 'config\wms.json' -Raw -Encoding UTF8 | ConvertFrom-Json }
    if (-not $ListenHost) {
        if ($existing) { $ListenHost = $existing.listen_host }
        else {
            Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'} | Format-Table InterfaceAlias,IPAddress
            $ListenHost = Read-Host 'Enter THIS computer company-LAN IPv4 address (example: 192.168.1.45)'
        }
    }
    if (-not $PlcHost) {
        if ($existing) { $PlcHost = $existing.host }
        else { $PlcHost = Read-Host 'Enter PLC IPv4 address [192.168.0.100]'; if (-not $PlcHost) { $PlcHost = '192.168.0.100' } }
    }
    & $pythonPath scripts/initialize-deployment.py --listen-host $ListenHost --plc-host $PlcHost --web-port $WebPort
    if ($LASTEXITCODE -ne 0) { throw 'Local configuration initialization failed.' }
    Write-Host 'Installation ready. Start run_wms.py with .venv\Scripts\python.exe, or double-click the WMS startup launcher.'
    Write-Host 'On first startup a NEW control key is generated in data\physical-control-key.txt.'
    Write-Host 'LAN firewall steps and data migration are described in DEPLOY-LAN.md.'
} finally { Pop-Location }
