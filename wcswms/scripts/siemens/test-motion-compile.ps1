$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$api=Resolve-SiemensApi TIA
[Reflection.Assembly]::LoadFrom($api) | Out-Null
Add-Type -Path (Join-Path $PSScriptRoot 'MotionCompile.cs') -ReferencedAssemblies @($api,'System.dll','System.Core.dll','System.Xml.dll','System.Xml.Linq.dll')
$output=Join-Path $root ('data/siemens/motion-tests/'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
Write-Output ('REPORT '+$output)
[MotionCompile]::Run($output,(Join-Path $root 'data/siemens/exports/20260921-103552'))
[xml]$report=Get-Content -LiteralPath (Join-Path $output 'motion-report.xml') -Raw
if($report.SelectNodes('//Compile').Count -eq 0 -or $report.SelectNodes('//Error | //Compile[number(@errors)>0]').Count -gt 0){throw 'Motion compile failed; see report.'}
Write-Output 'MOTION COMPILE PASSED'
