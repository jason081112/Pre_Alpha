# P1-67 限流、平台配额与 token 审计

对应看板 #67。目标：防止单用户刷爆平台 Key，同时给后续订阅额度留接口；
BYOK 只做防滥用限流，不扣平台额度。

## 结构

```
shell/
├── limits.py        # 计数、配置、配额、探活限制、HTTP/WS 中间件
├── limits_api.py    # 管理接口：配置热更新 + 用量审计查询
├── api.py           # provider 创建/更新探活接入限流
├── routing.py       # 平台/BYOK 实际 token 用量回调
└── app.py           # 中间件顺序、异常处理、WS 路由上下文
```

限流数据默认落在 `.data/limits.db`，与路由库、认证库分开。
只存计数与 token 数量，不存消息原文。

## 限流维度

每个受限请求同时检查：

| 维度 | 默认 RPM | 默认 RPD | 说明 |
|---|---:|---:|---|
| 用户 | 60 | 2000 | 已登录请求；按可信 `user_id` |
| IP | 120 | 5000 | 未登录请求只按 IP；已登录也同时检查 |

- 固定窗口：RPM 按自然分钟，RPD 按 UTC 自然日。
- 任一维度超限即拒绝，响应 `429`，带整数秒 `Retry-After`。
- RPD 的 `Retry-After` 指向下一个 UTC 零点；RPM 指向当前分钟结束。
- 检查全部通过后才统一扣减，避免被拒绝的请求误扣其他维度。
- 配置为 `0` 表示关闭该维度。

默认受限路径：

- `POST /api/chat`
- `GET /api/tts`、`POST /api/image`
- `POST /api/demo/inject`
- `POST /api/auth/login`、`POST /api/auth/register`、`POST /api/auth/refresh`
- `WS /ws/chat`：按每条 `{"type":"chat", ...}` 消息检查，不是只检查握手

鉴权中间件在限流中间件外层：受保护路径先认证，再限流；登录/注册/刷新
是公共路径，按 IP 限流。

## HTTP 429

```json
{
  "error": "rate_limited",
  "code": "rate_limited",
  "detail": "请求太快，请稍后再试",
  "action": "wait",
  "retry_after": 17
}
```

响应头：

```http
Retry-After: 17
X-Alpha-Error: rate_limited
Content-Type: application/json; charset=utf-8
```

平台额度用尽时 `code` 为 `quota_exceeded`，`action` 为 `wait_or_byok`，
提示切换到自有 Key 或等 UTC 零点。

## WebSocket 提示

WebSocket 不会只丢消息。超限时服务端先发送：

```json
{
  "type": "error",
  "error": "rate_limited",
  "code": "rate_limited",
  "detail": "请求太快，请稍后再试",
  "action": "wait",
  "retry_after": 17
}
```

然后继续等待下一条消息；客户端可读提示并稍后重发。
平台额度用尽时同样返回 `quota_exceeded`。

## 平台 Key 与 BYOK

`token_usage` 表按 `kind` 分开：

- `platform`：平台注册表 provider，受 `platform_daily_tokens` 限制。
- `byok`：`custom-*` provider，只做 RPM/RPD 防滥用，不扣平台额度。

配额按**主路由**判定：

- 主路由是平台 provider：请求前检查当日平台 token 用量。
- 主路由是 BYOK：不检查平台额度，即使回退链里有平台 provider。

用量写入时机：

- 非流式：优先使用 provider 返回的 `usage`；缺失时按字符数估算并标记。
- 流式：上游只吐文本，没有 usage；按输入/输出字符数估算，
  `estimated_requests` 会加一，不伪装成精确值。
- 只写 token 数量、provider、model、日期、请求数；不写消息内容。

审计表核心字段：

```text
user_id, day, kind, provider, model,
prompt_tokens, completion_tokens, total_tokens,
requests, estimated_requests, updated_at
```

## 配置与热更新

环境变量是默认值；管理接口写入 SQLite 覆盖层，不重启生效。

| 变量 | 默认 | 说明 |
|---|---:|---|
| `ALPHA_LIMITS_DB` | `.data/limits.db` | 限流/审计库 |
| `ALPHA_USER_RPM` | 60 | 每用户每分钟请求 |
| `ALPHA_USER_RPD` | 2000 | 每用户每日请求 |
| `ALPHA_IP_RPM` | 120 | 每 IP 每分钟请求 |
| `ALPHA_IP_RPD` | 5000 | 每 IP 每日请求 |
| `ALPHA_PLATFORM_DAILY_TOKENS` | 200000 | 每用户每日平台 token |
| `ALPHA_PROBE_PER_HOUR` | 10 | provider 探活每小时次数 |
| `ALPHA_PROBE_CONCURRENCY` | 2 | 每用户探活并发 |
| `ALPHA_PROBE_BACKOFF_BASE` | 2 | 失败退避起始秒数 |
| `ALPHA_PROBE_BACKOFF_MAX` | 300 | 失败退避上限秒数 |
| `ALPHA_TRUST_PROXY` | 空 | `1/true/yes/on` 时信任 `X-Forwarded-For` 第一跳 |
| `ALPHA_ADMIN_TOKEN` | 空 | 管理接口令牌；未设置时管理接口返回 503 |

配置覆盖层在多进程下最多延迟 2 秒；同一进程更新后立即生效。

```bash
# 读取
curl http://localhost:8000/api/admin/limits \
  -H "X-Alpha-Admin-Token: $ALPHA_ADMIN_TOKEN"

# 热更新：用户 30 RPM、每日 500 次，平台每日 50000 token
curl -X PUT http://localhost:8000/api/admin/limits \
  -H "X-Alpha-Admin-Token: $ALPHA_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "user_rpm": 30,
    "user_rpd": 500,
    "platform_daily_tokens": 50000
  }'
```

## 审计查询

```bash
# 某个用户今天
curl "http://localhost:8000/api/admin/usage?user_id=alice" \
  -H "X-Alpha-Admin-Token: $ALPHA_ADMIN_TOKEN"

# 某天全部用户（最多 5000 行）
curl "http://localhost:8000/api/admin/usage?day=2026-10-25&limit=1000" \
  -H "X-Alpha-Admin-Token: $ALPHA_ADMIN_TOKEN"
```

指定 `user_id` 时返回 `totals`，按 `platform` / `byok` 分组；
不指定时只返回明细行。明细行不含原文。

## provider 探活限制

`POST /api/me/providers` 与 `PATCH /api/me/providers/{id}` 的默认探活
（请求 `{base_url}/models`）额外受以下限制：

- 每用户、每 IP 每小时 `probe_per_hour` 次；默认 10 次。
- 每用户同时最多 `probe_concurrency` 个探活；默认 2 个；超限 429。
- 连接失败、重定向、非鉴权失败会累计失败次数，指数退避；
  退避期间直接 429，不再发出站请求。
- 401/403 说明目标可达，只拒绝保存，不进入退避。
- 探活仍复用 #81 的 `plan_outbound()`：只打公网、固定 IP、不跟随重定向。

退避键为 `user_id + host + port`，不是全局目标；A 用户失败不会影响 B 用户。

## 中间件顺序

```
AuthMiddleware          # 认证、注入可信 user_id / x-alpha-user
LimitsMiddleware        # HTTP 请求 + WS 每条 chat 消息
AlphaMiddleware         # 路由 contextvar、/api/chat 可观测字段
上游 FastAPI app
```

`LimitsMiddleware` 在 `AuthMiddleware` 内层，才能拿到可信用户；
在 `AlphaMiddleware` 外层，才能在进入 LLM 前拒绝。
`AlphaMiddleware` 现在也给 `/ws/chat` 建立路由 context，流式调用才能走
`RoutingLLMClient` 并记录 BYOK/平台用量。

## 测试

零依赖：

```bash
python3 tests/test_limits.py   # 12 项
python3 tests/test_routing.py  # 15 项（含用量回调/流式估算）
```

覆盖：配置校验与跨实例热读、固定窗口、用户/IP 双维度、匿名 IP 限流、
平台配额、BYOK 分离、审计字段、探活小时限流/并发/退避、HTTP 429、
WS 逐消息提示、`Retry-After`、代理头信任边界。

真实 HTTP transport：

```bash
# 需要 fastapi uvicorn httpx pydantic
python3 scripts/dev/verify_limits_http.py
```

该脚本启动临时 uvicorn，验证 IP/用户 429、`Retry-After`、6 路并发压测、
配置热更新、平台配额、BYOK 放行和审计查询。GitHub Actions workflow：
`limits-smoke`。

原始结果与 CI run：
[`data/p1-67-limits.json`](data/p1-67-limits.json)。

## 已知边界

- 平台 token 配额是软配额：并发请求可能在额度边缘多放行一轮。
- 流式 token 是字符估算，只用于防滥用和趋势审计，不作为账单依据。
- 探活并发计数是单进程内存；多 worker 时实际并发是 `workers × 配置`。
- 限流计数和配置在 SQLite 中跨进程共享；配置覆盖层有最多 2 秒缓存。
- 只有部署在可信反向代理后才开 `ALPHA_TRUST_PROXY=1`；否则客户端可伪造
  `X-Forwarded-For` 绕过 IP 限流。
- 管理接口目前用静态 `ALPHA_ADMIN_TOKEN`，不是用户角色系统；正式控制面
  应换成管理员账号 + 审计。
- 429 不会自动重试；客户端应按 `Retry-After` 退避。
