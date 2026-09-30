param([string]$Name='WCSWMS_Original_0922')
$ErrorActionPreference='Stop'
. "$PSScriptRoot/paths.ps1"
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM)) | Out-Null
if([string][Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::NetworkMode -ne 'Softbus'){throw 'Local Softbus required'}
$instance=[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::CreateInterface($Name)
try {
 $instance.UpdateTagList()
 $rows=@(foreach($tag in $instance.TagInfos){
  if($tag.Name -notmatch '报警|超前限|超后限|超上限|超下限|急停|限位|激光位置|行程|行走最小值|数据交互状态|PLC-上位机|轴参数'){continue}
  if($tag.Dimension -or [int]$tag.PrimitiveDataType -notin @(2,4,11)){continue}
  try {
   $v=switch([int]$tag.PrimitiveDataType){2 {$instance.ReadBool($tag.Name)} 4 {$instance.ReadInt16($tag.Name)} 11 {$instance.ReadFloat($tag.Name)}}
   [ordered]@{name=$tag.Name;value=$v;error=$null}
  } catch {[ordered]@{name=$tag.Name;value=$null;error=$_.Exception.Message}}
 })
 $result=[ordered]@{instance=$Name;state=[string]$instance.OperatingState;license=[string]$instance.LicenseStatus;time=(Get-Date).ToString('o');read_only=$true;signals=$rows}
 $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $root 'data/siemens/interlock-diagnostics.json') -Encoding UTF8
 $rows | ConvertTo-Json -Depth 5
} finally {$instance.Dispose()}
