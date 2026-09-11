#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 执行此脚本"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"

install -d -m 700 /var/lib/totod/accounts /var/lib/totod/uploads /var/lib/totod/postgres /var/lib/totod/redis /var/log/totod /var/backups/totod

if [[ ! -f "${ENV_FILE}" ]]; then
  umask 077
  database_password="$(openssl rand -hex 24)"
  application_secret="$(openssl rand -hex 48)"
  {
    echo "APP_NAME=TOTOD 知乎运营助手"
    echo "APP_ENV=production"
    echo "COMPOSE_PROJECT_NAME=totod"
    echo "APP_SECRET_KEY=${application_secret}"
    echo "ACCESS_TOKEN_EXPIRE_MINUTES=720"
    echo "DATABASE_URL=postgresql+asyncpg://totod:${database_password}@postgres:5432/totod"
    echo "REDIS_URL=redis://redis:6379/0"
    echo "ACCOUNT_DATA_ROOT=/var/lib/totod/accounts"
    echo "DEFAULT_TIMEZONE=Asia/Shanghai"
    echo "POSTGRES_DB=totod"
    echo "POSTGRES_USER=totod"
    echo "POSTGRES_PASSWORD=${database_password}"
  } > "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "已生成仅保存在服务器的 .env"
else
  echo "保留现有 .env，不重新生成密钥"
fi

cd "${PROJECT_DIR}"
docker compose up -d --build

install -d -m 755 /etc/nginx/sites-available /etc/nginx/sites-enabled
install -m 644 "${PROJECT_DIR}/nginx/totod.cn.conf" /etc/nginx/sites-available/totod.cn
ln -sfn /etc/nginx/sites-available/totod.cn /etc/nginx/sites-enabled/totod.cn
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  unlink /etc/nginx/sites-enabled/default
fi

nginx -t
systemctl reload nginx
rmdir "${PROJECT_DIR}/+" 2>/dev/null || true

echo "等待后端健康检查"
for attempt in {1..30}; do
  if curl -fsS http://127.0.0.1:18080/api/health; then
    echo
    echo "TOTOD 后端启动成功"
    docker compose ps
    exit 0
  fi
  sleep 2
done

echo "后端未在60秒内通过健康检查"
docker compose ps
docker compose logs --tail=120 backend
exit 1
