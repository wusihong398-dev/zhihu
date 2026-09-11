#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_DIR}"

echo "===== 容器 ====="
docker compose ps

echo "===== 本机API ====="
curl -fsS http://127.0.0.1:18080/api/health
echo
curl -fsS http://127.0.0.1:18080/api/version
echo

echo "===== Nginx ====="
nginx -t
curl -fsSI http://totod.cn | head -20

echo "===== 端口 ====="
ss -lntp | grep -E ':(22|80|443|18080)\\b' || true

echo "===== 资源 ====="
free -h
df -h /
swapon --show

