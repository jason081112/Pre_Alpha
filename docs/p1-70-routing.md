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
| `ALPHA_ALLOW_PRIVATE_PROVIDERS` | `1/true/yes/on` 时允许本机/局域网 provider（调试用） |
| `ALPHA_LIMITS_DB` | 限流/用量审计库，默认 `.data/limits.db`，见 [`p1-67-limits.md`](p1-67-limits.md) |
| `ALPHA_ADMIN_TOKEN` | 限流配置/审计管理接口令牌 |

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
- 列表接口只返回 `key_last4`（末四位）与 `has_key`，永远不回显 Key
- 轮换 Key 走 `PATCH`，`key_version` 递增；撤销走 `/revoke`，密文清空
- 生命周期与审计细节见 [`p1-71-byok.md`](p1-71-byok.md)

### 轮换、撤销与审计

```bash
# 轮换 Key（默认先 probe，失败不覆盖旧 Key）
curl -X PATCH http://localhost:8000/api/me/providers/custom-a1b2c3d4 \
  -H 'Content-Type: application/json' \
  -d '{"api_key":"sk-new-xxx","probe":true}'

# 撤销：清空密文，保留 provider，下一次对话返回 409
curl -X POST http://localhost:8000/api/me/providers/custom-a1b2c3d4/revoke

# 审计：只有事件与版本，没有 Key
curl http://localhost:8000/api/me/providers/custom-a1b2c3d4/audit
```

撤销后 `/api/chat` 返回 `provider_unavailable` + `reconfigure_provider`，
不会退化成裸 500，也不会静默回退到别的 provider。

完整字段、迁移和验收见 [`p1-71-byok.md`](p1-71-byok.md)。

### 出站安全规则（#81）

完整实现与测试见 [`p1-81-outbound.md`](p1-81-outbound.md)。

- 只接受 http/https；非本机地址必须 https
- 拒绝 URL 内嵌账号密码
- **默认拒绝所有非公网地址**：环回、私网、链路本地、保留、组播、未指定地址
- 本机 Ollama / 局域网调试必须在启动服务时设 `ALPHA_ALLOW_PRIVATE_PROVIDERS=1`
- 创建、更新、探活、真实请求共用同一套 `plan_outbound()`：
  - 请求前解析主机名，并把连接目标固定到解析出的 IP；
  - 后续请求不再依赖 DNS，DNS rebinding 改不了连接目标；
  - DNS 同时返回公网和私网地址时整体拒绝；
  - HTTPS 仍用原始主机名做 SNI 和证书校验；
- 不跟随 3xx 重定向，避免被带到内网；
- 解析失败、私网拒绝等错误只返回通用原因，不回显内网 IP。

> 这层只解决“出站地址不可信”，不改变“服务端代理会持有明文 Key/请求”的事实；
> 真正的“平台看不到”需要客户端直连或 TEE。

### 连通性检查

创建时默认请求 `{base_url}/models`，轮换 Key 时同样执行；探活与真实请求共用
同一套出站策略：

- 401/403 → 拒绝保存
- 3xx 重定向 → 拒绝，不跟随
- 私网/环回/链路本地/保留地址 → 拒绝
- 其他状态码 → 允许保存（部分服务不实现 /models）
- 可传 `"probe": false` 跳过

本机 mock 验收时要同时设置：

```bash
ALPHA_ALLOW_PRIVATE_PROVIDERS=1 python -m shell.app
```

否则 `http://127.0.0.1:9999/v1` 会被默认策略拒绝。

### 威胁模型与边界

这套实现是**服务端密钥库 + 代理**，不是浏览器端 BYOK：

- 浏览器只在提交表单的那一秒持有明文 Key，之后不再接触
- 长期 Key 不落 `localStorage` / `IndexedDB`，所以 XSS 偷不到“长期凭据”
- 解密只发生在服务端进程内，Key 永不回传前端

XSS 仍然能做的事：在用户提交表单时挂钩 `fetch` / 输入框，截获**当次**明文；
或者劫持会话直接调用我们的 API。后者是当前最大风险——认证还没接（#66），
`X-Alpha-User` 可伪造，上线前必须替换。CSP、Trusted Types、依赖审计随 #80 一起做。

静态加密的边界：

- 防的是 **DB 文件或备份被单独拿走**（SQL 注入、备份泄露、误提交）
- 防不住整机沦陷：主密钥在同一台机器上时，攻击者两样都能拿
- 生产环境设 `ALPHA_REQUIRE_MASTER_KEY=1`，强制用环境变量注入主密钥，
  且主密钥不要和 DB 备份放在一起
- 口令型主密钥走 **scrypt + 随机 salt**，不是裸 SHA-256
- nonce 每次加密随机 96-bit；AAD 绑定 `user_id:provider_id`，
  密文被搬到别的行会解密失败
- 同一主密钥建议在 2^32 次加密前轮换；当前量级远低于该阈值

残余风险：DNS rebinding（校验时解析到公网、真正请求时解析到内网）。
缓解手段是出站防火墙或固定出口；当前版本未做，记为已知限制。

## 测试

零依赖，可直接在 Termux 跑：

```bash
python3 tests/test_routing.py          # 15 项
python3 tests/test_limits.py           # 12 项（限流/配额）
python3 tests/test_custom_provider.py  # 16 项
python3 tests/test_smoke.py
```

覆盖：路由解析、注册表校验、SQLite 读写、错误分类、超时降级、
401 不降级、全失败、首 token 前降级、吐字后不降级、并发隔离、
自定义 provider 加密存取、用户隔离、URL/SSRF 校验。

## 已知限制

- 认证已接入（#66）；仅 `ALPHA_AUTH_DISABLED=1` 开发模式允许伪造 `X-Alpha-User`
- 平台模型清单是静态的，后续可加 `/models` 自动刷新
- 限流与平台 token 配额已接（#67）；真实计费仍未接
- 上游 startup 需要至少一个可用的默认 Key
- 自定义 provider 目前只支持 OpenAI 兼容协议
- 出站已固定 DNS 解析结果、拒绝私网并禁止重定向（#81）；残余风险见
  [`p1-81-outbound.md`](p1-81-outbound.md)
- 主密钥轮换 / 全量重新加密未实现
