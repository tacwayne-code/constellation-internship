param([string]$Name='WCSWMS_Local_S71500',[switch]$FreshStorage)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM)) | Out-Null
if ([string][Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::NetworkMode -ne 'Softbus') { throw 'This setup only operates in local Softbus mode; network settings were not changed.' }
$result=[ordered]@{name=$Name;checked_at=(Get-Date).ToString('o');network_mode='Softbus';original_program_executed=$false}
try {
 $existing=@([Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::RegisteredInstanceInfo | Where-Object {$_.InstanceName -eq $Name -or $_.Name -eq $Name})
 if($existing.Count -gt 0) { $instance=[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::CreateInterface($Name) }
 else {
  $instance=[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::RegisterInstance([Siemens.Simatic.Simulation.Runtime.ECPUType]::CPU1511,$Name)
  $storage=Join-Path $root 'data/siemens/plcsim-persistence'
  if($FreshStorage){$storage=Join-Path $root ('data/siemens/clean-probes/'+(Get-Date -Format 'yyyyMMdd-HHmmss'))}
  New-Item -ItemType Directory -Path $storage -Force | Out-Null
  $instance.StoragePath=$storage
 }
 $result.storage_path=$instance.StoragePath
 $result.cpu_type=[string]$instance.CPUType
 $result.before=[string]$instance.OperatingState
 try {$result.license_before_power_on=[string]$instance.LicenseStatus} catch {$result.license_probe_error=$_.Exception.Message}
 if($result.before -eq 'Off') { $result.power_on_result=[string]$instance.PowerOn(30000) }
 $result.operating_state=[string]$instance.OperatingState
 $result.license_status=[string]$instance.LicenseStatus
 $result.note='Dedicated blank virtual CPU. Original program has not been compiled or downloaded.'
} catch {$result.error=$_.Exception.ToString()}
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'data/siemens/instance-status.json') -Encoding utf8
$history=Join-Path $root 'data/siemens/instance-probes'
New-Item -ItemType Directory -Path $history -Force | Out-Null
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $history ((Get-Date -Format 'yyyyMMdd-HHmmss')+'.json')) -Encoding utf8
$result | ConvertTo-Json -Depth 5

if($result.error){exit 1}
