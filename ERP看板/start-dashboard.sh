#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export PYTHONIOENCODING=utf-8
export ODOO_URL="${ODOO_URL:-http://x.inspiri.cn}"
export ODOO_DB="${ODOO_DB:-inspiri_erp}"
if [ -z "${ODOO_USER:-}" ]; then
  read -r -p "请输入 Odoo 只读用户名: " ODOO_USER
  export ODOO_USER
fi
export PORT="${PORT:-8088}"
export CACHE_TTL_SECONDS="${CACHE_TTL_SECONDS:-180}"

if [ -z "${ODOO_PASSWORD:-}" ]; then
  read -r -s -p "请输入 Odoo 密码: " ODOO_PASSWORD
  echo
  export ODOO_PASSWORD
fi

python3 server.py
