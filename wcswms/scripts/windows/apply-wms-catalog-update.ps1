$ErrorActionPreference = 'Stop'
$wmsProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
Set-Location -LiteralPath $wmsProjectRoot
$wmsConfig = Get-Content -LiteralPath (Join-Path $wmsProjectRoot 'config\wms.json') -Raw | ConvertFrom-Json
$wmsListener = Get-NetTCPConnection -State Listen -LocalPort $wmsConfig.web_port -ErrorAction SilentlyContinue
if ($wmsListener) {
    throw "WMS is still running on port $($wmsConfig.web_port). Close the existing WMS backend first. This script does not stop processes."
}
$wmsUpdateRoot = Join-Path $wmsProjectRoot 'release\wms-catalog-update'
$wmsDistRoot = Join-Path $wmsProjectRoot 'web\dist'
$wmsPython = Join-Path $wmsProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath (Join-Path $wmsUpdateRoot 'wms.html'))) {
    throw 'Prepared WMS material-catalog update is missing.'
}
@'
from datetime import datetime
from pathlib import Path
import sqlite3
source = Path('data/wms/warehouse.sqlite3')
if source.exists():
    backup = source.parent/'backups'/f'warehouse-before-catalog-apply-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3'
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
        assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    print(f'Ledger backup: {backup}')
'@ | & $wmsPython -
if ($LASTEXITCODE -ne 0) { throw 'Ledger backup failed. Update was not applied.' }
New-Item -ItemType Directory -Path (Join-Path $wmsDistRoot 'static') -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $wmsUpdateRoot 'static') -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $wmsDistRoot 'static') -Force
}
Copy-Item -LiteralPath (Join-Path $wmsUpdateRoot 'wms.html') -Destination (Join-Path $wmsDistRoot 'wms.html') -Force
Write-Host "Material catalog update applied. Starting WMS on http://$($wmsConfig.listen_host):$($wmsConfig.web_port)/"
& $wmsPython (Join-Path $wmsProjectRoot 'run_wms.py')
exit $LASTEXITCODE
