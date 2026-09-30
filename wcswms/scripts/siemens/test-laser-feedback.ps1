param([string]$Name='WCSWMS_Original_0922')
$ErrorActionPreference='Stop'
. "$PSScriptRoot/paths.ps1"
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM)) | Out-Null
if([string][Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::NetworkMode -ne 'Softbus'){throw 'Local Softbus required'}
$instance=[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::CreateInterface($Name)
$instance.UpdateTagList()
$original=$instance.ReadInt32('Tag_2')
$results=@()
try {
    if([string]$instance.OperatingState -ne 'Run'){throw 'Original CPU must be RUN'}
    foreach($value in @(0,1000,2000,0)) {
        $instance.WriteInt32('Tag_2',[int]$value)
        Start-Sleep -Milliseconds 800
        $results += [ordered]@{input_written=$value;input_read=$instance.ReadInt32('Tag_2');position=$instance.ReadFloat('HMI不保持.激光位置');alarm_bit=$instance.ReadBool('Tag_13');alarm_code=$instance.ReadInt16('交互.PLC-上位机.异常代码');time=(Get-Date).ToString('o')}
    }
} finally {
    $instance.WriteInt32('Tag_2',$original)
    $instance.Dispose()
    [ordered]@{instance=$Name;input_symbol='Tag_2';address='%ID58';original_input=$original;restored=$true;note='Synthetic sensor values, not calibrated distance; no task command';samples=$results} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $root 'data/siemens/laser-feedback-test.json') -Encoding UTF8
}
$results | ConvertTo-Json -Depth 5
