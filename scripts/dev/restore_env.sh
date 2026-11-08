#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# 恢复本机开发环境：gh CLI + GitHub 凭据 + git 身份 + credential helper
#                  + Python 依赖（服务端/引擎/流水线）+ 密钥（可选）。
#
# 适用场景：容器/Codespace 的根文件系统被重建，/root 下的 gh 与 ~/.config/gh 丢失，
# 而持久盘（如 /sandbox/workspace）里的仓库与备份仍在。
#
# 凭据备份是 gh 的 hosts.yml（含 token）；密钥备份是 .secrets/alpha.env。
# 脚本只读取它们，不打印任何值。
#
# 一条命令从零恢复到能跑：
#   bash scripts/dev/restore_env.sh
#
# Usage:
#   scripts/dev/restore_env.sh
#   scripts/dev/restore_env.sh --backup /path/to/hosts.yml
#   scripts/dev/restore_env.sh --no-install          # 只恢复凭据，不装包
#   ALPHA_GH_BACKUP=/path/to/hosts.yml scripts/dev/restore_env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP="${ALPHA_GH_BACKUP:-}"
INSTALL=1
GIT_NAME="${ALPHA_GIT_NAME:-jason081112}"
GIT_EMAIL="${ALPHA_GIT_EMAIL:-jason081112@users.noreply.github.com}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) BACKUP="${2:-}"; shift ;;
    --no-install) INSTALL=0 ;;
    --git-name) GIT_NAME="${2:-}"; shift ;;
    --git-email) GIT_EMAIL="${2:-}"; shift ;;
    -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "ERROR: unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

info() { printf '  %s\n' "$*"; }

# 备份候选：显式 > /sandbox/workspace/.gh_backup > $HOME/.gh_backup
resolve_backup() {
  local candidates=()
  [[ -n "${BACKUP}" ]] && candidates+=("${BACKUP}")
  candidates+=("/sandbox/workspace/.gh_backup/hosts.yml" "${HOME}/.gh_backup/hosts.yml")
  local path
  for path in "${candidates[@]}"; do
    if [[ -f "${path}" ]]; then
      printf '%s\n' "${path}"
      return 0
    fi
  done
  return 1
}

install_gh() {
  if command -v gh >/dev/null 2>&1; then
    info "gh 已存在：$(gh --version | head -1)"
    return 0
  fi
  if [[ ${INSTALL} -eq 0 ]]; then
    echo "ERROR: gh 未安装且指定了 --no-install" >&2
    return 1
  fi
  if command -v apt-get >/dev/null 2>&1; then
    info "安装 gh (apt)"
    if ! apt-get install -y gh >/dev/null 2>&1; then
      info "apt 缓存可能过期，重试 update"
      apt-get update -qq >/dev/null 2>&1 || true
      apt-get install -y gh >/dev/null 2>&1
    fi
  else
    echo "ERROR: 无 apt-get，请手动安装 gh（https://github.com/cli/cli）" >&2
    return 1
  fi
  command -v gh >/dev/null 2>&1
}

restore_credentials() {
  local source
  if ! source="$(resolve_backup)"; then
    echo "ERROR: 找不到 hosts.yml 备份；请用 --backup 指定" >&2
    return 1
  fi
  mkdir -p "${HOME}/.config/gh"
  cp "${source}" "${HOME}/.config/gh/hosts.yml"
  chmod 600 "${HOME}/.config/gh/hosts.yml"
  info "凭据已恢复自 ${source}"
}

setup_git() {
  gh auth setup-git
  git config --global user.name "${GIT_NAME}"
  git config --global user.email "${GIT_EMAIL}"
  info "git 身份：${GIT_NAME} <${GIT_EMAIL}>"
}

verify() {
  local status
  if ! status="$(gh auth status 2>&1)"; then
    echo "${status}" >&2
    echo "ERROR: gh 认证失败" >&2
    return 1
  fi
  printf '%s\n' "${status}" | sed -n '1,6p'
  if printf '%s\n' "${status}" | grep -q '"' ; then :; fi
  if git -C "${ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
    info "仓库：$(git -C "${ROOT}" log --oneline -1 2>/dev/null || echo '(无提交)')"
  fi
}

restore_python_deps() {
  # 沙箱根文件系统会重置，site-packages 一并丢失。覆盖三组：
  #   服务端   fastapi / uvicorn / httpx / websockets / dotenv / cryptography
  #   引擎     openai / anthropic / aiosqlite / apscheduler / Pillow / edge-tts
  #   流水线   frontmatter / yaml / jsonschema / pytest
  # 只装缺的；全在则秒过。
  local missing
  missing="$(python3 - <<'PY'
import importlib.util

mods = {
    "frontmatter": "python-frontmatter",
    "yaml": "pyyaml",
    "jsonschema": "jsonschema",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "httpx": "httpx",
    "websockets": "websockets",
    "dotenv": "python-dotenv",
    "openai": "openai",
    "anthropic": "anthropic",
    "aiosqlite": "aiosqlite",
    "apscheduler": "apscheduler",
    "PIL": "pillow",
    "edge_tts": "edge-tts",
    "cryptography": "cryptography",
    "pytest": "pytest",
}
print(" ".join(pkg for mod, pkg in mods.items() if importlib.util.find_spec(mod) is None))
PY
)"
  if [ -z "${missing}" ]; then
    info "Python 依赖齐全（服务端 + 引擎 + 流水线，共 16 个模块）"
    return 0
  fi
  info "补装 Python 依赖：${missing}"
  # 默认 pypi 极慢，走阿里云镜像
  if pip install -q ${missing} -i https://mirrors.aliyun.com/pypi/simple/ >/dev/null 2>&1; then
    info "Python 依赖已装好"
  else
    info "WARN: pip 安装失败（可能无网络），需要联网的操作会降级"
    return 0
  fi
}

restore_venv() {
  # 坑（2026-11-08 踩过）：venv 绝不能放 /sandbox/workspace。
  # 那是 overlay 文件系统，stat 极慢 —— import fastapi 会卡在
  # _bootstrap_external._path_stat 上几分钟甚至不返回。
  # 放 /tmp（容器内层，快），代价是重建后要重装，本函数负责这件事。
  local venv="${ALPHA_VENV:-/tmp/venv}"
  if [ -x "${venv}/bin/python" ] && "${venv}/bin/python" -c "import fastapi, httpx, websockets" >/dev/null 2>&1; then
    info "venv 就绪：${venv}"
    return 0
  fi
  info "创建 venv：${venv}（首次约 1–2 分钟）"
  python3 -m venv "${venv}" >/dev/null 2>&1 || {
    info "WARN: venv 创建失败，回退到系统 pip"
    return 0
  }
  "${venv}/bin/pip" install -q \
    fastapi "uvicorn[standard]" httpx websockets python-dotenv \
    python-frontmatter pyyaml jsonschema openai anthropic aiosqlite \
    apscheduler pillow edge-tts cryptography \
    -i https://mirrors.aliyun.com/pypi/simple/ >/dev/null 2>&1 && \
    info "venv 依赖已装好：${venv}"
}

restore_secrets() {
  # 密钥不入库：放在 <workspace>/.secrets/alpha.env（KEY=VALUE，chmod 600）。
  # 找不到也不影响启动 —— shell/app.py 对上游 startup 做了启动解耦，
  # 缺 key 时服务能起，只有真正调用模型才报错。
  local candidates=(
    "/sandbox/workspace/.secrets/alpha.env"
    "${HOME}/.alpha/env"
  )
  local path
  for path in "${candidates[@]}"; do
    if [[ -f "${path}" ]]; then
      set -a
      # shellcheck disable=SC1090
      . "${path}"
      set +a
      info "已加载密钥：${path}（$(grep -c '=' "${path}" 2>/dev/null || echo 0) 项，值不打印）"
      return 0
    fi
  done
  info "未找到 .secrets/alpha.env —— 跳过（服务仍可启动，调用模型时才报缺 key）"
}

info "环境恢复开始（仓库：${ROOT}）"
install_gh
restore_credentials
setup_git
restore_python_deps
restore_venv
restore_secrets
verify
info "完成。可运行 gh auth status / git push 校验。"
