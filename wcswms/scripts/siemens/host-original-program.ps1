param([Parameter(Mandatory=$true)][string]$CardDirectory,[string]$Name='WCSWMS_Original_0922')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$card=(Resolve-Path -LiteralPath $CardDirectory).Path
$boundary=[IO.Path]::GetFullPath((Join-Path $root 'data/siemens/cards')).TrimEnd('\')+'\'
if(-not $card.StartsWith($boundary,[StringComparison]::OrdinalIgnoreCase)){throw 'Only workspace simulation cards allowed'}
[xml]$report=Get-Content -LiteralPath (Join-Path $card 'card-report.xml') -Raw
if($report.SelectNodes('//Download[@errors="0"]').Count -ne 1 -or $report.SelectNodes('//Error').Count -gt 0){throw 'Card download report did not pass'}
[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM)) | Out-Null
if([string][Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::NetworkMode -ne 'Softbus'){throw 'Local Softbus required'}
$instance=[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::RegisterInstance([Siemens.Simatic.Simulation.Runtime.ECPUType]::CPU1511,$Name)
$instance.StoragePath=Join-Path $card 'storage'
$status=[ordered]@{name=$Name;host_pid=$PID;card_directory=$card;original_program_executed=$false;state='Off'}
$statusPath=Join-Path $root 'data/siemens/original-status.json'
function Save-Status { $status.updated_at=(Get-Date).ToString('o'); $status | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath ($statusPath+'.tmp') -Encoding UTF8; Move-Item -LiteralPath ($statusPath+'.tmp') -Destination $statusPath -Force }
try {
    Save-Status
    $instance.PowerOn(60000)
    $status.power_on='OK';$status.state=[string]$instance.OperatingState;Save-Status
    $instance.UpdateTagList()
    $instance.TagInfos | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $root 'data/siemens/original-tags.json') -Encoding UTF8
    $status.tag_count=$instance.TagInfos.Count;Save-Status
    $instance.Run(30000)
    $status.original_program_executed=([string]$instance.OperatingState -eq 'Run')
    while($true){
        $status.state=[string]$instance.OperatingState
        $status.license=[string]$instance.LicenseStatus
        $status.controller_name=$instance.ControllerName
        Save-Status
        Start-Sleep -Seconds 1
    }
} catch { $status.error=$_.Exception.ToString();Save-Status;throw }
finally { $instance.Dispose() }
