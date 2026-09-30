param([switch]$ValidateOnly,[switch]$ExportOnly,[string]$CompileBlock)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$api=(Resolve-SiemensApi TIA)
[Reflection.Assembly]::LoadFrom($api) | Out-Null
Add-Type -Path (Join-Path $PSScriptRoot 'TiaExport.cs') -ReferencedAssemblies @($api,'System.dll','System.Core.dll','System.Xml.dll','System.Xml.Linq.dll')
if($ValidateOnly){Write-Output 'Engineering helper compiled against installed TIA V20 API. Project not opened.';exit 0}
if($CompileBlock -and -not $ExportOnly){throw 'Use -ExportOnly with -CompileBlock to isolate one block from a full compile.'}
[WarehouseTiaExport]::CompileBlockName=$CompileBlock
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole('Siemens TIA Openness')) {throw 'Current Windows logon token lacks Siemens TIA Openness. Sign out and sign in after group membership is granted.'}
$working=Join-Path $root 'data/siemens/projects/v20-simulation'
$upgraded=Join-Path $root 'data/siemens/projects/v20-simulation_V20'
if(Test-Path -LiteralPath (Join-Path $upgraded 'v20-simulation_V20.ap20')) {$working=$upgraded}
$project=Get-ChildItem -LiteralPath $working -Filter '*.ap20' | Select-Object -First 1
if(-not $project){$project=Get-ChildItem -LiteralPath $working -Filter '*.ap18' | Select-Object -First 1}
if(-not $project){throw 'Run scripts/siemens/prepare-project.py first.'}
$out=Join-Path $root ('data/siemens/exports/'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
$reportPath=[WarehouseTiaExport]::Run($project.FullName,$out,$working,(-not $ExportOnly))
Write-Output $reportPath
[xml]$report=Get-Content -LiteralPath $reportPath -Raw
$errors=@($report.SelectNodes('//Error | //Compile[number(@errors) > 0]'))
$plcs=@($report.SelectNodes('/EngineeringExport/PLC'))
if($errors.Count -gt 0 -or $plcs.Count -eq 0) {
    Write-Error "Engineering validation incomplete: $($errors.Count) error entries, $($plcs.Count) PLCs. See $reportPath"
    exit 1
}
