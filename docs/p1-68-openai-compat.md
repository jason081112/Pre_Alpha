# P1-68 OpenAI 兼容端点

对应看板 #68。目标：让 Alpha 既能被自有 Web/PWA 使用，也能被任意
OpenAI 兼容客户端（curl、`openai-python`、标准 SSE 客户端）调用。

## 范围

已实现：

- `POST /v1/chat/completions`
  - 非流式：返回 OpenAI `chat.completion`。
  - 流式：返回 OpenAI `chat.completion.chunk` SSE，以 `data: [DONE]` 结束。
- `GET /v1/models`
  - 返回 `alpha` 与当前加载的人格 ID，方便客户端发现模型。
- 认证、限流、配额、聊天记录、agent 状态落盘全部复用现有外壳。
- 与原有 `POST /api/chat`、`WS /ws/chat` 并存，不修改上游聊天协议。

不包含：

- OpenAI Responses API、Embeddings、Images、Audio。
- 工具调用 / function calling。
- 多模态输入真正进入图片或语音引擎；当前图片/音频 part 只作为占位文本。
- 跨请求固定 OpenAI `session_id`；多轮由服务端聊天记录恢复。

## 认证

所有 `/v1/*` 都受 `AuthMiddleware` 保护。OpenAI 客户端把 Alpha access token
填到 `api_key` 即可：

```bash
export ALPHA_TOKEN='<access_token>'
```

未认证返回 OpenAI 标准结构：

```json
{
  "error": {
    "message": "Invalid API key",
    "type": "invalid_request_error",
    "param": null,
    "code": "invalid_api_key"
  }
}
```

响应头带 `WWW-Authenticate: Bearer`。开发态可用
`ALPHA_AUTH_DISABLED=1` + `X-Alpha-User`，但生产必须保持认证开启。

## 请求映射

### messages

只取**最后一条 `role=user`** 的文本作为本轮输入。更早的 system、assistant、
user 消息不会重复传给引擎；服务端会按当前用户 + `client_id` 从聊天记录恢复
上下文。

`content` 支持：

- 字符串。
- OpenAI content parts 数组：`{"type":"text","text":"..."}` 正常拼接。
- `image_url` / `image`、`input_audio` / `audio` 只转为 `[图片]`、`[音频]`
  占位，不把二进制塞进 prompt。

没有 user 消息或最后一条 user 内容为空，返回 `400 invalid_messages`。

### model

`model` 同时承担人格与路由选择：

| `model` | 人格 | provider 路由 |
|---|---|---|
| 人格 ID，例如 `luna` | 该人格 | 账号已保存的主路由 |
| `provider/model`，例如 `openai/gpt-4o` | 默认人格 | 本次显式路由 + 账号回退链 |
| `alpha` / `default` / 空值 | 默认人格 | 账号已保存的主路由 |
| 其他模型名，例如 SDK 默认的 `gpt-4o-mini` | 默认人格 | 账号已保存的主路由 |

默认人格按以下顺序选择：

1. `ALPHA_OPENAI_DEFAULT_PERSONA`
2. `ALPHA_PERSONA`
3. `luna`
4. 当前加载的第一个人格

显式 `provider/model` 必须在当前用户可用的 provider 注册表中，且 Key
可用；自定义 provider 只允许当前用户自己的。未配置账号路由且没有显式
`provider/model` 时返回 `400 route_not_configured`。

### user / client_id

OpenAI 的 `user` 字段映射为聊天记录的 `client_id`：

- 未传时使用固定值 `openai`。
- 服务端会再叠加认证用户命名空间，所以两个用户即使用相同的 `user`，
  聊天记录也互相隔离。
- `client_id` 不是认证凭据；真正身份只来自 Bearer token。

## 非流式响应

```http
POST /v1/chat/completions
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "model": "luna",
  "user": "web-1",
  "messages": [
    {"role": "system", "content": "ignored"},
    {"role": "user", "content": "今天有点累"}
  ]
}
```

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1770000000,
  "model": "luna",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "那就先歇一会儿。"},
      "finish_reason": "stop"
    }
  ]
}
```

响应头包含：

- `X-Alpha-Session-Id`：本次引擎会话 ID。
- `X-Alpha-Route`：实际成功使用的 `provider/model`（如果路由层有记录）。

## 流式响应

请求加 `"stream": true`，响应为 `text/event-stream`：

```text
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"model":"luna","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"model":"luna","choices":[{"index":0,"delta":{"content":"那就"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"model":"luna","choices":[{"index":0,"delta":{"content":"先歇一会儿。"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"model":"luna","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

上游 `ChatAgent.chat_stream()` 输出的是原始标记流。外壳在流式过程中：

- 跳过 `__FEEL_DONE__` 控制标记。
- 只透出 `【最终回复】` / `[Final Reply]` 段。
- 丢弃 `【内心独白】`、`【表达方式】` 与 `[Expression Mode]`。
- 处理标记跨 chunk 切断的情况。
- 流结束后补 `_persist_agent`、聊天记录 `save_turn`，再发 `finish_reason=stop`
  与 `[DONE]`。

如果流已经开始后上游才报错，HTTP 状态码已经是 `200`，服务端会发送一条
OpenAI 错误结构的 SSE 事件，然后发送 `[DONE]`。如果错误发生在建会话阶段，
仍返回 JSON `500`。这是 SSE 协议的固有限制。

## 错误码

`/v1/*` 的错误体统一为：

```json
{
  "error": {
    "message": "...",
    "type": "invalid_request_error",
    "param": "messages",
    "code": "invalid_messages"
  }
}
```

| HTTP | code 示例 | 场景 |
|---:|---|---|
| 400 | `invalid_messages` / `route_not_configured` / `model_not_found` | 输入或模型路由不合法 |
| 401 | `invalid_api_key` | 未带 token、token 过期或无效 |
| 429 | `rate_limited` / `quota_exceeded` | RPM/RPD 超限或平台 token 配额用尽 |
| 500 | `provider_unavailable` | 建会话或整个回退链失败 |

429 带 `Retry-After`（整数秒）。RPM 指向当前分钟结束，RPD 与平台配额指向
下一个 UTC 零点。

## 路由与配额

- 正常请求使用账号保存的主路由 + 回退链。
- `model=provider/model` 时，本次显式路由作为主路由，账号已保存的主路由与
  回退链作为后续回退。
- 平台 token 配额按**本次实际主路由**检查：
  - 主路由是 `custom-*`（BYOK）：不扣平台额度。
  - 主路由是平台 provider：检查当日平台 token 用量。
- 限流中间件对 `/v1/chat/completions` 只做用户/IP 的 RPM/RPD；平台配额由
  端点在解析出实际主路由后检查，避免 `model` 覆盖路由时绕过配额。
- token 用量仍由 `RoutingLLMClient` 的 usage hook 记录；流式为字符估算，
  会标记 `estimated_requests`，不作为账单依据。

## curl 示例

非流式：

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $ALPHA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "luna",
    "user": "web-1",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

流式：

```bash
curl -N http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $ALPHA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "luna",
    "stream": true,
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

## openai-python 示例

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="<access_token>",
    max_retries=0,
)

reply = client.chat.completions.create(
    model="luna",
    user="web-1",
    messages=[{"role": "user", "content": "你好"}],
)
print(reply.choices[0].message.content)

stream = client.chat.completions.create(
    model="luna",
    user="web-1",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

## 服务端接线

```
AuthMiddleware          # /v1/* 认证；401 输出 OpenAI 错误结构
LimitsMiddleware        # /v1/chat/completions 的 RPM/RPD；429 输出 OpenAI 错误结构
AlphaMiddleware         # 外层路由 contextvar
FastAPI /v1 router      # 解析 model、检查配额、复用 ChatAgent
```

关键模块：

| 文件 | 职责 |
|---|---|
| `shell/openai_format.py` | 纯格式层：消息提取、model 别名、SSE 提取器、响应/错误结构 |
| `shell/openai_api.py` | `/v1/chat/completions`、`/v1/models`、错误处理器 |
| `shell/auth/middleware.py` | `/v1/*` 保护与 401 OpenAI 结构 |
| `shell/limits.py` | `/v1` RPM/RPD、`check_platform_quota`、429 OpenAI 结构 |
| `shell/app.py` | 路由挂载与 `/v1` 异常处理分支 |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `ALPHA_OPENAI_DEFAULT_PERSONA` | `luna` | 默认人格 ID |
| `ALPHA_PERSONA` | 空 | 默认人格的后备变量 |
| `ALPHA_ALLOW_PRIVATE_PROVIDERS` | 空 | 本机/局域网调试 provider 时设为 `1` |
| `ALPHA_AUTH_DISABLED` | 空 | 仅开发；生产不要开启 |

## 测试与验收

零依赖：

```bash
python3 tests/test_openai_compat.py
```

覆盖消息提取、content parts、model 别名、中文/英文标记、跨 chunk 标记、
sentinel、无标记回退、响应结构与错误结构。

真实 HTTP + 官方 SDK：

```bash
# 需要 fastapi uvicorn httpx pydantic openai
python3 scripts/dev/verify_openai_http.py
```

脚本启动临时 uvicorn，使用真实 `AuthMiddleware`、`LimitsMiddleware` 和
`/v1` router，验证：

- curl 等价非流式。
- 官方 `openai-python` 非流式。
- 官方 `openai-python` 流式。
- 原始 SSE 标准流式消费。
- `model=provider/model` 路由。
- 聊天记录与 agent 落盘。
- 401 / 429 / 500 / 400 的 OpenAI 错误结构。
- 平台配额按实际主路由检查。

GitHub Actions workflow：`openai-smoke`。原始结果见
[`data/p1-68-openai.json`](data/p1-68-openai.json)。

## 已知限制

- 流式情绪/动作标签没有逐 chunk 清洗；上游 REST 会统一清洗，流式只保证
  “不泄露内心独白与表达方式段”。
- 流式响应不含 `usage` 字段；真实用量由服务端审计。
- 工具调用、结构化输出、图片/语音多模态暂未接入。
- 显式 `model=provider/model` 可以选择当前用户可用的任意模型；如果产品
  需要严格白名单，应在 `_resolve_route` 前加订阅/套餐校验。
- 本端点不替代自有 Web/PWA 的 `/api/chat`；两者共享会话、状态与聊天记录。
