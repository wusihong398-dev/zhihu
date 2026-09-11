#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 执行此脚本"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y certbot python3-certbot-nginx

certbot_args=(
  --nginx
  --non-interactive
  --agree-tos
  --redirect
  -d totod.cn
  -d www.totod.cn
)

if [[ -n "${CERTBOT_EMAIL:-}" ]]; then
  certbot_args+=(--email "${CERTBOT_EMAIL}")
else
  certbot_args+=(--register-unsafely-without-email)
fi

certbot "${certbot_args[@]}"
install -m 644 "${PROJECT_DIR}/nginx/totod.cn.https.conf" /etc/nginx/sites-available/totod.cn
nginx -t
systemctl reload nginx

echo "HTTPS 配置完成"
curl -sSI --connect-timeout 10 http://totod.cn | head -n 5 || true
curl -sSI --connect-timeout 10 https://totod.cn | head -n 5 || true
