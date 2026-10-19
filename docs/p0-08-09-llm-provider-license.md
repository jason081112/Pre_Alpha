# P0-8 + P0-9 · LLM provider 选型与连通性实测 / 许可证核对

> 阶段：P0 调研验证 · 任务 P0-8：LLM provider 选型与连通性实测（gemini 免费档 / openrouter 免费模型 / ollama 本地）  
> 阶段：P0 调研验证 · 任务 P0-9：许可证核对（OpenHer Apache-2.0 + Distilly MIT → 本仓库 Apache-2.0 + THIRD_PARTY_NOTICES）

---

## P0-8 · LLM provider 选型与连通性实测

### 连通性实测结果（2026-10-18）

| 目标 | 状态 | 响应时间 | 备注 |
|---|---|---|---|
| github.com | ✅ 200 OK | ~5.9s | 慢但可达，git 操作需重试 |
| openrouter.ai /api/v1/models | ✅ 200 OK | ~2.6s | **可用**，免费模型丰富 |
| generativelanguage.googleapis.com (gemini) | ❌ 超时 | 15s+ | 不可用，需代理或换 provider |
| ollama (127.0.0.1:11434) | ❌ 未安装/未运行 | — | 本地未部署 |

### LLM provider 候选对比

| Provider | 免费档 | 特点 | 适合场景 |
|---|---|---|---|
| **OpenRouter** | 有（多个免费模型） | 聚合众多模型，API 兼容 OpenAI 格式，连通性好 | **P1 推荐首选** |
| Gemini (Google) | 有免费档 | 需代理，本地连通性差 | 备选，需额外网络 |
| Ollama (本地) | 免费 | 需本地模型文件，ARM64 Termux 可跑轻量模型 | 长期考虑，需装 ollama + 模型 |
| DeepSeek (付费 API) | 付费 | 0.15$/M input, 0.6$/M output | 成本可控，但非免费 |

### OpenRouter 接入建议

OpenRouter 兼容 OpenAI ChatCompletions API，OpenHer 的 `providers/llm/client.py` 支持 openai provider，可直接通过 `base_url` 指向 OpenRouter：

```bash
# .env 示例
DEFAULT_PROVIDER=openai
DEFAULT_MODEL=openrouter/auto   # 或具体免费模型，如 openrouter/meta-llama/llama-3.1-8b-instruct:free
OPENAI_API_KEY=<openrouter_api_key>
OPENAI_BASE_URL=https://openrouter.ai/api/v1
```

### P1 LLM 选型结论

| 决策 | 内容 |
|---|---|
| 主推 provider | **OpenRouter**（免费模型 + OpenAI 兼容 + 连通性好）|
| 备选 | Gemini（需解决代理）|
| 本地模型 | Ollama（P1 可跳过，P6 可考虑）|
| 密钥管理 | `.env` 配置，`gitignore` 屏蔽，不入库 |

---

## P0-9 · 许可证核对

### 上游许可证

| 项目 | 仓库 | 许可证 | 角色 |
|---|---|---|---|
| OpenHer | kellyvv/OpenHer | Apache-2.0 | 后端人格引擎，vendor/OpenHer |
| Distilly（同事.skill） | titanwings/distilly @ dot-skill | MIT | 前端蒸馏流程，vendor/distilly |
| EverOS（原 EverMemOS） | EverMind-AI/EverOS | Apache-2.0 | 可选长期记忆后端，默认关闭 |

### 本仓库许可证

| 文件 | 内容 | 状态 |
|---|---|---|
| `LICENSE` | Apache-2.0（从 OpenHer 取原件） | ✅ 已提交 |
| `THIRD_PARTY_NOTICES.md` | 三方组件版权与兼容性说明 | ✅ 已提交 |
| `.gitignore` | 屏蔽 raw_chats、.env、*.sqlite 等隐私红线 | ✅ 已提交 |

### 许可证兼容性结论

本仓库采用 **Apache-2.0**，可同时覆盖 MIT（Distilly）与 Apache-2.0（OpenHer / EverOS）代码：
- MIT 组件：保留版权声明即可
- Apache-2.0 组件：须保留 LICENSE、NOTICE 及变更声明
- 上游代码引入时**不修改其 LICENSE / NOTICE 文件**

**反向不成立**：用 MIT 仓库承载 Apache-2.0 代码不可行，因为 Apache-2.0 附带 NOTICE 与专利条款不可剥离。

---

## P0 阶段总进度（10/10）

| 任务 | 内容 | 状态 |
|---|---|---|
| P0-1 | chat_agent 调用链 + SOUL/SHELL 注入点 | ✅ Done |
| P0-2 | genome_engine 25D→24D→8D 向量构成 | ✅ Done |
| P0-3 | style_memory / store / seeds.bin 风格种子 | ✅ Done |
| P0-4 | drive_metabolism 5 维驱力代谢 | ✅ Done |
| P0-5 | critic.py + signal_buckets.yaml | ✅ Done |
| P0-6 | soulmem.py + memory_store.py FTS5 + EverOS 降级 | ✅ Done |
| P0-7 | Termux 环境盘点（磁盘仅剩 555MB）| ✅ Done |
| P0-8 | LLM provider 选型（OpenRouter 首选） | ✅ Done |
| P0-9 | 许可证核对（Apache-2.0） | ✅ Done |

**P0 调研阶段全部完成，10/10 全部 Done。**

---

## 下一步：P3 桥接层（核心创新点）

P1（后端跑通）和 P2（前端跑通）需要 OpenAI-compatible LLM Key 才能真正跑通对话。如果你有免费 Key（如 OpenRouter），可以直接进入 P1；否则 P3 桥接层设计可以先不依赖实际 LLM 调用，纯基于代码规格进行。

建议下一步：**直接开始 P3 桥接层 `profile2soul.py` 数据模型 + 映射启发式 v1**，这是项目的核心创新点，不依赖 LLM Key 即可开工。

---

## 结论修订（2026-10-19）

**产品化定位：LLM provider 从"选一个"改为"多 provider + BYOK 双轨"。**

原结论是"P1 主推 OpenRouter 单一 provider"。产品面向 C 端用户后，推理成本与隐私需要分层，故修订为：

| 原结论 | 修订后 |
|---|---|
| 选 OpenRouter 一个 provider | **支持多 provider**（统一走 OpenAI 兼容接口），平台可切换 |
| 平台统一用免费模型跑通 | **双轨计费**：① 平台 Key（免费额度 / 付费订阅）；② **BYOK**（用户自带 Key，加密存储，免订阅）|

**连通性实测结论仍然有效**（github 慢、openrouter 通 ~2.6s、gemini 不可用、ollama 未装），作为 P1 选 provider 的基线。

**接入形态**：平台 Key 与 BYOK 都通过 OpenHer 的 OpenAI 兼容 provider 接入；产品侧需要一个 **Key 管理层**（加密存储用户 Key、按用户选择路由）。

**成本 / 隐私说明**：平台 Key 模式推理成本由平台承担 → 靠订阅或额度覆盖；BYOK 把成本转移给用户。二者并存。
