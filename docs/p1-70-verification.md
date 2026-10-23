# P1-70 运行时集成验证

- 日期：2026-10-24
- 环境：GitHub Codespace；Ollama Cloud `gemma4:31b` + 本地 OpenAI 兼容 mock
- 结果：**18 / 18 PASS**
- 原始结果：[`docs/data/p1-70-verification.json`](data/p1-70-verification.json)
- 脚本：[`scripts/dev/verify_routing.py`](../scripts/dev/verify_routing.py)

## 覆盖

| # | 检查 | 结果 | 证据 |
|---|---|---|---|
| 1 | `/api/status` | PASS | 200 |
| 2 | 角色加载 | PASS | 10 个 persona |
| 3 | 未选模型 | PASS | 409 `route_not_configured` + `open_model_picker` |
| 4 | 创建自定义 provider | PASS | 200 |
| 5 | 目录合并 | PASS | 平台 7 个 + 自定义 1 个 |
| 6 | Key 不回显 | PASS | 列表只有 `has_key` |
| 7 | Key 静态加密 | PASS | 库里是 `v1:` 密文 |
| 8 | 切换自定义路由 | PASS | 200 |
| 9 | 自定义 provider 对话 + usage | PASS | `custom-*/mock-ok`，15 tokens |
| 10 | 回复内容 | PASS | `mock reply` |
| 11 | 切回 Ollama | PASS | 200 |
| 12 | Ollama 对话 | PASS | `ollama-cloud/gemma4:31b` |
| 13 | 500 + 回退链 | PASS | 200 |
| 14 | 500 触发回退 | PASS | 失败跳记录 500，最终 Ollama |
| 15 | 连接失败触发回退 | PASS | `APIConnectionError` → Ollama |
| 16 | 首 token 后不重试 | PASS | 收到 `mock ` 后报错，仅 1 次尝试 |
| 17 | 使用中的 provider 删除保护 | PASS | 409 |
| 18 | 切换后删除 | PASS | 200 `removed: true` |

## 关键结论

- 路由键统一是 **`provider/model`**，不是 `provider:model`。
- 自定义 Key 以 AES-GCM 密文落库；列表接口不回显，删除后不可恢复。
- 500 与连接错误都会按回退链切换；失败的跳会出现在 `fallback_attempts`。
- 流式首 token 吐出后，即使连接被 RST 中断，也**不会**切到下一跳。
- 删除正在使用的 provider 返回 409；切走后可删。

## 发现

1. **上游 SDK 自带重试**：`AsyncOpenAI` 默认 `max_retries=2`。连接失败检查耗时约 1.3s，说明 SDK 可能内部重试了两次。路由层的“首 token 后不重试”仍然成立，但“连接失败只尝试一次”并不成立。建议显式设 `max_retries=0`，把重试策略完全收回到 `shell/routing.py`，避免和外层回退叠加。
2. `mock-cut` 直接 `close()` 时，OpenAI SDK 把 EOF 当成正常结束；必须用 `SO_LINGER` 强制 RST 才能测到“首 token 后报错”。mock 已改为 RST。
3. 验证脚本原先硬编码了 `/workspaces/Pre_Alpha`；已改为从脚本位置推导仓库根，并支持 `ALPHA_BASE` / `ALPHA_DATA_DIR` / `ALPHA_ROUTE_MODEL`。

## 复现

```bash
# 终端 1
python scripts/dev/mock_provider.py

# 终端 2（仓库根目录）
python -m shell.app

# 终端 3
python scripts/dev/verify_routing.py
```

默认输出 `/tmp/verify-routing.json`，可用 `ALPHA_VERIFY_OUT` 改路径。
