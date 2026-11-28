#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# 回滚到上一镜像；不带参数时读 .deploy/previous_tag
#
#   ./scripts/dev/docker_rollback.sh            # 回上一版
#   ./scripts/dev/docker_rollback.sh 6e16449    # 回指定 tag
#
# 数据兼容：SQLite 表结构只在启动时幂等创建，不删列不改列，
# 因此老镜像读新库通常没问题；若新版本做过破坏性变更，先恢复
# /var/lib/prealpha 与引擎 .data 的备份再回滚。
set -euo pipefail

cd "$(dirname "$0")/../.."

TAG="${1:-}"
if [[ -z "$TAG" ]]; then
  if [[ ! -f .deploy/previous_tag ]]; then
    echo "✗ 没有 .deploy/previous_tag，请显式给 tag" >&2
    exit 1
  fi
  TAG="$(cat .deploy/previous_tag)"
fi

if ! docker image inspect "alpha:$TAG" >/dev/null 2>&1; then
  echo "✗ 本地没有镜像 alpha:$TAG，可用：docker images alpha" >&2
  exit 1
fi

echo "▸ 回滚到 alpha:$TAG"
ALPHA_TAG="$TAG" docker compose up -d --no-build

for i in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "✓ 回滚完成：alpha:$TAG"
    [[ -f .deploy/previous_tag ]] && echo "$TAG" > .deploy/current_tag
    exit 0
  fi
  sleep 2
done

echo "✗ 回滚后仍未健康，请检查：" >&2
docker compose logs --tail=80 app >&2 || true
exit 1
