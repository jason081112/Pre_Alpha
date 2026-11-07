#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# 在 GitHub Codespaces 里一条命令起服务。
#
#   bash scripts/dev/codespace_start.sh
#
# 密钥来源（按优先级）：
#   1. 环境变量 OPENAI_API_KEY —— Codespaces secret 会自动注入，不用手贴
#      gh secret set OPENAI_API_KEY --repo <owner>/<repo> --app codespaces
#   2. $HOME/.alpha.env（KEY=VALUE），给不想配 secret 的人用
#
# 端口 8000 由 .devcontainer/devcontainer.json 的 forwardPorts 暴露，
# 起来后用 https://<codespace>-8000.app.github.dev 直接访问。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

# 密钥兜底：没有环境变量时读 ~/.alpha.env
if [ -z "${OPENAI_API_KEY:-}" ] && [ -f "${HOME}/.alpha.env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "${HOME}/.alpha.env"
  set +a
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "⚠️  没有 OPENAI_API_KEY：服务能起，但调用模型会失败。" >&2
  echo "   配一次即可（不用再贴）：" >&2
  echo "   gh secret set OPENAI_API_KEY --repo <owner>/<repo> --app codespaces" >&2
fi

PYTHON="${ALPHA_PYTHON:-${HOME}/venv/bin/python}"
if [ ! -x "${PYTHON}" ]; then
  echo "→ 首次运行，创建 venv 并安装依赖（约 1–2 分钟）"
  python3 -m venv "${HOME}/venv"
  "${HOME}/venv/bin/pip" install --quiet \
    fastapi "uvicorn[standard]" httpx websockets python-dotenv \
    python-frontmatter pyyaml jsonschema openai anthropic aiosqlite \
    apscheduler pillow edge-tts cryptography
  PYTHON="${HOME}/venv/bin/python"
fi

export ALPHA_PORT="${ALPHA_PORT:-8000}"
export ALPHA_HOST="${ALPHA_HOST:-0.0.0.0}"
export ALPHA_DATA_DIR="${ALPHA_DATA_DIR:-/tmp/alpha}"
export ALPHA_REQUIRE_INVITE="${ALPHA_REQUIRE_INVITE:-0}"
# 新用户没有自带路由（见 issue #97），先用服务端配置兜一个默认值。
# 有 ALPHA_DEV_ROUTE 就不用管；想走用户自己的路由时把这个置空。
export ALPHA_DEV_ROUTE="${ALPHA_DEV_ROUTE:-ollama-cloud/gemma4:31b}"

echo "→ http://0.0.0.0:${ALPHA_PORT}  (dev_route=${ALPHA_DEV_ROUTE:-unset})"
exec "${PYTHON}" -u -m shell.app
