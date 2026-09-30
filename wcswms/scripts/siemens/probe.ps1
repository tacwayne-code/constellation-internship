$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$out=Join-Path $root 'data/siemens'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
Write-Output ('USER '+$identity.Name)
$principal=New-Object Security.Principal.WindowsPrincipal($identity)
Write-Output ('OPENNESS_GROUP '+$principal.IsInRole('Siemens TIA Openness'))
$engineering=(Resolve-SiemensApi TIA)
$api=[Reflection.Assembly]::LoadFrom($engineering)
Write-Output ('TIA_API '+$api.FullName)
try {
  $processes=[Siemens.Engineering.TiaPortal]::GetProcesses()
  $processes | Select-Object Id,Path,ProjectPath,Mode | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $out 'tia-processes.json') -Encoding utf8
  Write-Output ('TIA_PROCESSES '+$processes.Count)
} catch { Write-Output ('TIA_PROBE_ERROR '+$_.Exception.ToString()) }
$sim=[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM))
Write-Output ('SIM_API '+$sim.FullName)
$types=$sim.GetExportedTypes()
$types | Where-Object {$_.Name -match 'RuntimeManager|^SimulationRuntime|^IInstance$|^ECPU'} | ForEach-Object {
  Write-Output ('TYPE '+$_.FullName)
  $_.GetMembers() | Where-Object {$_.MemberType -in @('Method','Property')} | ForEach-Object {$_.ToString()}
} | Set-Content -LiteralPath (Join-Path $out 'sim-api-reflection.txt') -Encoding utf8
