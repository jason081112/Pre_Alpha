# P4-93 · 接缝守卫 + 上游后门封禁

> 关联 ADR-0002。验收脚本：`scripts/dev/verify_93_seams.py`（10 项全过，1 项 skip）

## 1. 为什么要做这件事

看完代码发现一个容易忽略的事实：**外壳不是「另一套 API」，而是和引擎共用同一个 FastAPI app**。

```python
# shell/app.py:102
app = upstream.app
```

P1 做的认证、限流、多 provider 路由、用户隔离，全部靠**运行时替换 5 个对象**接上去，
`vendor/` 一行没改。好处是升级只需替换目录；坏处是——

**上游一重构，接缝就静默失效。**

看这段（`app.py:118`，改造前）：

```python
loader = getattr(upstream, "persona_loader", None)
if loader is None:
    return          # ← 上游改了名，这里就悄悄什么都不做
```

服务能起、能聊、不报错，只是**人格改了不生效**、**记录没隔离**。
这类故障最难查，因为所有指标都「正常」。

## 2. 做了什么

### 2.1 接缝守卫 `shell/seams.py`

启动时逐条验证 5 个注入点，缺失即拒绝启动。

| # | 接缝 | 注入内容 | 失效表现 |
|---|---|---|---|
| 1 | `get_or_create_session` | session 按用户前缀化、历史恢复、补充状态回填 | 跨用户串会话 |
| 2 | `_persist_agent` | 关系 EMA / turn_count 落盘 | 关系状态每轮重置 |
| 3 | `llm_client` | `RoutingLLMClient` | 绕过限流与多 provider 路由 |
| 4 | `chat_log_store` | `ScopedChatLog` | 聊天记录不分用户 |
| 5 | `persona_loader.reload` | 人格热重载 | 改人格要重启 |

关键设计：**用 identity 比对，不是 `hasattr`。**

```python
def _is_installed(upstream, attr, expected):
    actual = getattr(upstream, attr)
    if actual is not expected:      # 还是上游原版 → 等于没装
        return False, f"{attr} 仍是上游原版（未注入）"
```

只判断「能拿到」是不够的：上游原版函数也在，`hasattr` 永远为真。
P1 里 `llm_client` 就差点这么漏掉——上游 startup 会先创建一个自己的客户端。

启动输出：

```text
✓ 接缝自检:
  ✓ get_or_create_session
  ✓ _persist_agent
  ✓ llm_client
  ✓ chat_log_store
  ✓ persona_loader.reload
✓ 已封禁上游端点: /api/demo/inject
```

失败时：

```text
接缝缺失，拒绝启动：llm_client
上游（vendor/OpenHer）可能已重构。请检查 shell/seams.py 的检查项与
docs/adr/0002-shell-as-product-layer.md 的接缝表；确认无害后可设
ALPHA_SEAM_STRICT=0 降级为告警。
```

`chat_log_store` 单独处理：上游本来就允许不启用持久化，此时记 ⚠ 不记 ✗。

### 2.2 封禁上游演示后门

`app = upstream.app` 意味着上游全部 17 个路由对外可达，其中：

```python
# vendor/OpenHer/main.py:878
@app.post("/api/demo/inject")
async def demo_inject(request: Request):
    """Push a demo command to the UI client via WS."""
```

它能向**任意 `client_id`** 的 WS 连接推 `send_chat`——等于**绕过认证冒充用户发言**。
演示用的后门，在生产环境是提权漏洞。

做法是**从路由表摘除**而不是中间件拦截：

```python
def _strip_dangerous_routes(application):
    for route in list(application.router.routes):
        if getattr(route, "path", None) in _DENY_PATHS:
            application.router.routes.remove(route)   # 摘掉即 404
```

摘掉之后不存在绕过路径（中间件可能被某条子路由跳过）。需要演示时用
`ALPHA_ENABLE_DEMO_INJECT=1` 显式打开。

## 3. 验收（`scripts/dev/verify_93_seams.py`）

自包含：内置 OpenAI 兼容 mock，不联网、不烧额度。

| # | 检查 | 结果 |
|---|---|---|
| 0 | 5 条接缝全部 ✓（读启动日志） | PASS |
| 1 | HTTP 无 token → 401 | PASS |
| 2 | B 拿 A 的 `session_id` → 404 | PASS |
| 3 | WS 3 秒内连发 3 条 → `chat_end` 只有 1 次 | PASS |
| 3b | WS 上有没有 `chat_chunk` | **SKIP（上游没有）** |
| 4 | HTTP SSE 多个 `data:` 事件 + `[DONE]` | PASS |
| 5 | `/api/demo/inject` → 404 | PASS |

```bash
python3 scripts/dev/verify_93_seams.py
# 10/10 passed，1 skipped
```

### 3.1 一个必须记下来的发现

**上游的 WS 流式是死代码。**

`main.py:1108` 调用 `stream_to_ws(agent.chat_stream(...), ws.send_json, ...)`，
但 `agent/output_router.py` 里 `ws_send` 参数**从未被调用**（全文 126 行，只在
签名和 docstring 里出现）。它只做「找【最终回复】标记 → 解析 → 回调」，不发 chunk。

main.py 的 docstring 写着：

```text
Server → Client: {"type": "chat_chunk", "content": "嘿～"}  (streamed)
```

与实际不符。**首字延迟等于全文生成时间**，在陪伴场景这是体验硬伤。

这条单独开 issue 处理（#94）：在 `shell/` 内提供真流式 WS 端点，不改 vendor。

同时修正 #45 文档里「流式分块只有 WS 一条路」的说法——实际相反，**流式只有 HTTP SSE**。

### 3.2 mock 必须回符合引擎协议的内容

第一版 mock 回「我在。别急。慢慢说。」→ `chat_chunk=0`。
原因不是代理层：引擎要 `【最终回复】` 标记才认得出哪段是对外话。mock 改成：

```text
【内心独白】他好像有点急。
【最终回复】我在。
别急，慢慢说。
```

这提醒一件事：**任何 mock 都得符合 `actor_single.md` 协议**，否则测的是 mock 自己的错。

### 3.3 「轮」怎么数

一次用户消息 → 引擎内部可能是 3–5 次 LLM 调用（内心独白、最终回复分步生成）。
所以防抖不能用「mock 被调用几次」判断，要用**引擎下发的 `chat_end` 次数**。

实测：3 条消息 → `chat_end=1`，mock 调用 5 次。前者是 1 轮，后者是 5 次内部调用。

## 4. 环境注意

沙箱 `/sandbox/workspace` 是 overlay 文件系统，SQLite 写入报 `disk I/O error`。
验收脚本把所有 DB 指向 `/tmp`：

```python
"ALPHA_DATA_DIR": str(tmp),        # tmp = /tmp/verify93-<ts>
"ALPHA_ROUTING_DB": str(tmp / "routing.db"),
...
```

另外上游 startup 会先创建一个默认 LLM 客户端，没有 `OPENAI_API_KEY` 直接失败。
脚本传 `sk-dummy-for-startup`——这个客户端随后被 `RoutingLLMClient` 替换，不会真调用。
（这正是接缝 3 的 identity 检查要盯的地方。）

## 5. 没做

- **没改 `vendor/`**：一行都没改
- **没做 HTTP 代理**：见 ADR-0002 修订说明
- **没实现真流式**：上游限制，转 #94
