function Resolve-SiemensApi([ValidateSet('TIA','PLCSIM')][string]$Kind) {
    $root=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $config=Get-Content -LiteralPath (Join-Path $root 'config/siemens.json') -Raw | ConvertFrom-Json
    if($Kind -eq 'TIA') {
        $override=$env:WCS_TIA_API
        $candidates=@($config.tia_api,'C:\Program Files\Siemens\Automation\Portal V20\PublicAPI\V20\Siemens.Engineering.dll','D:\Siemens\Automation\Portal V20\PublicAPI\V20\Siemens.Engineering.dll')
    } else {
        $override=$env:WCS_PLCSIM_API
        $candidates=@($config.plcsim_api,'C:\Program Files (x86)\Common Files\Siemens\PLCSIMADV\API\6.0\Siemens.Simatic.Simulation.Runtime.Api.x64.dll')
    }
    if($override) {
        if(-not (Test-Path -LiteralPath $override -PathType Leaf)){throw "$Kind API override does not exist: $override"}
        return $override
    }
    foreach($candidate in $candidates) {
        if($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)){return $candidate}
    }
    throw "$Kind API not found. Configure config/siemens.json or WCS_TIA_API / WCS_PLCSIM_API."
}
