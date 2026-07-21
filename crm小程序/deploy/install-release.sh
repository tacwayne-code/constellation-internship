#!/usr/bin/env bash
set -euo pipefail

PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ROOT="${APP_ROOT:-/home/sameng/apps/crm-v01}"
SERVICE_NAME="${SERVICE_NAME:-crm-v01.service}"
RELEASE_ID="$(date +%Y%m%d-%H%M%S)"
RELEASE_DIR="$APP_ROOT/releases/$RELEASE_ID"

if [[ ! -f "$PACKAGE_ROOT/dist/index.html" || ! -f "$PACKAGE_ROOT/server/shared_server.py" ]]; then
  echo "发布包不完整：缺少 dist/index.html 或 server/shared_server.py" >&2
  exit 1
fi

mkdir -p "$APP_ROOT/releases" "$APP_ROOT/shared" "$RELEASE_DIR"
cp -a "$PACKAGE_ROOT/dist" "$PACKAGE_ROOT/server" "$RELEASE_DIR/"
rm -rf "$RELEASE_DIR/server/data" "$RELEASE_DIR/server/__pycache__"

if [[ ! -f "$APP_ROOT/shared/crm.env" ]]; then
  cp "$PACKAGE_ROOT/deploy/crm.env.example" "$APP_ROOT/shared/crm.env"
  chmod 600 "$APP_ROOT/shared/crm.env"
  echo "已创建 $APP_ROOT/shared/crm.env，请配置高德和 Odoo。"
fi

if [[ -e "$APP_ROOT/current" && ! -L "$APP_ROOT/current" ]]; then
  mv "$APP_ROOT/current" "$APP_ROOT/current.backup-$RELEASE_ID"
fi
ln -sfn "$RELEASE_DIR" "$APP_ROOT/current"

sudo cp "$PACKAGE_ROOT/deploy/crm-v01.service" "/etc/systemd/system/$SERVICE_NAME"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

for _ in {1..15}; do
  if curl --fail --silent http://127.0.0.1:8123/api/health; then
    echo
    echo "CRM 发布成功：$RELEASE_DIR"
    exit 0
  fi
  sleep 1
done

echo "健康检查失败：sudo journalctl -u $SERVICE_NAME -n 100 --no-pager" >&2
exit 1
