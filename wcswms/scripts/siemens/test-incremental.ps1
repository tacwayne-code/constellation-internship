param([string]$SourceDirectory='data/siemens/exports/20260921-102108')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$api=Resolve-SiemensApi TIA
[Reflection.Assembly]::LoadFrom($api) | Out-Null
Add-Type -Path (Join-Path $PSScriptRoot 'IncrementalCompile.cs') -ReferencedAssemblies @($api,'System.dll','System.Core.dll','System.Xml.dll','System.Xml.Linq.dll')
$output=Join-Path $root ('data/siemens/incremental-tests/'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
Write-Output ('REPORT '+$output)
[IncrementalCompile]::Run($output,(Join-Path $root $SourceDirectory))
Write-Output 'Diagnostic stages finished. Review report: compile errors and failed imports do not mean the original project is validated.'
