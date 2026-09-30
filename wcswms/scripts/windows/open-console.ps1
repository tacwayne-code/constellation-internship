$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$config=Get-Content -LiteralPath (Join-Path $root 'config/plc.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$port=if ($config.PSObject.Properties['web_port']) {$config.web_port} else {8765}
Start-Process ('http://' + $config.listen_host + ':' + $port + '/')
