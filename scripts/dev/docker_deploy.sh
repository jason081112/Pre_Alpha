#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# 一条命令：build → up → 等健康 → smoke
#
#   ./scripts/dev/docker_deploy.sh
#
# 可选环境变量：
#   PIP_INDEX_URL   国内镜像（默认 PyPI）
#   ALPHA_TAG       指定镜像 tag（默认 git short sha）
#   SKIP_BUILD=1    只重启现有镜像
set -euo pipefail

cd "$(dirname "$0")/../.."

if [[ ! -f .env ]]; then
  echo "✗ 缺少 .env：cp .env.example .env 后填真实密钥（chmod 600）" >&2
  exit 1
fi

TAG="${ALPHA_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}"
mkdir -p .deploy
if [[ -f .deploy/current_tag ]]; then
  cp .deploy/current_tag .deploy/previous_tag
fi
echo "$TAG" > .deploy/current_tag

export ALPHA_TAG="$TAG"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}"

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  echo "▸ 构建 alpha:$TAG（pip index: $PIP_INDEX_URL）"
  docker compose build
fi

echo "▸ 启动 compose（tag=$TAG）"
docker compose up -d

echo "▸ 等待 /healthz（最多 120s）"
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:8080/healthz" >/dev/null 2>&1; then
    break
  fi
  if [[ $i -eq 60 ]]; then
    echo "✗ 容器未在 120s 内健康，最近日志：" >&2
    docker compose logs --tail=80 app >&2 || true
    exit 1
  fi
  sleep 2
done

echo "▸ smoke"
curl -fsS http://127.0.0.1:8080/healthz; echo
if curl -fsS http://127.0.0.1:8080/readyz >/tmp/alpha-readyz.json 2>/dev/null; then
  echo "✓ /readyz ok"
else
  echo "✗ /readyz 未通过：" >&2
  cat /tmp/alpha-readyz.json >&2 || true
  exit 1
fi
if [[ -n "${ALPHA_SMOKE_URL:-}" ]]; then
  curl -fsS -o /dev/null -w "✓ 公网 $ALPHA_SMOKE_URL → %{http_code}\n" "$ALPHA_SMOKE_URL"
fi

echo "✓ 部署完成：alpha:$TAG（回滚：./scripts/dev/docker_rollback.sh）"
