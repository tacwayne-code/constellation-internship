$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi TIA)) | Out-Null
try {
 $tia=New-Object Siemens.Engineering.TiaPortal([Siemens.Engineering.TiaPortalMode]::WithoutUserInterface)
 Write-Output ('TIA_CREATED '+$tia.GetCurrentProcess().Id)
 $tia.Dispose()
} catch { Write-Output ('TIA_ERROR '+$_.Exception.ToString()) }
[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM)) | Out-Null
[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager].GetMembers() | ForEach-Object {$_.ToString()}
