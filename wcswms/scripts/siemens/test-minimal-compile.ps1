$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$api=Resolve-SiemensApi TIA
[Reflection.Assembly]::LoadFrom($api) | Out-Null
Add-Type -Path (Join-Path $PSScriptRoot 'MinimalCompile.cs') -ReferencedAssemblies @($api,'System.dll','System.Core.dll','System.Xml.dll','System.Xml.Linq.dll')
$output=Join-Path $root ('data/siemens/minimal-tests/'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
Write-Output ('REPORT '+$output)
[MinimalCompile]::Run($output)
[xml]$report=Get-Content -LiteralPath (Join-Path $output 'minimal-compile-report.xml') -Raw
if($report.SelectNodes('//Compile').Count -eq 0 -or $report.SelectNodes('//Error | //Compile[number(@errors)>0]').Count -gt 0){throw 'Minimal compile failed; see report.'}
Write-Output 'MINIMAL COMPILE PASSED'
