$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'paths.ps1')
$root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$api=Resolve-SiemensApi TIA
[Reflection.Assembly]::LoadFrom($api) | Out-Null
Add-Type -Path (Join-Path $PSScriptRoot 'BuildSimulationCard.cs') -ReferencedAssemblies @($api,'System.dll','System.Core.dll','System.Xml.dll','System.Xml.Linq.dll')
$output=Join-Path $root ('data/siemens/cards/'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
Write-Output ('OUTPUT '+$output)
[BuildSimulationCard]::Run($root,$output)
Write-Output 'SIMULATION CARD CREATED'
