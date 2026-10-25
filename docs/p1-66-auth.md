# P1-66 认证与会话隔离

- 状态：本地实现、登录页与运行时双用户验收完成（Codespace，19/19）
- 登录方式：**邮箱 + 密码 + 强制邀请码**
- 手机号：已预留 `phone` 身份与密码字段，首版不开放；后续形态为手机号 + 密码
- 代码边界：认证逻辑全部在 `shell/auth/`，不修改 `vendor/OpenHer`

## 为什么先做邮箱 + 密码

- 邀请制内测只需要最小闭环，不依赖短信商、邮件商和 OAuth 审核。
- 公开仓库需要可自托管；邮箱 + 密码不绑定任何外部账号体系。
- 手机号后置时不需要改表：`auth_identity` 已经支持 `email` / `phone` 两种身份，手机号登录使用同一套密码和 token。

手机号首版不做的原因：

- 国内短信需要企业实名、签名和模板审核，容易卡外部流程。
- 海外与中国号码的短信商不同，需要按地区路由。
- 手机号是强 PII，需要加密、打码、验证码防刷和合规说明。

## 数据模型

| 表 | 用途 |
|---|---|
| `auth_user` | 账号主体、创建时间、停用标记 |
| `auth_identity` | `email` / `phone` 身份，密码哈希、归一化标识、唯一约束 |
| `auth_token` | access / refresh token 的 SHA-256 哈希、过期、撤销、轮换父子关系 |
| `auth_invite` | 邀请码哈希、次数、过期、撤销、最近使用时间 |

密码：`scrypt`，参数写入哈希串，方便未来升级。
Token：随机 32 字节，数据库只存哈希；access 默认 15 分钟，refresh 默认 30 天。
邀请码：数据库只存哈希，明文只在生成时打印一次。

## API

### 注册

```http
POST /api/auth/register
Content-Type: application/json

{
  "email": "alice@example.com",
  "password": "至少 8 位",
  "invite_code": "ALPHA-XXXXX-XXXXX"
}
```

- `201`：返回 access / refresh / user。
- `403`：缺少、无效、过期或用完的邀请码。
- `409`：邮箱已注册。
- `422`：邮箱格式或密码长度不合法。

### 登录

```http
POST /api/auth/login
```

```json
{"email": "alice@example.com", "password": "..."}
```

失败统一 `401`，不区分“邮箱不存在”和“密码错误”。

### 刷新

```http
POST /api/auth/refresh
```

```json
{"refresh_token": "..."}
```

- 刷新会轮换 refresh token，旧 token 立即失效。
- access token 过期后客户端用 refresh 换新，不需要重新登录。
- 旧 refresh 重放返回 `401`。

### 当前用户

```http
GET /api/auth/me
Authorization: Bearer <access_token>
```

### 退出

```http
POST /api/auth/logout
Authorization: Bearer <access_token>

{"refresh_token": "..."}
```

同时撤销传入的 access 和 refresh；接口幂等。

### 登录页

```http
GET /login
```

- 公开页面，不依赖认证；内联 HTML/CSS/JS，不需要前端构建。
- 登录成功后页面显示 `OK` 和邮箱；刷新时会先用 `/api/auth/me` 校验，access 过期则自动 refresh。
- 同页带折叠的邀请码注册表单，方便内测冒烟。
- Token 暂存 `localStorage`；正式聊天页接入时应改为 HttpOnly cookie + CSRF 防护。
- 页面不接聊天 UI，也不改 `vendor/OpenHer`。

## 会话与历史隔离

上游有两个不能直接信任的入口：

1. `active_sessions` 只按 `session_id` 查找，不校验用户。
2. `chat_log_store` 只按前端 `client_id` 分区。

外壳在 `shell/auth/scope.py` 做命名空间映射：

- 认证用户的 session id 格式：`u<user_hash>.<8位hex>`。
- 非空 session id 必须属于当前用户，否则按“会话不存在”处理。
- 前端 `client_id` 进入上游前映射为 `u<user_hash>:<client_id>`。
- `ScopedChatLog` 代理 `save_turn` / `save_message` / `load_messages` / `count_messages`。
- 上游 Agent 的 `user_id` 固定为认证用户 id，因此 `state_store`、`AgentExtraStore`、关系 EMA 都按用户隔离。
- 不再把客户端传入的 `user_name` 当身份。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `ALPHA_AUTH_DB` | `.data/auth.db` | 认证数据库 |
| `ALPHA_REQUIRE_INVITE` | `1` | 注册是否强制邀请码 |
| `ALPHA_ACCESS_TTL` | `900` | access token 秒数 |
| `ALPHA_REFRESH_TTL` | `2592000` | refresh token 秒数 |
| `ALPHA_AUTH_DISABLED` | 空 | 仅本地回归用；设为 `1` 时跳过认证，继续信任 `X-Alpha-User` |

> 生产环境不要设置 `ALPHA_AUTH_DISABLED=1`。旧的 `verify_routing.py` / `verify_byok.py` / `verify_state.py` 仍使用 `X-Alpha-User`，需要临时以 dev 模式启动。

## 邀请码

```bash
python scripts/dev/make_invite.py --label alpha --uses 5 --days 14
python scripts/dev/make_invite.py --revoke ALPHA-XXXXX-XXXXX
```

默认数据库与 app 一致：`ALPHA_AUTH_DB` 或 `.data/auth.db`。

## 验收

本地零依赖：

```bash
python3 tests/test_auth.py
python3 tests/test_smoke.py
python3 tests/test_routing.py
python3 tests/test_custom_provider.py
python3 tests/test_agent_state.py
```

运行时（需要 Codespace 或可用服务）：

```bash
# 服务端
ALPHA_REQUIRE_INVITE=1 ALPHA_ALLOW_PRIVATE_PROVIDERS=1 python -m shell.app

# 客户端
ALPHA_INVITE_CODE=ALPHA-XXXXX-XXXXX python scripts/dev/verify_auth.py
```

2026-10-25 Codespace 实跑结果：**19 PASS，0 FAIL，0 SKIP**，原始结果见
`docs/data/p1-66-auth.json`。聊天隔离部分为每个测试用户单独配置了
`127.0.0.1:9999` 的 mock provider。

运行时脚本覆盖：

- 未认证 `/api/me/routing` 返回 401
- 两个用户注册、登录、access 鉴权
- refresh 轮换、旧 refresh 拒绝、logout
- 两用户并发聊天
- 跨用户 session status 返回 404
- 历史记录互相不可见
- `chat_messages.client_id` 已带用户命名空间
- `openher.db` 与 `agent_extra.db` 的 user_id 是认证用户 id

## 已知边界

- 首版不做邮箱验证、找回密码、邮箱变更；邀请码就是准入门槛。
- 账号停用有数据库字段和 `403` 分支，但还没有管理 API。
- 邀请码没有管理 API，先用脚本。
- OAuth、手机号 OTP、手机号 + 密码、Passkey 后置。
- `/api/selfie/*`、`/api/avatar/*` 仍是公开静态资源；如果后续出现用户私有生成内容，需要再改成签名 URL。
