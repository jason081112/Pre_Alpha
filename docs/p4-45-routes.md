# P4-45 · OpenHer 路由清单

`vendor/OpenHer/main.py`（1573 行）共 **17 个路由**。本文是可对接的契约，不是源码复述。

## 一、页面（4）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 主页（`include_in_schema=False`） |
| GET | `/app` | 应用页 |
| GET | `/discover` | 发现页 |
| GET | `/chat/{path:path}` | 聊天页（catch-all，前端路由用） |

这四个都是 `FileResponse` / `HTMLResponse`，托管上游自带的 Web 前端。我们做自己的
Web 页（#46）时不需要它们，但保留可作对照。

## 二、状态与人格（4）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/status` | — | `{name, version, engine, status, personas[], active_sessions}` |
| GET | `/api/personas` | — | `{personas: [{persona_id, name, name_zh, age, gender, mbti, tags, tags_zh, description, avatar_url, has_front, has_awakening_video}]}` |
| GET | `/api/persona/{persona_id}/media/{media_type}` | `media_type`: front / awakened / awakening | 文件流 |
| GET | `/api/avatar/{persona_id}` | — | `FileResponse` PNG |

`description` 取 `bio.zh` / `bio.en` / `personality` 的**第一句**截断到 120 字；
`avatar_url` 仅当 `personas/<id>/avatar.png` 存在时非空。

**`/api/personas` 只有 GET，没有创建/编辑/删除。** 人格增删改是我们自己做的
（`shell/personas_api.py` 8 个端点，#83），不是上游能力。

## 三、对话（4）

### POST `/api/chat`

```json
// 请求
{"session_id": "可空", "persona_id": "luna", "user_name": "可空",
 "client_id": "前端 localStorage UUID", "message": "你好"}
```

```json
// 响应
{"session_id": "...", "response": "你好呀！\n\n现在这个点刚好…",
 "modality": "文字", "image_url": null,
 "dominant_drive": "...", "drive_baseline": {...}, "drive_state": {...}}
```

三个坑：

1. **字段名是 `response`，不是 `reply`**。内部 `result['reply']` 对外映射成 `response`，
   脚本读 `reply` 会一直拿到空。
2. **`client_id` 不是认证**，是前端 localStorage 里的 UUID，可伪造。
3. **会话按 `(session_id, persona_id, user_name, client_id)` 复用**。测试脚本用固定
   `client_id` 会读到上次历史，模型容易答「你是不是复读机」（#30 踩过）。

`persona_id` 不存在时返回 **404**，不是 400。

### GET `/api/session/{session_id}/status`

返回 `agent.get_status()`；会话不存在 404。

### GET `/api/chat/history/{persona_id}`

| 参数 | 必填 | 说明 |
|---|---|---|
| `client_id` | 是 | query，前端 UUID |
| `limit` | 否 | 默认 50，1–500 |
| `before_id` | 否 | 分页游标 |

返回 `{messages: [], total: N}`。**只影响展示层，不碰引擎状态** —— 历史由
`chat_log_store` 独立存。

### WS `/ws/chat`

```
Client → Server: {"type": "chat", "content": "hello", "persona_id": "vivian"}
Server → Client: {"type": "chat_start", "session_id": "abc123"}
Server → Client: {"type": "chat_chunk", "content": "嘿～"}   # 流式
Server → Client: {"type": "chat_end", "dominant_drive": "🔗 联结", ...}
```

两个值得抄的设计：

- **打字防抖**：`DEBOUNCE_GRACE_SEC = 2.0` / `DEBOUNCE_FALLBACK_SEC = 3.0`。
  用户连发几条时先缓冲，等光标离开输入框 2 秒（或无信号 3 秒）再合并成一轮。
  真实聊天里用户确实会「说一句、补一句」，逐条处理会让回复错位。
- **流式分块**：`chat_chunk` 逐段推，不等全文生成完。

**没有 SSE**。流式只有 WebSocket 一条路，REST 端点整段返回。

## 四、媒体与调试（5）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tts` | 文字转语音 |
| POST | `/api/image` | 文生图 |
| GET | `/api/selfie/{filename}` | 取缓存的生成图 |
| POST | `/api/demo/inject` | 往 WS 客户端推演示指令 |
| GET | `/api/proactive/metrics` | 主动消息心跳指标 |

`/api/demo/inject` 是演示后门，**上线前必须删或加开关**。

## 五、与自建 API 的关系

| 关注点 | 上游 main.py | 我们（shell/） |
|---|---|---|
| 认证 | 无（client_id 可伪造） | P1 已做邮箱/邀请码 + refresh token |
| 人格 CRUD | 无（只读） | 8 端点（#83） |
| 多用户隔离 | 无（全局 `active_sessions`） | 按 `user_id` 隔离 |
| 限流配额 | 无 | #67 已做 |
| OpenAI 兼容 | 无 | `POST /v1/chat/completions`（#68） |
| 流式 | WS 分块 | 未接 |
| 会话历史 | `chat_log_store` | 未接（#50） |

**上游 main.py 是「单机演示服务器」，不是产品后端** —— 缺认证、隔离、限流。
`shell/` 补了这些，但两套目前并存，需要定谁是对外入口。

## 六、缺口（P4 待补）

| 缺口 | 任务 |
|---|---|
| Web 聊天页要的接口（流式、历史、切角色） | #46 #49 |
| CLI 走的接口 | #47 |
| 会话历史导出 | #50 |
| 模态开关（一期只开文本） | #52 |
| 主动消息接入会话存储 | #48 |

## 七、架构决策（已定）

见 `docs/adr/0002-shell-as-product-layer.md`：

**`shell/` 是唯一对外入口；`main.py` 作为内部服务保持上游原样。**

```text
浏览器 / CLI / OpenAI SDK
        │ HTTPS + WebSocket
        ▼
   shell/（认证 · 隔离 · 限流 · 人格 CRUD · 代理）
        │ 内部调用
        ▼
   vendor/OpenHer/main.py（上游，不改）
```

两条上游设计**必须保留**：

1. **打字防抖**（2s 宽限 / 3s 兜底）—— 连发消息合并成一轮，否则她的回复会错位
2. **流式** —— **修正**：WS 上的 `chat_chunk` 是死代码（`output_router.py` 收了 `ws_send`
   却从不调用），真正的流式在 `shell/` 的 `/v1/chat/completions` SSE。
   本文档第九章「流式分块，上游没有 SSE」的结论是反的，以这里为准（详见 `p4-93-seams.md` §3.1）

不嵌入引擎的理由：上游还在迭代，嵌入后每次升级都要重新对一遍；而且认证与引擎会挤进
同一个进程的全局单例（`active_sessions`、`persona_loader`）。

代理的代价（多一跳）在毫秒级，相对模型推理的秒级可忽略。
