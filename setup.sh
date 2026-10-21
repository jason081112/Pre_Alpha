#!/usr/bin/env bash
# Pre_Alpha 开发环境安装
#
# 支持: Linux（容器 / VPS / Codespaces）
# 拒绝: Termux / Android（原因与替代方案见 docs/p1-04-env-setup.md）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENHER_DIR="$REPO_ROOT/vendor/OpenHer"
VENV_DIR="$OPENHER_DIR/.venv"

FETCH=0
FULL=0
NO_ENV=0

usage() {
  cat <<'USAGE'
用法: ./setup.sh [选项]

选项:
  --fetch      vendor/OpenHer 不存在时自动下载（tarball，约 255 MB）
  --full       安装全部依赖（含代码未引用的 chromadb / evermemos）
  --no-env     不生成 vendor/OpenHer/.env
  -h, --help   显示帮助

支持平台: Linux x86_64 / aarch64（容器、VPS、Codespaces）
不支持:   Termux / Android（原因见 docs/p1-04-env-setup.md）
USAGE
}

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --fetch)  FETCH=1 ;;
    --full)   FULL=1 ;;
    --no-env) NO_ENV=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $arg（用 --help 查看用法）" ;;
  esac
done

detect_platform() {
  if [ -n "${PREFIX:-}" ] && [[ "$PREFIX" == *"com.termux"* ]]; then
    echo termux
    return
  fi
  case "$(uname -s)" in
    Linux) echo linux ;;
    *)     echo other ;;
  esac
}

PLATFORM="$(detect_platform)"

if [ "$PLATFORM" = "termux" ]; then
  cat >&2 <<'MSG'
[!] 检测到 Termux / Android：本脚本不在此安装。

原因:
  1. Termux 的 pip 只接受 android_arm64_v8a 标签，PyPI 的 manylinux wheel 全部不可用
  2. pydantic-core / tokenizers / orjson 无 Android 预编译包，只能源码编译
  3. 编译需要 Rust 工具链，中间产物数百 MB，手机空间不足

替代方案:
  在云端 Linux（GitHub Codespaces / VPS / 容器）运行本脚本。
  详见 docs/p1-04-env-setup.md
MSG
  exit 2
fi

if [ "$PLATFORM" != "linux" ]; then
  die "仅支持 Linux，当前系统: $(uname -s)"
fi

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || die "找不到 python3，请先安装 Python 3.11+"

"$PYTHON" - <<'PY' || die "Python 版本过低，需要 3.11+"
import sys
if sys.version_info < (3, 11):
    print("  当前: %s" % sys.version.split()[0])
    sys.exit(1)
PY

log "Python: $("$PYTHON" --version 2>&1)"

if [ ! -d "$OPENHER_DIR" ] || [ -z "$(ls -A "$OPENHER_DIR" 2>/dev/null)" ]; then
  if [ "$FETCH" -eq 1 ]; then
    log "下载 OpenHer（tarball，约 255 MB）..."
    mkdir -p "$OPENHER_DIR"
    curl -fL --retry 3 --retry-delay 5 \
      "https://codeload.github.com/kellyvv/OpenHer/tar.gz/refs/heads/main" \
      | tar xz -C "$OPENHER_DIR" --strip-components=1
  else
    die "vendor/OpenHer 不存在。用 ./setup.sh --fetch 自动下载，或手动放置。
  参考:
    git clone --depth 1 https://github.com/kellyvv/OpenHer.git vendor/OpenHer"
  fi
fi

if [ ! -f "$OPENHER_DIR/requirements.txt" ]; then
  die "缺少 $OPENHER_DIR/requirements.txt，vendor/OpenHer 可能不完整"
fi

if [ ! -d "$VENV_DIR" ]; then
  log "创建 venv: $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

log "升级 pip"
python -m pip install -U pip wheel >/dev/null

REQ_SRC="$OPENHER_DIR/requirements.txt"
REQ_TMP="$OPENHER_DIR/requirements.local.txt"

if [ "$FULL" -eq 1 ]; then
  cp "$REQ_SRC" "$REQ_TMP"
  log "安装全部依赖（--full）"
else
  grep -vE '^(evermemos|chromadb)' "$REQ_SRC" > "$REQ_TMP"
  log "安装依赖（跳过未被代码引用的 evermemos / chromadb）"
fi

python -m pip install -r "$REQ_TMP"

if [ "$NO_ENV" -eq 0 ] && [ ! -f "$OPENHER_DIR/.env" ]; then
  cp "$OPENHER_DIR/.env.example" "$OPENHER_DIR/.env"
  warn "已生成 $OPENHER_DIR/.env，请填入 LLM 密钥后启动"
fi

cat <<'DONE'

==> 安装完成

启动:
  source vendor/OpenHer/.venv/bin/activate
  cd vendor/OpenHer
  uvicorn main:app --host 0.0.0.0 --port 8000

验证:
  curl -s http://localhost:8000/api/status
  curl -s http://localhost:8000/api/personas

注意: 上游 README 的 `python main.py` 已失效（main.py 无 __main__ 块），必须用 uvicorn。
DONE
