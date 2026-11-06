# P4-94 · 真流式 WS 端点

> 验收：`scripts/dev/verify_94_stream.py`（9/9）。单测：`tests/test_stream_parse.py`（10/10）。

## 1. 问题

上游 `main.py:1108`：

```python
await _stream_to_ws(
    agent.chat_stream(merged_text),
    ws.send_json,
    on_feel_done=...,
    on_reply_complete=...,
)
```

`agent/output_router.py:58` 的 `stream_to_ws(raw_stream, ws_send, ...)` 里，**`ws_send` 从未被调用**。
全文 126 行，它只出现在签名和 docstring。

实际行为：扫描到 `【最终回复】` 标记 → 攒全文 → `parse_raw_output` → 回调。
中间一个 chunk 都不发。客户端看到的是：

```text
chat_start → （沉默 5–15 秒）→ chat_end
```

**首字延迟 = 全文生成时间。**

## 2. 实现

### 2.1 `shell/stream_parse.py` — 增量解析

把原始流切成「可对外发送的正文」：

| 状态 | 行为 |
|---|---|
| `before` | 攒着，找 `【最终回复】` / `[Final Reply]`；找到前一个字都不发 |
| `in_reply` | 边收边发；遇到 `【表达方式】` / `[Expression Mode]` 停 |
| `after` | 丢弃（表达方式、后续花絮都不属于回复正文） |

三个必须处理的边界：

1. **标记跨块**：`"【最终回"` + `"复】"`。做法是尾部保留「可能是标记前缀」的长度，等下一块再判。
2. **正文里出现标记字样**：角色可能聊到「【表达方式】这个词」，只认第一次出现的结束标记。
3. **模型不守协议**：一个标记都没有时，中途一个字都不发——否则会把内心独白当回复吐给用户。
   由 `finish()` 兜底，且 `chat_end` 用引擎自己的 `parse_raw_output` 结果，保证最终文本正确。

### 2.2 `shell/ws_stream.py` — 端点

```text
Client → {"type":"chat","content":"...","persona_id":"luna","client_id":"...","user_name":"..."}
Client → {"type":"typing","active":false}        # 可选，窗口缩到 2s
Client → {"type":"ping"} / {"type":"close"}

Server → {"type":"chat_start","session_id":"...","user_content":"..."}
Server → {"type":"chunk","content":"我在","index":0}
Server → {"type":"chunk","content":"。","index":1}
Server → {"type":"chat_end","session_id":"...","reply":"我在。","modality":"文字",
          "segments":["我在。"],"engine_segments":null,"chunks":3,
          "first_chunk_ms":183,"elapsed_ms":1204,
          "persona":"Luna","dominant_drive":"..."}
```

> 多段逐条推送（`segment` / `index` / `segment_end`）在 #96 加入，
> 见 `docs/p4-96-segments.md`。此处的报文是单段形态。
Server → {"type":"error","content":"..."}
```

复用外壳已有的接缝，不重复实现：

- `upstream.get_or_create_session` → 用户隔离 + 历史恢复
- `upstream._persist_agent` → 关系 EMA / turn_count 落盘
- `upstream.chat_log_store` → 对话历史（`ScopedChatLog` 自动加用户前缀）

防抖单独实现（上游那份和 WS 主循环耦合，抽不出来）：

- 默认窗口 3s，每条新消息重置
- 收到 `typing:false` 后窗口 2s
- 硬上限 20s（用户一直在打字时强制回一轮）
- 单 worker 串行处理，一轮没跑完不会开第二轮

## 3. 两个踩到的坑

### 3.1 新 WS 端点没有路由上下文

第一版一跑就报：

```text
[react] ❌ Round 1 LLM error: 当前用户尚未选择模型（route_not_configured）
```

`AlphaMiddleware` 只对 `path == "/ws/chat"` 注入 `routing_context`，新路径被直接透传，
`RoutingLLMClient` 拿不到 route。

修：把 WS 路由收成集合。

```python
_WS_ROUTES = ("/ws/chat", "/ws/chat/stream")
```

### 3.2 未认证 WS 返回的是 403，不是 1008

`AuthMiddleware._reject` 原本直接发 `websocket.close(1008)`。但 ASGI 规范下，
**握手未 `accept` 就 `close`，服务器会把它翻成 HTTP 403**，客户端拿到的不是 close code。

修：先 `accept` 再 `close(1008)`。客户端才看得到「unauthorized」。

（这个 bug 在 #93 之前就存在，只是那条路径没人测。）

## 4. 验收数据

mock 每个分片之间刻意停 500ms；如果实现退化成非流式，所有 chunk 会同时到达。

```text
[PASS] 1 收到 chat_start                        n=1
[PASS] 2 分块数 >= 3                            n=3
[PASS] 3a 首字延迟（服务端计时）                 first_chunk_ms=183ms
[PASS] 3b 首字延迟（客户端，从 chat_start 起）   45ms
[PASS] 4 分块分批到达（非一次性）                首尾跨度 958ms
[PASS] 5 chunk 拼接 == chat_end.reply           完全一致
[PASS] 6 chat_end 带延迟指标                    first_chunk_ms=183, chunks=3
[PASS] 7 防抖：3 条 → 1 轮                      chat_end=1
[PASS] 8 无 token → 1008 关闭                   close code=1008
```

### 「首字延迟」的口径

必须分清两段时间：

```text
发送 ──[防抖窗口 2–3s]── chat_start ──[183ms]── 第一个字 ──[1s]── chat_end
        ↑ 设计行为，连发消息合并                    ↑ 流式能力，本次要证明的
```

第一版我把整段都算成「首字延迟」，测出 2833ms 就判 FAIL——**判错了**。
发一句话等 3 秒才开打字是防抖的设计（连发时才有意义），不是流式失效。

修正后：服务端 183ms、客户端从 `chat_start` 起 45ms，都远小于全文 1000ms。

真要缩短第一段，办法是前端在按发送键时发 `typing:false`（输入框已空），窗口从 3s 降到 2s。

## 5. 与上游 `/ws/chat` 的关系

两条并存，不替换：

| | `/ws/chat`（上游） | `/ws/chat/stream`（本条） |
|---|---|---|
| 中间内容 | 无 | 有（chunk） |
| 防抖 | 有 | 有 |
| 分段（segments） | 逐段 `chat_end` + 段间 300ms 停顿 | `chat_end` 带 `segments`，前端自行处理 |
| 图片/语音 | 有（`tts_audio`、`image_url`） | 无（`chat_end` 只带文本与状态） |
| 用途 | 上游自带前端 | #46 新页面 / #47 CLI |

多模态没搬过来：`chat_end` 里仍返回 `modality`，但 `image_url` / `tts_audio` 的推送要等
#52（模态开关）决定做不做。

## 6. 没做

- 不改 `vendor/OpenHer`
- 不做 chunk 合并节流（每个 LLM 分片发一条，前端按 `index` 拼）
- 不做断线重连补偿（重连后拿不到断线期间的内容，`chat_log` 里有）
