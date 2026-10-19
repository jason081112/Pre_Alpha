# P0-7 · Termux 环境盘点报告

> 阶段：P0 调研验证 · 任务：Termux 环境盘点报告：python3.11 / venv / pip 源 / 端口 8000 / 磁盘空间

> ⚠️ **本文结论已于 2026-10-19 修订**：部署形态改为云端运行、Web 优先，**555 MB 磁盘约束已降级、不再阻塞 P1**，Termux 仅作开发环境。正文保留原记录，修订内容见文末「结论修订」。

## 一、环境概览

| 项目 | 检测结果 | 备注 |
|---|---|---|
| Python | **3.14.6** | 计划写 3.11，实际 Termux 提供 3.14.6，更高版本 |
| pip | **26.2.1** | 来自系统 site-packages |
| venv | ✅ 可用 | `python3 -m venv` 正常 |
| pip 源 | 未检查 | 默认 PyPI，github.com 网络不稳定，pip 安装大包可能失败 |
| 端口 8000 | ❌ 未监听 | 尚未启动任何服务，P1 可直接占用 |
| 磁盘空间 | ⚠️ **仅剩 555 MB (100% 已用)** | **严重风险**，OpenHer 依赖安装可能需要几百 MB |
| 内存 | 7.2 GiB 总量，可用 1.3 GiB | 足够跑 OpenHer 纯 Python 后端 |
| 架构 | aarch64 (ARM64) | Termux 原生 ARM64，无 x86 兼容层问题 |

### 磁盘空间详情

```
/dev/block/bootdevice/by-name/userdata 106G 105G 555M 100% /storage/emulated/0/Android/obb
```

系统分区几乎满了，**仅剩约 555 MB 可用空间**。这是 P1 后端跑通的最大瓶颈：
- OpenHer `requirements.txt` 里的 fastapi/uvicorn/chromadb/pillow 等安装后可能占用 300–800 MB
- `vendor/OpenHer` 源码克隆已约 283 MB
- 建议 P1 前清理不需要的文件，或把依赖安装到外部存储（如 SD 卡）

### 网络状况

- github.com 直连不稳定（TLS 错误、连接中断），已验证 `v4.gh-proxy.org` 镜像可用于 clone
- pip 安装大包可能需要配置镜像源（如清华、阿里云 PyPI 镜像）

---

## 二、P1 后端跑通可行性评估

| 风险项 | 等级 | 说明 | 应对 |
|---|---|---|---|
| 磁盘空间不足 | 🔴 高 | 仅剩 555 MB，依赖安装可能撑爆 | 清理缓存、改用外部存储、精简依赖 |
| github.com 直连不稳定 | 🟡 中 | git clone/push 容易失败 | 已验证 gh-proxy 镜像，加重试循环 |
| Python 3.14 vs 3.11 | 🟢 低 | 版本更高，一般兼容 | 部分老包可能有轮子问题，实际安装再看 |
| 内存 | 🟢 低 | 可用 1.3 GiB，纯 Python 后端足够 | 无需操作 |
| 端口 8000 | 🟢 低 | 未被占用，可直接使用 | 无需操作 |

**结论**：Termux 上可以跑 OpenHer 后端，但**磁盘空间是硬约束**，P1 安装依赖前必须先清理空间。

---

## 三、P1 建议操作顺序

1. **清理磁盘空间**（确保 >1 GB 可用）：
   - `pip cache purge` 清理 pip 缓存
   - 检查并删除不必要的大文件/旧备份
   - 考虑将 `vendor/` 或 `.data/` 迁移到外部存储

2. **创建 venv 并安装依赖**：
   ```bash
   python3 -m venv ~/.venvs/openher
   source ~/.venvs/openher/bin/activate
   pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r vendor/OpenHer/requirements.txt
   ```

3. **配置环境变量**：
   - `.env` 设置 `DEFAULT_PROVIDER`、`DEFAULT_MODEL`、LLM API Key（密钥不入库，见 `.gitignore`）

4. **启动并验证**：
   ```bash
   cd vendor/OpenHer
   python main.py   # 或 uvicorn main:app --port 8000
   curl http://127.0.0.1:8000/api/status
   ```

---

**P0-7 状态**：调研完成。磁盘空间紧张（555 MB）是 P1 的首要风险点，需先清理。**（已修订，见下）**

---

## 结论修订（2026-10-19）

**架构方向变更：后端改为云端运行，Web 端优先。**

原结论以"OpenHer 后端跑在 Termux 手机上"为前提，故把"磁盘仅剩 555 MB"列为 P1 首要风险。经重新评估，部署形态改为：

- **引擎（OpenHer）统一跑在云端**，手机 Termux 仅作开发 / 调试环境
- **前端优先 Web/PWA**，Android（APK）搁置，将来可能做

由此修订：

| 原结论 | 修订后 |
|---|---|
| 🔴 Termux 磁盘 555 MB 是 P1 阻塞项 | 🟢 **降级**：后端不在 Termux 跑，磁盘紧张不再是 P1 阻塞；Termux 只需容纳代码 + 少量工具 |
| P1 = 在 Termux 起 `:8000` | P1 = 在**云端 / 本地开发容器**起 OpenHer，Termux 只做代码编辑与 git 操作 |

**仍有效的部分**：Termux 上的 git 网络不稳定（github 直连慢、需 gh-proxy 镜像）、Python 3.14.6、aarch64、内存等环境事实不变，仍作为开发环境参考。
