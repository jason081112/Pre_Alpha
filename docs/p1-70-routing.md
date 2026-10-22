# P1-70 运行期路由与回退链

对应看板 #70。目标：不重启、不改上游核心，让每个请求按用户选择走不同 provider/model。

## 结构

```
shell/
├── app.py           # 入口：import 上游 main.app，挂中间件与 API
├── api.py           # GET/PUT /api/me/routing、GET /api/routes/catalog
├── providers.json   # provider 注册表（base_url / models / key_env）
├── providers.py     # 注册表加载与校验
├── routing.py       # contextvar 路由 + 回退执行器
└── store.py         # user_routing（SQLite）
```

上游 `vendor/OpenHer` 零改动。

## 产品语义

- 没有「默认模型」，只有一条持久化的当前模型
- 用户未配置时，`POST /api/chat` 返回 `409 route_not_configured`
- 回退链默认空，由用户配置，最多 4 跳
- 切换立即写入，退出重进、换设备、开新会话都保持

## 运行

```bash
# 仓库根目录
python -m shell.app
# 或
uvicorn shell.app:app --host 0.0.0.0 --port 8000
```

上游 startup 会先按 `.env` 创建一次默认客户端，随后被 `RoutingLLMClient` 替换。
因此 `.env` 里至少要有一个可用 Key（否则上游 startup 会先失败）。

环境变量：

| 变量 | 说明 |
|---|---|
| `ALPHA_ROUTING_DB` | 路由库路径，默认 `.data/routing.db` |
| `ALPHA_DEV_ROUTE` | 开发态兜底路由，如 `ollama-cloud/gemma4:31b` |
| `ALPHA_PROVIDERS_FILE` | 自定义注册表路径 |

## 开发态设置

```bash
alpha route set --provider ollama-cloud --model gemma4:31b \
  --fallback "openai/gpt-4o,deepseek/deepseek-chat"
alpha route show
```

多用户用 `--user` 区分；HTTP 侧用 `X-Alpha-User` 头（#66 接入认证后替换）。

## API

### GET `/api/routes/catalog`

返回 provider、模型、能力标签、Key 是否就绪。

### GET `/api/me/routing`

```json
{
  "user_id": "local",
  "configured": true,
  "route": {"provider": "ollama-cloud", "model": "gemma4:31b"},
  "route_key": "ollama-cloud/gemma4:31b",
  "fallback": [{"provider": "openai", "model": "gpt-4o"}],
  "updated_at": 1792598xxx
}
```

未配置时 `configured: false`。

### PUT `/api/me/routing`

```json
{
  "provider": "ollama-cloud",
  "model": "gemma4:31b",
  "fallback": [{"provider": "openai", "model": "gpt-4o"}]
}
```

校验：provider/模型存在、回退链 ≤4、去重、不含当前模型。

## 回退规则

| 情况 | 行为 |
|---|---|
| 超时 / 429 / 5xx / 网络错误 | 换下一跳 |
| 400 / 401 / 403 / 404 / 422 | 不换，直接返回可读错误 |
| 缺 Key（ValueError） | 不换 |
| 流式已吐出首个 token | 不换，抛错 |

每次请求的尝试记录（仅失败跳转）通过响应字段返回：

```json
{
  "route_used": "openai/gpt-4o",
  "fallback_attempts": [
    {"provider": "ollama-cloud", "model": "gemma4:31b", "ok": false,
     "latency_ms": 60012, "error": "TimeoutError: "}
  ],
  "latency_ms": 64310
}
```

响应头同时带 `X-Alpha-Route`。

## 用户自定义 provider

除了平台清单，用户可以用任意 OpenAI 兼容端点：

```bash
curl -X POST http://localhost:8000/api/me/providers \
  -H 'Content-Type: application/json' \
  -H 'X-Alpha-User: local' \
  -d '{
    "label": "我的中转",
    "base_url": "https://relay.example.com/v1",
    "api_key": "sk-xxx",
    "models": ["gpt-x"]
  }'
```

返回 `id`（形如 `custom-a1b2c3d4`），之后就能像平台模型一样写入路由：

```bash
curl -X PUT http://localhost:8000/api/me/routing \
  -H 'Content-Type: application/json' \
  -d '{"provider":"custom-a1b2c3d4","model":"gpt-x","fallback":[]}'
```

### 存储与加密

表 `user_provider`，Key 用 AES-GCM 加密后落库：

- 主密钥来自 `ALPHA_MASTER_KEY`（64 位 hex / base64 32B / 任意口令派生）
- 未设置时自动生成 `.data/master.key`（0600）
- 列表接口只返回 `has_key`，永远不回显 Key

### 安全规则

- 只接受 http/https；非本机地址必须 https
- 拒绝 URL 内嵌账号密码
- 拒绝字面量内网 IP；创建时做 DNS 解析，域名指向内网直接拒绝
- 本机地址（localhost / 127.0.0.1 / ::1）放行，方便本地 Ollama
- 内网调试可设 `ALPHA_ALLOW_PRIVATE_PROVIDERS=1`

### 连通性检查

创建时默认请求 `{base_url}/models`：

- 401/403 → 拒绝保存
- 其他状态码 → 允许保存（部分服务不实现 /models）
- 可传 `"probe": false` 跳过

## 测试

零依赖，可直接在 Termux 跑：

```bash
python3 tests/test_routing.py          # 11 项
python3 tests/test_custom_provider.py  # 6 项
python3 tests/test_smoke.py
```

覆盖：路由解析、注册表校验、SQLite 读写、错误分类、超时降级、
401 不降级、全失败、首 token 前降级、吐字后不降级、并发隔离、
自定义 provider 加密存取、用户隔离、URL/SSRF 校验。

## 已知限制

- 认证尚未接入（#66），当前用 `X-Alpha-User` 头
- 平台模型清单是静态的，后续可加 `/models` 自动刷新
- 配额与计费未接（#67）
- 上游 startup 需要至少一个可用的默认 Key
- 自定义 provider 目前只支持 OpenAI 兼容协议
