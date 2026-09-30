$ErrorActionPreference = 'Stop'
$wmsProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
Set-Location -LiteralPath $wmsProjectRoot
$wmsConfig = Get-Content -LiteralPath (Join-Path $wmsProjectRoot 'config\wms.json') -Raw | ConvertFrom-Json
$wmsListener = Get-NetTCPConnection -State Listen -LocalPort $wmsConfig.web_port -ErrorAction SilentlyContinue
if ($wmsListener) {
    throw "WMS is still running on port $($wmsConfig.web_port). Close the existing WMS backend first. This script does not stop processes."
}
$wmsUpdateRoot = Join-Path $wmsProjectRoot 'release\wms-layout-update'
$wmsDistRoot = Join-Path $wmsProjectRoot 'web\dist'
if (-not (Test-Path -LiteralPath (Join-Path $wmsUpdateRoot 'wms.html'))) {
    throw 'Prepared WMS location-map update is missing.'
}
Get-ChildItem -LiteralPath (Join-Path $wmsUpdateRoot 'static') -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $wmsDistRoot 'static') -Force
}
Copy-Item -LiteralPath (Join-Path $wmsUpdateRoot 'wms.html') -Destination (Join-Path $wmsDistRoot 'wms.html') -Force
Write-Host "Location-map update applied. Starting WMS on http://$($wmsConfig.listen_host):$($wmsConfig.web_port)/"
& (Join-Path $wmsProjectRoot '.venv\Scripts\python.exe') (Join-Path $wmsProjectRoot 'run_wms.py')
exit $LASTEXITCODE
