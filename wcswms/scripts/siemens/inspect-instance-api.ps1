[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM)) | Out-Null
[Enum]::GetNames([Siemens.Simatic.Simulation.Runtime.ECPUType])
[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::DefaultStoragePath
[Siemens.Simatic.Simulation.Runtime.IInstance].GetMethods() | Where-Object {$_.Name -match 'Storage|PowerOn|License|Name|Dispose|Status|State'} | ForEach-Object {$_.ToString()}
