$ErrorActionPreference = 'Stop'
$demoDirectory = Join-Path $PSScriptRoot 'delivery-demo'
Push-Location -LiteralPath $demoDirectory
try {
    if (-not (Test-Path -LiteralPath 'node_modules/vite/bin/vite.js')) {
        & npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw '演示依赖安装失败。' }
    }
    Write-Host '交付塔概念演示：http://127.0.0.1:8781'
    & npm.cmd run dev
} finally {
    Pop-Location
}
