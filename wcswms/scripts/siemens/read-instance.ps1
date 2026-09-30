param([string]$Name='WCSWMS_Local_S71500')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM)) | Out-Null
$i=[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::CreateInterface($Name)
[pscustomobject]@{Name=$i.Name;State=[string]$i.OperatingState;License=[string]$i.LicenseStatus;Storage=$i.StoragePath} | ConvertTo-Json
