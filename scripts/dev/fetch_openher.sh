#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Fetch the pinned OpenHer fork into vendor/OpenHer.
#
# OpenHer 上游约 268 MB，其中 persona/personas/*/idimage/ 占 200 MB、docs/assets 占 55 MB、
# desktop/ 占 10 MB。本项目只消费「人格契约 + 引擎动力学 + 记忆」相关源码，因此默认做
# partial clone + sparse checkout，跳过模态素材与桌面端。需要完整素材时加 --with-assets。
#
# 仓库的 vendor/ 目录已 gitignore：上游源码不入库，本脚本保证本地可复现。
#
# Usage:
#   scripts/dev/fetch_openher.sh
#   scripts/dev/fetch_openher.sh /path/to/vendor/OpenHer
#   scripts/dev/fetch_openher.sh --with-assets
#   ALPHA_GIT_MIRROR=https://v4.gh-proxy.org scripts/dev/fetch_openher.sh
#
# 网络受限时：sparse checkout 需按需下载 blob，若代理中断会失败，脚本会自动回退到
# `--minimal`（用 raw 逐文件拉取，只取人格/引擎源码，约 5 MB）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST=""
WITH_ASSETS=0
FORCE_MINIMAL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-assets) WITH_ASSETS=1 ;;
    --minimal) FORCE_MINIMAL=1 ;;
    -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "ERROR: unknown option $1" >&2; exit 2 ;;
    *) DEST="$1" ;;
  esac
  shift
done
DEST="${DEST:-${ROOT}/vendor/OpenHer}"

UPSTREAM="https://github.com/kellyvv/OpenHer.git"
SHA="ef5b2145c9c15582499ecc5fb9d10376d82eccdf"
MIRROR="${ALPHA_GIT_MIRROR:-}"
REPO="${UPSTREAM}"
if [[ -n "${MIRROR}" ]]; then
  REPO="${MIRROR%/}/${UPSTREAM}"
fi

# 只保留人格/引擎/记忆/接入需要的目录；排除模态素材、文档大图、桌面端。
SPARSE_EXCLUDE=( "/persona/*/idimage/" "/docs/assets/" "/desktop/" )

# sparse checkout 需要按需下载 blob；网络受限时用 CHECKOUT_TIMEOUT 兜底，失败即回退 raw。
CHECKOUT_TIMEOUT="${ALPHA_CHECKOUT_TIMEOUT:-120}"

MINIMAL_PREFIXES=(
  "agent" "engine" "memory" "persona" "providers" "skills" "scripts" "tests"
)
MINIMAL_FILES=(
  "LICENSE" "README.md" "SKILL.md" "INSTALL.md" "requirements.txt"
)

info() { printf '%s\n' "$*"; }

verify() {
  local dest="$1"
  local missing=0
  for f in LICENSE README.md persona/loader.py persona/store.py \
           engine/genome/genome_engine.py engine/genome/style_memory.py \
           agent/prompt_builder.py; do
    if [[ ! -f "${dest}/${f}" ]]; then
      echo "ERROR: missing ${f}" >&2
      missing=1
    fi
  done
  if [[ ${missing} -ne 0 ]]; then
    return 1
  fi
  grep -qi 'Apache License' "${dest}/LICENSE"
  return 0
}

# partial clone：只要有 tree，不含 blob；checkout 时按需下载。
clone_skeleton() {
  local repo="$1" dest="$2"
  rm -rf "${dest}"
  mkdir -p "$(dirname "${dest}")"
  info "Cloning skeleton ${repo}"
  git -c pack.threads=1 clone --filter=blob:none --no-checkout --depth 1 \
      --branch main "${repo}" "${dest}" >/dev/null
}

apply_sparse() {
  local dest="$1"
  git -C "${dest}" sparse-checkout init --no-cone >/dev/null 2>&1
  local patterns=("/*")
  if [[ ${WITH_ASSETS} -eq 0 ]]; then
    local ex
    for ex in "${SPARSE_EXCLUDE[@]}"; do patterns+=("!${ex}"); done
  fi
  git -C "${dest}" sparse-checkout set --no-cone "${patterns[@]}" >/dev/null 2>&1
}

checkout_pinned() {
  local dest="$1"
  timeout "${CHECKOUT_TIMEOUT}" git -C "${dest}" fetch --no-tags --depth 1 origin "${SHA}" >/dev/null 2>&1 || true
  timeout "${CHECKOUT_TIMEOUT}" git -C "${dest}" checkout --detach "${SHA}" >/dev/null 2>&1 || return 1
  [[ "$(git -C "${dest}" rev-parse HEAD 2>/dev/null)" == "${SHA}" ]]
}

# 回退路径：tree 已在本地，按清单用 raw 逐文件下载，不碰 pack。
minimal_fetch() {
  local dest="$1"
  local tree
  tree="$(git -C "${dest}" ls-tree -r --name-only HEAD)"
  if [[ -z "${tree}" ]]; then
    echo "ERROR: 无法读取 tree；请检查 ${dest}/.git" >&2
    return 1
  fi
  info "Minimal fetch (raw) → ${dest}"
  TREE_LIST="${tree}" DEST_DIR="${dest}" SHA_REF="${SHA}" python3 - <<'PY'
import concurrent.futures, os, sys, time, urllib.parse, urllib.request

# 只取引擎与人格契约直接依赖的源码。skill 资产、桌面端、素材、provider 各实现（体积大且
# raw CDN 对其不稳定）全部跳过，只保留 provider 注册表本身。
prefixes = ("agent/", "engine/", "memory/", "persona/")
explicit = (
    "LICENSE", "README.md", "SKILL.md", "INSTALL.md", "requirements.txt",
    "providers/__init__.py", "providers/api.yaml", "providers/api_config.py",
    "providers/config.py",
)
skip_dirs = ("/idimage/", "docs/assets/", "desktop/", "agent/skills/")

paths = []
for line in os.environ["TREE_LIST"].splitlines():
    p = line.strip()
    if not p or any(d in p for d in skip_dirs):
        continue
    if p.startswith(prefixes) or p in explicit:
        paths.append(p)

dest = os.environ["DEST_DIR"]
sha = os.environ["SHA_REF"]
timeout_s = int(os.environ.get("ALPHA_RAW_TIMEOUT", "8"))
budget_s = int(os.environ.get("ALPHA_RAW_BUDGET", "120"))
deadline = time.time() + budget_s

def fetch(path):
    target = os.path.join(dest, path)
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return "skip", path
    if time.time() > deadline:
        return "fail:budget", path
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    url = "https://raw.githubusercontent.com/kellyvv/OpenHer/" + sha + "/" + urllib.parse.quote(path)
    err = "unknown"
    for _ in range(2):
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as resp:
                data = resp.read()
            with open(target, "wb") as handle:
                handle.write(data)
            return "ok", path
        except Exception as exc:  # noqa: BLE001
            err = str(exc)[:60]
    return "fail:" + err, path

ok = skip = fail = 0
failures = []
pending = [p for p in paths if not os.path.exists(os.path.join(dest, p))]
print(f"tree={len(paths)} pending={len(pending)} (budget {budget_s}s)", flush=True)

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    futures = [pool.submit(fetch, p) for p in pending]
    done = 0
    for fut in concurrent.futures.as_completed(futures):
        status, path = fut.result()
        done += 1
        if status == "ok":
            ok += 1
        else:
            fail += 1
            failures.append((path, status))
        if done % 10 == 0 or done == len(pending):
            print(f"  progress {done}/{len(pending)} ok={ok} fail={fail}", flush=True)

skip = len(paths) - len(pending)
print(f"raw fetch: ok={ok} skip={skip} fail={fail} total={len(paths)}")
for path, status in failures[:8]:
    print(f"  WARN {path} ({status})", file=sys.stderr)
if failures:
    print("  可重复执行本脚本补齐；核心文件已由 verify 校验", file=sys.stderr)
PY
}

main() {
  info "OpenHer target: ${DEST}"
  info "  upstream: ${UPSTREAM}"
  info "  commit:   ${SHA}"

  if [[ ! -d "${DEST}/.git" ]]; then
    clone_skeleton "${REPO}" "${DEST}"
  fi

  if [[ ${FORCE_MINIMAL} -eq 0 ]]; then
    apply_sparse "${DEST}"
    if checkout_pinned "${DEST}" && verify "${DEST}"; then
      info "OpenHer ready (sparse): ${DEST}"
      print_summary
      return 0
    fi
    echo "WARN: sparse checkout 失败，回退到 raw 逐文件拉取" >&2
  fi

  minimal_fetch "${DEST}"
  verify "${DEST}"
  info "OpenHer ready (minimal): ${DEST}"
  print_summary
}

print_summary() {
  local files size
  files="$(find "${DEST}" -type f -not -path "*/.git/*" | wc -l | tr -d ' ')"
  size="$(du -sh "${DEST}" 2>/dev/null | cut -f1)"
  printf '  files:    %s\n' "${files}"
  printf '  size:     %s\n' "${size}"
  printf '  checkout: %s\n' "$(git -C "${DEST}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  if [[ ${WITH_ASSETS} -eq 0 ]]; then
    printf '  assets:   skipped (idimage/ docs/assets/ desktop/)\n'
  else
    printf '  assets:   included\n'
  fi
}

main "$@"
