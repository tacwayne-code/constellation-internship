$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Test-Path '.venv/Scripts/python.exe')) { python -m venv .venv }
    & ./.venv/Scripts/python.exe -m pip install -r requirements.txt --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw '服务依赖安装失败' }
    & npm.cmd --prefix delivery-tower/frontend ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
    & npm.cmd --prefix delivery-tower/frontend run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
    Write-Host '交付塔：http://127.0.0.1:8782'
    & ./.venv/Scripts/python.exe -m delivery.run
} finally { Pop-Location }
