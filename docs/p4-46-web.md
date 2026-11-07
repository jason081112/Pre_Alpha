# P4-46 · 极简 Web 聊天页

> 验收：`scripts/dev/verify_46_ui.py`（17/17）。文件：`shell/ui.py`（单文件，无构建）。

## 1. 怎么跑

```bash
bash scripts/dev/restore_env.sh          # 首次 / 沙箱重置后
python3 -m shell.app                     # 默认 0.0.0.0:8000
```

打开 `http://<host>:8000/` → 登录 → 聊天。

首次需要账号：

```bash
python3 scripts/dev/make_invite.py       # 拿邀请码（若 ALPHA_REQUIRE_INVITE=1）
# 或直接注册：POST /api/auth/register {"email","password"}
```

开发期可 `ALPHA_AUTH_DISABLED=1` 跳过登录（此时所有请求按 `local` 用户处理）。

## 2. 首页接管

上游 `main.py` 自带四个页面路由（`/`、`/app`、`/discover`、`/chat/{path}`）。
启动时摘掉 `/`，换成 `shell/ui.py` 的聊天页：

```python
_STRIPPED_PAGES = _strip_upstream_pages(app, ("/",))
```

其余三个保留（不影响使用；上游演示前端仍在 `/app`）。

## 3. 页面能力

| 能力 | 实现 |
|---|---|
| 登录 | `POST /api/auth/login`，token 存 `localStorage` |
| 人格切换 | `GET /v1/models` 拿列表，下拉切换；切换后清屏重拉历史 |
| 历史 | `GET /api/chat/history/{persona}?client_id=…&limit=60` |
| 发送 | `WS /ws/chat/stream`，`{type:"chat", content, persona_id, client_id}` |
| 逐字 | `chunk` 直接追加到当前气泡 |
| 多段气泡 | `chunk.segment` 变化 → 新建气泡；`segment_end` → 收尾 |
| 正在输入 | `chat_start` 显示，首个 `chunk` 隐藏 |
| 断线 | 3 秒重连；`1008`（未认证）直接回登录 |

发送时额外发一条 `{"type":"typing","active":false}` —— 输入框已空，让后端把防抖窗口
从 3s 缩到 2s。

## 4. 安全

CSP（`shell/ui.py`）：

```text
default-src 'none'; style-src 'nonce-…'; script-src 'nonce-…';
connect-src 'self'; img-src 'self' data:;
base-uri 'none'; form-action 'self'; frame-ancestors 'none'
```

- 所有内联样式/脚本带 nonce，**无内联事件属性**（`onclick=` 会被 CSP 拦）
- 全部事件用 `addEventListener` 绑定
- `Cache-Control: no-store`

**已知待办**：token 存 `localStorage`，XSS 可读。#82 会换成 HttpOnly cookie + CSRF。

## 4b. 事件日志（#98）

页面上发生过的事都记下来，出问题能回看——**默认开启**，不上报服务器。

```json
{"t": 1762500000123, "k": "ws.close", "d": {"code": 1006, "reason": "", "wasClean": false, "buffered": 1}}
```

| 事件 | 数据 |
|---|---|
| `app.start` | 是否带 token、当前人格、client_id |
| `auth.login` | 成功与否 |
| `ws.open` / `ws.close` / `ws.error` | code、reason、readyState、**未回复消息数** |
| `ws.reconnect_scheduled` | 未回复消息数、重连延迟 |
| `send.chat` | 原文、persona、client_id、累计未回复数 |
| `recv.chunk` | segment、index、长度、内容 |
| `recv.segment_end` / `recv.chat_end` | 段数、first_chunk_ms、elapsed_ms、全文 |
| `recv.bad` | 无法解析的原始报文 |
| `http` | 路径（去掉 query）、状态码 |
| `ui.switch_persona` | from / to |

存储：`localStorage["alpha.debug"]`，环形 800 条（约 13 轮对话），先丢最旧。

导出：顶栏「日志」按钮 → 下载 `alpha-debug-<时间>.json`。

实时面板：URL 加 `?debug=1`，底部显示最近 14 条。

**为什么 `send.chat` 要记 `unanswered`**：#98 第 6 条「断线后消息没回应」，靠这个数能直接看出
重连时是否还有未完成的一轮。

**为什么 chunk 不抽样**：查「气泡粘连」必须看到每个 chunk 的 `segment`/`index`。

验证：`node tests/ui_debug_smoke.mjs` —— 用最小 DOM stub 跑真实页面脚本，
断言日志真的写进 localStorage（10/10），而不是只检查某个字符串出现在 HTML 里。

## 5. 不做的

| 项 | 归谁 |
|---|---|
| 打字节奏（标点停顿、加速） | #95 |
| 视觉语言、动效 | #80 |
| PWA 安装 / 离线 | #72 |
| 多设备同步 | 未排期 |

现在 `chunk` 一来就直接追加，不排队、不加延迟 —— 会稍微「跳」，但可用。

## 6. 验收

沙箱没有浏览器，所以验证的是「页面能安全地跑起来」的必要条件：

```text
[PASS] 1 首页 200
[PASS] 2 返回 HTML
[PASS] 3 有 CSP
[PASS] 4 CSP 含 script nonce
[PASS] 5 HTML nonce 与 header 一致（2 处）
[PASS] 6 无未替换占位符
[PASS] 7 无内联事件属性
[PASS] 8 DOM 含 login/log/text/send
[PASS] 9 页面连 /ws/chat/stream
[PASS] 10 页面用 /api/chat/history
[PASS] 11 注册可用
[PASS] 12 /v1/models 返回人格
[PASS] 13 历史端点可用
[PASS] 14 WS 可连（ping/pong）
```

另有 JS 语法检查：`node --check`（336 行，通过）。

**没有做的验证**：真实浏览器渲染、点击流程、iOS Safari 兼容。这些只能人工。
第一次在手机上打开时，如果气泡不出现，先看控制台是不是 CSP 拦了 `ws://`——
`connect-src 'self'` 在部分浏览器的历史版本里不匹配 WebSocket，需要显式加 `ws: wss:`。

## 7. 下一步

页面出来之后，#49（多角色管理）、#50（导出）、#82（安全基线）、#72（PWA）都可以接。
