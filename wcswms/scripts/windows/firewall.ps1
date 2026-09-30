param([switch]$Apply)
$ErrorActionPreference='Stop'
try {
    $root=Split-Path $PSScriptRoot -Parent
    if (-not $Apply) {
        $process=Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-Apply')
        exit $process.ExitCode
    }
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw '需要管理员授权。' }
    $config=Get-Content -LiteralPath (Join-Path $root 'config/plc.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $nic=@(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -eq $config.listen_host })
    if ($nic.Count -ne 1) { throw '公司局域网 IP 不属于此主机，未修改防火墙。' }
    $octets=([Net.IPAddress]::Parse($config.listen_host)).GetAddressBytes()
    $bits=[int]$nic[0].PrefixLength
    $network=New-Object byte[] 4
    for ($i=0; $i -lt 4; $i++) {
        $count=[Math]::Min(8,[Math]::Max(0,$bits-8*$i))
        $mask=if ($count -eq 0) {0} else {256-[Math]::Pow(2,8-$count)}
        $network[$i]=$octets[$i] -band [int]$mask
    }
    $remote=($network -join '.') + '/' + $bits
    $exe=Join-Path $root 'WcsPlcConsole.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw '缺少后台程序。' }
    $sha=[Security.Cryptography.SHA256]::Create()
    try { $id=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant())))).Replace('-','').Substring(0,12) } finally { $sha.Dispose() }
    $name='WcsPlcCommissioning-' + $id
    $port=if ($config.PSObject.Properties['web_port']) {[int]$config.web_port} else {8765}
    Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -Name $name -DisplayName 'WCS physical PLC commissioning (company LAN)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -LocalAddress $config.listen_host -RemoteAddress $remote -Program $exe -Profile Any | Out-Null
    exit 0
} catch {
    $root=Split-Path $PSScriptRoot -Parent
    $log=Join-Path $root 'firewall-setup-error.txt'
    $_.Exception.Message | Set-Content -LiteralPath $log -Encoding UTF8
    exit 1
}
