Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-WcsFullPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw '安装路径不能为空。' }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full.StartsWith('\\') -or $full.Length -lt 4 -or $full.Substring(2).Contains(':')) {
        throw '请选择本机磁盘中的独立文件夹，不能使用磁盘根目录或网络共享。'
    }
    return $full
}

function Test-WcsPayload([string]$PackageRoot) {
    $payload = [IO.Path]::GetFullPath((Join-Path $PackageRoot 'payload')).TrimEnd('\')
    $rows = Get-Content -LiteralPath (Join-Path $PackageRoot 'PACKAGE-MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $rows = @($rows)
    if ($rows.Count -lt 1) { throw '安装包清单为空。' }
    $seen = @{}
    foreach ($row in $rows) {
        $candidate = [IO.Path]::GetFullPath((Join-Path $payload $row.path))
        if (-not $candidate.StartsWith($payload + '\', [StringComparison]::OrdinalIgnoreCase) -or $seen.ContainsKey($candidate)) {
            throw '安装包清单含有不合法路径。'
        }
        $seen[$candidate] = $true
        $file = Get-Item -LiteralPath $candidate
        if ($file.Length -ne $row.size -or (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash -ne $row.sha256) {
            throw ('安装包校验失败：' + $row.path)
        }
    }
    if (@(Get-ChildItem -LiteralPath $payload -Recurse -File).Count -ne $rows.Count) {
        throw '安装包包含清单以外的文件。'
    }
    return $payload
}

function Assert-WcsIPv4([string]$Address, [string]$Label) {
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed) -or
        $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
        $parsed.ToString() -ne $Address -or $Address -eq '0.0.0.0' -or
        $Address -eq '255.255.255.255' -or $parsed.GetAddressBytes()[0] -ge 224) {
        throw ($Label + '必须是明确的 IPv4 地址。')
    }
}

function Install-WcsPackage {
    param(
        [Parameter(Mandatory=$true)][string]$PackageRoot,
        [Parameter(Mandatory=$true)][string]$TargetPath,
        [Parameter(Mandatory=$true)][string]$ListenHost,
        [string]$PlcHost = '192.168.0.100',
        [int]$WebPort = 8765,
        [int]$PlcPort = 102,
        [int]$Rack = 0,
        [int]$Slot = 1,
        [string]$HistoryPath = ''
    )
    if (-not [Environment]::Is64BitOperatingSystem) { throw '需要 64 位 Windows 10/11。' }
    $payload = Test-WcsPayload $PackageRoot
    $target = Get-WcsFullPath $TargetPath
    $sourceRoot = [IO.Path]::GetFullPath($PackageRoot).TrimEnd('\')
    if ($target -eq $sourceRoot -or $target.StartsWith($sourceRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $sourceRoot.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw '安装目录不能与解压目录重叠。'
    }
    $existing = Test-Path -LiteralPath $target
    $preserve = $false
    if ($existing) {
        $marker = Join-Path $target '.wcs-install.json'
        if (@(Get-ChildItem -LiteralPath $target -Force).Count -gt 0) {
            if (-not (Test-Path -LiteralPath $marker)) { throw '目标文件夹非空且不是本套件的安装目录，请选择新目录。' }
            $old = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($old.product -ne 'wcs-physical-commissioning') { throw '不能覆盖其他产品的安装目录。' }
            $preserve = $true
            foreach ($relative in @('data/server.lock', 'WcsPlcConsole.exe')) {
                $item = Join-Path $target $relative
                if (Test-Path -LiteralPath $item) {
                    try { $handle = [IO.File]::Open($item, 'Open', 'ReadWrite', 'None'); $handle.Dispose() }
                    catch { throw '后台仍在运行或文件不可写，请先在后台窗口按 Ctrl+C 后重试。' }
                }
            }
        }
    }
    if ($preserve) {
        if ($HistoryPath) { throw '升级会保留已有任务记录，不能同时导入另一份任务记录。' }
        $config = Get-Content -LiteralPath (Join-Path $target 'config/plc.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    } else {
        Assert-WcsIPv4 $ListenHost '公司局域网地址'
        Assert-WcsIPv4 $PlcHost 'PLC 地址'
        if ($WebPort -lt 1 -or $WebPort -gt 65535 -or $PlcPort -lt 1 -or $PlcPort -gt 65535 -or
            $Rack -lt 0 -or $Rack -gt 7 -or $Slot -lt 0 -or $Slot -gt 31) { throw '端口、机架或槽号超出范围。' }
        $config = [ordered]@{ enabled=$true; profile='PHYSICAL_CONTROL'; host=$PlcHost; port=$PlcPort;
            rack=$Rack; slot=$Slot; listen_host=$ListenHost; web_port=$WebPort; control_enabled=$true;
            note='Standalone physical PLC commissioning; no software PLC.' }
    }
    $parent = Split-Path $target -Parent
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $stage = Join-Path $parent ('.wcs-stage-' + [Guid]::NewGuid().ToString('N'))
    $backup = $target + '.backup-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,6)
    # All staging/backup moves stay in the explicitly selected target's parent.
    if ((Split-Path ([IO.Path]::GetFullPath($stage)) -Parent) -ne $parent -or
        (Split-Path ([IO.Path]::GetFullPath($backup)) -Parent) -ne $parent) { throw '安装临时目录校验失败。' }
    [IO.Directory]::CreateDirectory($stage) | Out-Null
    try {
        Get-ChildItem -LiteralPath $payload -Force | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $stage -Recurse }
        [IO.Directory]::CreateDirectory((Join-Path $stage 'config')) | Out-Null
        [IO.Directory]::CreateDirectory((Join-Path $stage 'data')) | Out-Null
        $utf8 = New-Object Text.UTF8Encoding($false)
        [IO.File]::WriteAllText((Join-Path $stage 'config/plc.json'), ($config | ConvertTo-Json -Depth 10), $utf8)
        if ($preserve -and (Test-Path -LiteralPath (Join-Path $target 'data'))) {
            Get-ChildItem -LiteralPath (Join-Path $target 'data') -Force | ForEach-Object {
                Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $stage 'data') -Recurse -Force
            }
        }
        $exe = Join-Path $stage 'WcsPlcConsole.exe'
        $result = & $exe --self-test 2>&1
        if ($LASTEXITCODE -ne 0) { throw ('运行环境自检失败：' + ($result -join "`n")) }
        $result = & $exe --check 2>&1
        if ($LASTEXITCODE -ne 0) { throw ('网络配置自检失败，请确认公司局域网 IP 属于新主机：' + ($result -join "`n")) }
        if ($HistoryPath) {
            # Read-only validation and backup through SQLite, including any committed WAL data.
            $result = & $exe --audit-check $HistoryPath 2>&1
            if ($LASTEXITCODE -ne 0) { throw ('任务记录校验失败：' + ($result -join "`n")) }
            $result = & $exe --audit-copy $HistoryPath --audit-destination (Join-Path $stage 'data/physical-commands.sqlite3') 2>&1
            if ($LASTEXITCODE -ne 0) { throw ('任务记录迁移失败：' + ($result -join "`n")) }
        }
        $metadata = @{ product='wcs-physical-commissioning'; installed_at=[DateTime]::Now.ToString('o'); root=$target }
        [IO.File]::WriteAllText((Join-Path $stage '.wcs-install.json'), ($metadata | ConvertTo-Json), $utf8)
        if ($existing) { Move-Item -LiteralPath $target -Destination $backup }
        try { Move-Item -LiteralPath $stage -Destination $target }
        catch {
            if ($existing -and -not (Test-Path -LiteralPath $target)) { Move-Item -LiteralPath $backup -Destination $target }
            throw
        }
    } catch {
        throw ("安装未完成，原目录和现场 PLC 未被更改。临时文件保留在 $stage 。原因：" + $_.Exception.Message)
    }
    $port = if ($config.PSObject.Properties['web_port']) { $config.web_port } elseif ($config -is [Collections.IDictionary]) { $config['web_port'] } else { 8765 }
    return [pscustomobject]@{ Path=$target; Url=('http://' + $config.listen_host + ':' + $port + '/'); Preserved=$preserve; Backup=$(if ($existing) { $backup } else { '' }) }
}
