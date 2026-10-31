#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# 恢复本机开发环境：gh CLI + GitHub 凭据 + git 身份 + credential helper。
#
# 适用场景：容器/Codespace 的根文件系统被重建，/root 下的 gh 与 ~/.config/gh 丢失，
# 而持久盘（如 /sandbox/workspace）里的仓库与备份仍在。
#
# 凭据备份是 gh 的 hosts.yml（含 token）。脚本只读取它，不会打印 token 内容。
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
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
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
  # 沙箱根文件系统会重置，site-packages 一并丢失：gh 之外 python 依赖也要补。
  local missing
  missing="$(python3 -c 'import importlib.util
mods = {"frontmatter": "python-frontmatter", "yaml": "pyyaml", "jsonschema": "jsonschema"}
print(" ".join(pkg for mod, pkg in mods.items() if importlib.util.find_spec(mod) is None))')"
  if [ -z "${missing}" ]; then
    info "Python 依赖齐全（frontmatter / yaml / jsonschema）"
    return 0
  fi
  info "补装 Python 依赖：${missing}"
  # 默认 pypi 极慢，走阿里云镜像
  if pip install -q ${missing} -i https://mirrors.aliyun.com/pypi/simple/ >/dev/null 2>&1; then
    info "Python 依赖已装好"
  else
    info "WARN: pip 安装失败（可能无网络），vendor 相关校验会降级为 skipped"
  fi
}

info "环境恢复开始（仓库：${ROOT}）"
install_gh
restore_credentials
setup_git
restore_python_deps
verify
info "完成。可运行 gh auth status / git push 校验。"
