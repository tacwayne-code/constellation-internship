param([string]$Name='WCSWMS_Original_0922')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
[Reflection.Assembly]::LoadFrom((Resolve-SiemensApi PLCSIM)) | Out-Null
if([string][Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::NetworkMode -ne 'Softbus'){throw 'Local Softbus required'}
$instance=[Siemens.Simatic.Simulation.Runtime.SimulationRuntimeManager]::CreateInterface($Name)
$instance.UpdateTagList()
$tags=@($instance.TagInfos | Where-Object {(-not $_.Dimension) -and (($_.Name.StartsWith('交互.') -and [int]$_.PrimitiveDataType -eq 4) -or ($_.Name -match '^HMI不保持.激光|^HMI保持.*轴参数\.' -and [int]$_.PrimitiveDataType -eq 11) -or (($_.Name -match '^急停按下$|^[行走升降货叉]+超[前后上下左右]限$' -or [int]$_.Area -eq 1) -and [int]$_.PrimitiveDataType -eq 2))})
$output=Join-Path $root 'data/siemens/native-telemetry.json'
$sequence=0
$probe='交互.上位机-PLC.INT2'
$before=$instance.ReadInt16($probe)
$instance.WriteInt16($probe,$before)
$after=$instance.ReadInt16($probe)
$proof=[ordered]@{symbol=$probe;operation='WriteInt16 / ReadInt16';before=$before;written=$before;after=$after;passed=($before -eq $after);note='Same-value transport probe; no task command sent';time=(Get-Date).ToString('o')}
try {
    while($true){
        $rows=@(foreach($tag in $tags){
            try {
                $value=if([int]$tag.PrimitiveDataType -eq 4){$instance.ReadInt16($tag.Name)}elseif([int]$tag.PrimitiveDataType -eq 2){$instance.ReadBool($tag.Name)}else{$instance.ReadFloat($tag.Name)}
                [ordered]@{name=$tag.Name;value=$value;error=$null}
            }catch{[ordered]@{name=$tag.Name;value=$null;error=$_.Exception.Message}}
        })
        $sequence++
        [ordered]@{instance=$Name;state=[string]$instance.OperatingState;license=[string]$instance.LicenseStatus;controller_name=$instance.ControllerName;sample=$sequence;updated_at=(Get-Date).ToString('o');source='Siemens PLCSIM Advanced V7 API / original PLC_2';read_write_probe=$proof;signals=$rows} |
            ConvertTo-Json -Depth 7 | Set-Content -LiteralPath ($output+'.tmp') -Encoding UTF8
        Move-Item -LiteralPath ($output+'.tmp') -Destination $output -Force
        Start-Sleep -Milliseconds 500
    }
} finally {$instance.Dispose()}
