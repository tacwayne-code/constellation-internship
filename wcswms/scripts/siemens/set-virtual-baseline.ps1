param([switch]$Restore,[switch]$Acknowledge)
$ErrorActionPreference='Stop'
. "$PSScriptRoot/paths.ps1"
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$profile=Get-Content (Join-Path $root 'config/native-virtual.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($profile.simulation_only -ne $true -or $profile.instance -ne 'WCSWMS_Original_0922' -or $profile.network -ne 'Softbus'){throw 'Invalid virtual profile'}
$backup=Join-Path $root 'data/siemens/virtual-baseline-backup.json'
$report=Join-Path $root 'data/siemens/virtual-baseline-result.json'
[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM)) | Out-Null
if([string][Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::NetworkMode -ne 'Softbus'){throw 'Softbus required'}
$instance=[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::CreateInterface($profile.instance)
function Read-Value($entry){switch($entry.type){'Float' {$instance.ReadFloat($entry.symbol)} 'Bool' {$instance.ReadBool($entry.symbol)} 'Int32' {$instance.ReadInt32($entry.symbol)} default {throw 'Unsupported type'}}}
function Write-Value($entry){switch($entry.type){'Float' {$instance.WriteFloat($entry.symbol,[single]$entry.value)} 'Bool' {$instance.WriteBool($entry.symbol,[bool]$entry.value)} 'Int32' {$instance.WriteInt32($entry.symbol,[int]$entry.value)} default {throw 'Unsupported type'}}}
try {
 $instance.UpdateTagList()
 $entries=@($profile.entries)
 foreach($entry in $entries){
  if($entry.symbol -notmatch '^HMI保持\.(行走轴|升降轴|货叉轴)参数\.(工作速度|加速度|减速度|行程最大值|行走最小值|行程最小值|提升高度|提升速度)$|^(急停按钮|行走轴前限位开关|行走轴后限位开关|Tag_2)$'){throw 'Symbol outside baseline allowlist'}
  $null=Read-Value $entry
 }
 if($Restore){
  $entries=@(Get-Content $backup -Raw -Encoding UTF8 | ConvertFrom-Json)
  foreach($entry in $entries){if(-not ($profile.entries | Where-Object {$_.symbol -eq $entry.symbol -and $_.type -eq $entry.type})){throw 'Backup contains unexpected symbol'}}
 } elseif(-not (Test-Path $backup)) {
  @($entries | ForEach-Object {[ordered]@{symbol=$_.symbol;type=$_.type;value=(Read-Value $_)}}) | ConvertTo-Json -Depth 5 | Set-Content $backup -Encoding UTF8
 }
 $rows=@(foreach($entry in $entries){Write-Value $entry; $actual=Read-Value $entry; [ordered]@{symbol=$entry.symbol;expected=$entry.value;actual=$actual;matched=($actual -eq $entry.value)}})
 [ordered]@{instance=$profile.instance;simulation_only=$true;restore=[bool]$Restore;time=(Get-Date).ToString('o');results=$rows} | ConvertTo-Json -Depth 6 | Set-Content $report -Encoding UTF8
 if(@($rows | Where-Object {-not $_.matched}).Count){throw 'Baseline readback mismatch; inspect result and use -Restore'}
 if($Acknowledge -and -not $Restore){
  $reset='HMI不保持.故障确认'
  if($instance.ReadBool($reset)){throw 'Fault confirmation already active'}
  try {$instance.WriteBool($reset,$true); Start-Sleep -Milliseconds 300} finally {$instance.WriteBool($reset,$false)}
  Start-Sleep -Seconds 3
  [ordered]@{time=(Get-Date).ToString('o');operation='HMI fault acknowledgement pulse';alarm=$instance.ReadInt16('交互.PLC-上位机.异常代码');status=$instance.ReadInt16('交互.PLC-上位机.状态');estop=$instance.ReadBool('急停按下');front=$instance.ReadBool('行走超前限');rear=$instance.ReadBool('行走超后限')} | ConvertTo-Json | Set-Content (Join-Path $root 'data/siemens/virtual-ack-result.json') -Encoding UTF8
 }
 $rows | ConvertTo-Json -Depth 4
} finally {$instance.Dispose()}
