# P1-4 环境安装记录：Termux 不适用，改走云端 Linux

> 结论先行：**引擎不在 Termux 上运行**。本文件记录实测依据、云端安装步骤、
> 以及踩到的坑，供 `setup.sh` 与后续 P1-6 使用。

---

## 1. 结论

| 平台 | 是否支持 | 处理 |
|---|---|---|
| Linux x86_64 / aarch64（容器、VPS、Codespaces） | ✅ | `./setup.sh` 标准安装 |
| Termux / Android | ❌ | `./setup.sh` 直接拒绝，退出码 2 |

依据：[ADR-0001](../docs/adr/0001-deployment-cloud-web-first.md)（云端 + Web/PWA 优先）。

---

## 2. Termux 为什么不行（2026-10 实测）

| 项 | 实测结果 |
|---|---|
| pip 兼容标签 | 全部是 `android_arm64_v8a`，**`manylinux` 数量为 0** |
| `pydantic-core` | 无 Android wheel → 需要 Rust 源码编译 |
| `tokenizers` | 无 Android wheel → 需要 Rust 源码编译（最重） |
| `orjson` | 无 Android wheel → 需要 Rust 源码编译 |
| 磁盘 | 可用空间不足 1 GB，编译中间产物风险高 |
| 常驻能力 | 手机无法 7×24 运行，与产品定位冲突 |

Termux 上可用的预编译包只有：`python-numpy`、`python-onnxruntime`、`python-grpcio`、
`python-cryptography`、`python-bcrypt`、`python-pillow`。核心的 pydantic 生态仍需编译。

---

## 3. 两个影响安装的发现

### 3.1 `chromadb`、`evermemos` 在代码里 0 处 import

全仓搜索：

```bash
grep -rn "import chromadb\|from chromadb" --include=*.py vendor/OpenHer   # 无结果
grep -rn "import evermemos\|from evermemos" --include=*.py vendor/OpenHer # 无结果
```

两者合计约 300 MB，属于历史遗留依赖。**默认安装时跳过**（`setup.sh` 已内置），
需要时可用 `./setup.sh --full` 装全量。

EverMemOS 走的是 HTTP 客户端（`providers/memory/evermemos/`），不依赖 Python 包。

### 3.2 上游 README 的启动命令已失效

```bash
grep -n "__main__\|uvicorn" vendor/OpenHer/main.py   # 无结果
```

`main.py` 只有 `app = FastAPI(...)`，没有 `uvicorn.run`。正确启动方式：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

另外**没有 `/health` 端点**，存活检查用 `/api/status`。

---

## 4. 云端安装步骤（Codespaces / VPS 通用）

### 4.1 放一份 OpenHer

`vendor/` 被本仓库 `.gitignore` 排除，所以云端 clone Pre_Alpha 后不含上游代码。

```bash
# 方式 A（推荐，省 221 MB）：只取工作区
mkdir -p vendor/OpenHer
curl -L https://codeload.github.com/kellyvv/OpenHer/tar.gz/refs/heads/main \
  | tar xz -C vendor/OpenHer --strip-components=1

# 方式 B：需要 git 历史时
git clone --depth 1 https://github.com/kellyvv/OpenHer.git vendor/OpenHer
```

| 方式 | 占用 |
|---|---|
| A（tarball） | 约 255 MB |
| B（含 `.git`） | 约 478 MB（`.git` 221 MB + 工作区 255 MB） |

上游仓库本身就是 `depth=1`（`size-pack` 221 MiB），所以浅克隆不会更小。

### 4.2 一键安装

```bash
./setup.sh            # 首次会自动提示缺 vendor
./setup.sh --fetch    # 或让它自己下载
```

脚本行为：

1. 检测平台，Termux 直接拒绝（退出码 2）
2. 校验 Python ≥ 3.11
3. 创建 `vendor/OpenHer/.venv`
4. 过滤掉 `evermemos` / `chromadb` 后安装
5. 首次生成 `.env`（不覆盖已有文件）

### 4.3 配置密钥

```bash
$EDITOR vendor/OpenHer/.env
```

至少填一组：

```bash
DEFAULT_PROVIDER=gemini
DEFAULT_MODEL=gemini-3.1-flash-lite-preview
GEMINI_API_KEY=xxxx
```

`DEFAULT_PROVIDER` 可选：`gemini`、`claude`、`dashscope`、`openai`、`minimax`、`moonshot`、`stepfun`、`ollama`。

### 4.4 启动与验证

```bash
source vendor/OpenHer/.venv/bin/activate
cd vendor/OpenHer
uvicorn main:app --host 0.0.0.0 --port 8000
```

```bash
curl -s localhost:8000/api/status                       # 不需要 Key
curl -s localhost:8000/api/personas                     # 不需要 Key
curl -s -X POST localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"你好","persona_id":"luna"}'           # 需要 Key
```

内置角色：`ember` `iris` `kai` `kelly` `luna` `mia` `nova` `rex` `sora` `vivian`。

---

## 5. 失败项与替代装法

| 现象 | 原因 | 替代装法 |
|---|---|---|
| Termux `pip install` 卡在 `Building wheel` | 无 manylinux wheel | 换云端 Linux（本文件方案） |
| `python3 -m venv` 报 `ensurepip is not available` | 缺 `python3-venv` | `sudo apt-get install -y python3-venv` |
| `pip install` 内存不足 | 机器内存 < 4 GB | 升级到 8 GB，或分步安装 |
| `python main.py` 无反应/立即退出 | 上游 README 过时 | 用 `uvicorn main:app` |
| `/health` 404 | 无此端点 | 用 `/api/status` |
| `ModuleNotFoundError: evermemos` | 依赖被过滤但确有 import | 实测无 import；如真出现，`pip install evermemos` |
| 启动报端口占用 | 旧进程未退出 | `pkill -f "uvicorn main:app"` |

---

## 6. 待验证项

- [ ] 在 Codespaces（Ubuntu x86_64 / Python 3.12）跑通 `./setup.sh`
- [ ] `/api/status` 与 `/api/chat` 通过（#16）
- [ ] 记录首轮时延与 token 成本（#18）

> 本文件随 P1-6 实测结果补充。
