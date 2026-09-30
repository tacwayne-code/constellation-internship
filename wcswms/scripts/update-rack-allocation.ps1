param([switch]$CheckOnly)
& (Join-Path $PSScriptRoot 'update-device-ip.ps1') -CheckOnly:$CheckOnly
