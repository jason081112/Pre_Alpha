# Refine API 契约（v1）

托管精修接口。**只接受结构化人格，不接受原始聊天记录。**

## 请求

```
POST /v1/persona/refine
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "schema_version": "1.0",
  "persona": { "...": "符合 persona.schema.json" },
  "options": {
    "language": "zh",
    "intensity": "balanced"
  }
}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | 固定 `"1.0"` |
| `persona` | object | 见 `persona.schema.json` |
| `options.language` | string | `zh` / `en`，默认 `zh` |
| `options.intensity` | string | `soft` / `balanced` / `vivid`，默认 `balanced` |

## 禁止字段

请求体任何层级出现以下字段，直接 `422 raw_text_not_allowed`：

- `raw_text`
- `messages`
- `chat_log`
- `chat_history`
- `transcript`
- `conversation`
- `content`（顶层）

服务端不记录被拒绝的请求体。

## 响应

```json
{
  "schema_version": "1.0",
  "persona": { "...": "精修后的人格" },
  "quality": {
    "fidelity": 0.78,
    "consistency": 0.81,
    "coverage": 0.66
  },
  "changes": [
    {"field": "speech_style.tone", "reason": "统一语气"}
  ],
  "usage": {
    "prompt_tokens": 1200,
    "completion_tokens": 800,
    "total_tokens": 2000
  }
}
```

## 隐私

- 默认处理完即弃，不落盘、不训练。
- 开启云同步时，只存精修后的人格，不存原文。
- 日志只记录 request id、耗时、token 数、错误码，不记录人格内容。
- 删除账号时，精修结果一并删除。

## 错误

| 状态码 | code | 说明 |
|---|---|---|
| 400 | `invalid_schema` | 不符合 schema |
| 401 | `unauthorized` | 缺少或无效 token |
| 422 | `raw_text_not_allowed` | 出现禁止字段 |
| 429 | `rate_limited` | 超出配额，带 `Retry-After` |
| 500 | `refine_failed` | 内部错误，可重试 |

## 版本

- `schema_version` 与接口版本独立演进。
- 破坏性变更走 `/v2/`，旧版本至少保留 6 个月。
