. "$PSScriptRoot/paths.ps1"
[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM)) | Out-Null
[Siemens.Simatic.Simulation.Runtime.IInstance].GetMethods() | Where-Object {$_.Name -match 'Tag|ReadInt16|ReadBool|WriteInt16|Run|ReadFloat|Cycle|Scan'} | ForEach-Object {$_.ToString()}
[Siemens.Simatic.Simulation.Runtime.IInstance].GetProperties() | ForEach-Object {$_.ToString()}
