$ErrorActionPreference = "Stop"

$env:PYTHONIOENCODING = "utf-8"
if (-not $env:ODOO_URL) { $env:ODOO_URL = "http://x.inspiri.cn" }
if (-not $env:ODOO_DB) { $env:ODOO_DB = "inspiri_erp" }
if (-not $env:ODOO_USER) { $env:ODOO_USER = Read-Host "请输入 Odoo 只读用户名" }
if (-not $env:PORT) { $env:PORT = "8088" }
if (-not $env:CACHE_TTL_SECONDS) { $env:CACHE_TTL_SECONDS = "180" }

if (-not $env:ODOO_PASSWORD) {
  $securePassword = Read-Host "请输入 Odoo 密码" -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
  try {
    $env:ODOO_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
}

Set-Location -LiteralPath $PSScriptRoot
python .\server.py
