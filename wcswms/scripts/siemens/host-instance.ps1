param([string]$Name='WCSWMS_RepairVerified_0922')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'setup-instance.ps1') -Name $Name
$statusPath=Join-Path $root 'data/siemens/host-status.json'
try {
    while($true) {
        [ordered]@{name=$Name;host_pid=$PID;updated_at=(Get-Date).ToString('o');state=[string]$instance.OperatingState;license=[string]$instance.LicenseStatus;storage=$instance.StoragePath;original_program_executed=$false} |
            ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding utf8
        Start-Sleep -Seconds 5
    }
} finally { $instance.Dispose() }
