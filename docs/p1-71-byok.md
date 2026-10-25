# P1-71 BYOK Key 生命周期

- 日期：2026-10-24
- 结果：**21 / 21 PASS**；#70 回归 **18 / 18 PASS**
- 原始结果：[`docs/data/p1-71-byok.json`](data/p1-71-byok.json)
- 脚本：[`scripts/dev/verify_byok.py`](../scripts/dev/verify_byok.py)

## 生命周期

| 操作 | API | key_version | 密文 | 可用 |
|---|---|---:|---|---|
| 创建 | `POST /api/me/providers` | 1 | 写入 | 是 |
| 改元数据 | `PATCH /api/me/providers/{id}` | 不变 | 不变 | 是 |
| 轮换 Key | `PATCH` 带 `api_key` | +1 | 重写 | 是 |
| 撤销 | `POST /api/me/providers/{id}/revoke` | +1 | 清空 | 否 |
| 重新配置 | `PATCH` 带 `api_key` | +1 | 重写 | 是 |
| 删除 | `DELETE /api/me/providers/{id}` | 记录最后版本 | 删除行 | — |

## API

### 创建

```http
POST /api/me/providers
Content-Type: application/json
X-Alpha-User: alice
```

```json
{
  "label": "我的中转",
  "base_url": "https://relay.example.com/v1",
  "api_key": "sk-...",
  "models": ["gpt-x"],
  "probe": true
}
```

返回里只有末四位和版本，没有明文 Key：

```json
{
  "id": "custom-a1b2c3d4",
  "label": "我的中转",
  "base_url": "https://relay.example.com/v1",
  "models": ["gpt-x"],
  "caps": ["zh"],
  "has_key": true,
  "available": true,
  "custom": true,
  "key_version": 1,
  "key_last4": "c3d4",
  "revoked": false,
  "updated_at": 1792773500.0
}
```

### 改元数据

```http
PATCH /api/me/providers/custom-a1b2c3d4
```

```json
{"label": "新名字", "models": ["gpt-x", "gpt-y"]}
```

- 不传 `api_key` 时版本不变、Key 不动。
- 正在使用的模型不能从 `models` 里直接删掉，否则 `409`，先切路由。
- 传 `base_url` 会重新做 URL / DNS 校验；默认还会 probe。

### 轮换 Key

```http
PATCH /api/me/providers/custom-a1b2c3d4
```

```json
{"api_key": "sk-new-...", "probe": true}
```

- 旧密文被覆盖，`key_version + 1`，`key_last4` 更新。
- 默认先 probe；失败返回 `422`，旧 Key 保持不变。
- 轮换后路由无需重启；下一次请求按新 `key_version` 创建客户端。

### 撤销

```http
POST /api/me/providers/custom-a1b2c3d4/revoke
```

- `api_key_enc` 置空，`revoked_at` 写入，`key_version + 1`。
- 保留 provider 元数据，方便用户重新填 Key。
- 下一次 `/api/chat` 立即返回：

```json
{
  "error": "provider_unavailable",
  "action": "reconfigure_provider",
  "detail": "自定义 provider custom-a1b2c3d4 的 Key 已撤销，请重新配置"
}
```

撤销期间不能把它重新设为当前路由；重新 `PATCH` 一个 Key 即可恢复。

### 审计

```http
GET /api/me/providers/custom-a1b2c3d4/audit
```

```json
{
  "provider_id": "custom-a1b2c3d4",
  "events": [
    {"event": "created", "key_version": 1, "at": 1792773500.0},
    {"event": "updated", "key_version": 1, "at": 1792773501.0},
    {"event": "rotated", "key_version": 2, "at": 1792773502.0},
    {"event": "revoked", "key_version": 3, "at": 1792773503.0},
    {"event": "rotated", "key_version": 4, "at": 1792773504.0}
  ]
}
```

只记事件、版本、时间，不记 Key、不记 base_url。

### 删除

```http
DELETE /api/me/providers/custom-a1b2c3d4
```

仍在使用中的 provider 返回 `409`；先切换路由再删。删除后审计保留 `deleted` 事件。

## 存储

表 `user_provider` 新增：

| 列 | 作用 |
|---|---|
| `key_version` | 创建/轮换/撤销递增 |
| `key_last4` | 只存末四位；短 Key 存 `****` |
| `updated_at` | 最后修改时间 |
| `revoked_at` | 撤销时间，非空即不可用 |

旧库启动时自动 `ALTER TABLE` 补列，并尽量回填 `key_last4`；
`updated_at` 为空时回填为 `created_at`。迁移不要求重新填 Key。

表 `provider_audit` 记录 `created / updated / rotated / revoked / deleted`，
外键字段只存 `user_id`、`provider_id`、`event`、`key_version`、`at`。

## 安全边界

- 明文 Key 只存在于提交请求的那一瞬间和服务端解密后的内存里。
- 响应、列表、审计、`repr()` 都不包含明文或密文。
- 日志只记录请求路径与状态码；`/tmp/app.log` 已断言不含测试 Key。
- AAD 仍是 `user_id:provider_id`，密文不能跨行搬运。
- `X-Alpha-User` 仍可伪造，这是 #66 认证要解决的问题；上线前不能裸奔。

## 验收对照

| #71 验收 | 证据 |
|---|---|
| 数据库落盘无明文 Key | `ciphertext at rest`、`custom provider ok (16 tests)` |
| 撤销后下一次请求立即失败并提示重配 | `revoked chat returns actionable 409` |
| 日志 grep 不到完整 Key | `app log has no plaintext key` |
| 可替换 / 撤销 / 删除 | `rotate key increments version`、`revoke clears key`、`delete after switch` |
| 展示只返回末四位 | `list shows last4 only` |

## 复现

```bash
python scripts/dev/mock_provider.py
ALPHA_ALLOW_PRIVATE_PROVIDERS=1 python -m shell.app
python scripts/dev/verify_byok.py
```

## 已知限制

- probe 走 `{base_url}/models`；部分兼容服务不实现该接口，可传 `"probe": false`。
- 轮换不会主动清空 `RoutingLLMClient` 里旧的客户端缓存；缓存键包含 Key 哈希，旧条目不会再被命中。
- 撤销只影响新请求，已在进行中的流式请求不会被强杀。
- 认证接入前，用户隔离仅靠 `X-Alpha-User`，不能直接暴露公网。
