$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$loadedApi=[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM))
$result=[ordered]@{checked_at=(Get-Date).ToString('o');api_version=$loadedApi.GetName().Version.ToString();original_program_executed=$false}
try {
 $result.initialized=[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::IsInitialized
 $result.available=[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::IsRuntimeManagerAvailable
 $result.network_mode=[string][Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::NetworkMode
 $result.instances=@([Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::RegisteredInstanceInfo | Select-Object *)
} catch {$result.error=$_.Exception.ToString()}
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'data/siemens/plcsim-status.json') -Encoding utf8
$result | ConvertTo-Json -Depth 5
