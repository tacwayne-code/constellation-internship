param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$releaseRoot = Join-Path $projectRoot 'release\wms-device-ip-update'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$launchPath = Join-Path $projectRoot 'run_wms.py'
$config = Get-Content -LiteralPath (Join-Path $projectRoot 'config\wms.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$baseUrl = 'http://{0}:{1}' -f $config.listen_host, $config.web_port

foreach ($relative in @('backend\wms_allocation.py','backend\wms_orders.py','backend\wms_store.py','backend\wms_service.py','backend\wms_app.py','backend\wms_device.py','run_wms.py','web\dist\wms.html')) {
    if (-not (Test-Path -LiteralPath (Join-Path $releaseRoot $relative))) { throw "Missing update file: $relative" }
}
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "Python not found: $pythonPath" }
$html = Get-Content -LiteralPath (Join-Path $releaseRoot 'web\dist\wms.html') -Raw -Encoding UTF8
$assets = [regex]::Matches($html, '(?:src|href)="(/static/[^"/]+)"') | ForEach-Object { $_.Groups[1].Value }
if (@($assets).Count -ne 3) { throw 'Unexpected frontend asset manifest' }
foreach ($asset in $assets) {
    if (-not (Test-Path -LiteralPath (Join-Path $releaseRoot ('web\dist' + $asset)))) { throw "Missing asset: $asset" }
}

$manifest = Get-Content -LiteralPath (Join-Path $releaseRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.version -ne '1.6.0') { throw 'Unexpected release version' }
foreach ($entry in $manifest.files.PSObject.Properties) {
    $relative = $entry.Name
    $resolved = [IO.Path]::GetFullPath((Join-Path $releaseRoot $relative))
    if (-not $resolved.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid release path' }
    if ((Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash -ne $entry.Value) { throw "Update file checksum mismatch: $relative" }
}

$listeners = @(Get-NetTCPConnection -LocalPort $config.web_port -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 1) { throw 'Multiple listeners found; update stopped.' }
$servicePid = $null
if ($listeners.Count -eq 1) {
    $servicePid = $listeners[0].OwningProcess
    $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$servicePid"
    if ($serviceProcess.Name -ne 'python.exe' -or $serviceProcess.CommandLine -notmatch [regex]::Escape($launchPath)) {
        throw 'Port belongs to another program. No process was stopped.'
    }
    $state = Invoke-RestMethod "$baseUrl/api/state" -TimeoutSec 10
    if (-not $state.plc.live -or -not $state.readiness.ready) { throw 'PLC is not online and idle. Finish the current operation first.' }
    if (@($state.tasks | Where-Object { $_.status -notin @('COMPLETED','CANCELLED','QUEUED') }).Count -gt 0) { throw 'There are running or handover tasks. Finish them before updating; queued tasks may remain.' }
    if (@($state.stock | Where-Object { $_.status -in @('AT_STATION','AT_EXIT') }).Count -gt 0) { throw 'Station is occupied. Finish the current box first.' }
}
if ($CheckOnly) { Write-Host "WMS 1.6.0 files, checksums and service preflight passed: $baseUrl"; return }

Write-Host 'Do not submit warehouse tasks from any device until this window reports completion.'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupPath = Join-Path $projectRoot "data\wms\backups\device-ip-deploy-$stamp"
New-Item -ItemType Directory -Path $backupPath | Out-Null
$backupProgram = @'
from pathlib import Path
import json,sqlite3,sys
root,backup=map(Path,sys.argv[1:])
with sqlite3.connect((root/'data/wms/warehouse.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    state=json.loads(db.execute('select payload from ledger where id=1').fetchone()[0])
    assert not [t for t in state['tasks'] if t['status'] not in {'COMPLETED','CANCELLED','QUEUED'}], 'Running or handover tasks: update stopped'
for source,name in [(root/'data/wms/warehouse.sqlite3','warehouse.sqlite3'),(root/'data/physical-commands.sqlite3','physical-commands.sqlite3')]:
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(backup/name) as dst: src.backup(dst)
print('Database backup completed: '+str(backup))
'@
$backupProgram | & $pythonPath - $projectRoot $backupPath
if ($LASTEXITCODE -ne 0) { throw 'Database preflight or backup failed. No process was stopped.' }
Copy-Item -LiteralPath (Join-Path $projectRoot 'web\dist\wms.html') -Destination (Join-Path $backupPath 'wms.html')

if ($null -ne $servicePid) {
    # Stop only the verified WMS listener, never other Python/PLC programs.
    Stop-Process -Id $servicePid
    Wait-Process -Id $servicePid -Timeout 10 -ErrorAction SilentlyContinue
    if (Get-Process -Id $servicePid -ErrorAction SilentlyContinue) { throw 'WMS listener did not exit; update stopped.' }
}
$backupProgram | & $pythonPath - $projectRoot $backupPath
if ($LASTEXITCODE -ne 0) { throw 'Post-stop database check failed; no release files were copied. Review the backup before restarting.' }
foreach ($relative in @('backend\wms_allocation.py','backend\wms_orders.py','backend\wms_store.py','backend\wms_service.py','backend\wms_app.py','backend\wms_device.py','run_wms.py')) {
    Copy-Item -LiteralPath (Join-Path $releaseRoot $relative) -Destination (Join-Path $projectRoot $relative) -Force
}
foreach ($asset in $assets) {
    Copy-Item -LiteralPath (Join-Path $releaseRoot ('web\dist' + $asset)) -Destination (Join-Path $projectRoot ('web\dist' + $asset)) -Force
}
$nextHtml = Join-Path $projectRoot 'web\dist\wms.next.html'
Copy-Item -LiteralPath (Join-Path $releaseRoot 'web\dist\wms.html') -Destination $nextHtml -Force
Move-Item -LiteralPath $nextHtml -Destination (Join-Path $projectRoot 'web\dist\wms.html') -Force
$stdout = Join-Path $projectRoot "data\wms\server-$stamp.stdout.log"
$stderr = Join-Path $projectRoot "data\wms\server-$stamp.stderr.log"
Start-Process -FilePath $pythonPath -ArgumentList ('"' + $launchPath + '"') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
$healthy = $false
for ($attempt=0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod "$baseUrl/api/health" -TimeoutSec 2
        if ($health.version -eq '1.6.0' -and $health.features -contains 'device_connection_settings') { $healthy = $true; break }
    } catch { }
}
if (-not $healthy) { throw "WMS did not report version 1.6.0. Check $stderr. Backup: $backupPath" }
Write-Host "WMS 1.6.0 is running: $baseUrl"
Write-Host "Refresh the browser with Ctrl+F5. PLC online: $($health.plc.live)"
Write-Host "Backup: $backupPath"
Write-Host "Log: $stderr"
