# ADR-0002 · 引擎以「同进程嵌入 + 受限接缝」接入，不做 HTTP 代理

- **状态**：Accepted（修订 2026-11-05）
- **日期**：2026-11-05
- **关联**：ADR-0001（云端部署）、#45（路由清单）、#93（接缝守卫）

---

## 背景

P1 阶段在 `shell/` 建了产品能力：认证、按用户会话隔离、限流配额、多 provider 路由、OpenAI 兼容端。

`vendor/OpenHer/main.py`（1573 行 / 17 路由）也能跑。看起来是两套并存。

**但读代码后发现不是**：`shell/app.py:102` 是 `app = upstream.app` —— 外壳和引擎本来就是
同一个 FastAPI 进程同一个 app 对象。P1 已经通过 5 个运行时接缝接上了，只是没写进文档。

> **本 ADR 的第一版（同日）错判为「两进程、需 HTTP 代理」。** 当时的依据是 #45 的
> 路由清单，没读 `app.py`。此处按代码事实修订。

## 决策

**同进程嵌入：`shell/app.py` 复用上游 FastAPI 实例，只经 5 个受限接缝注入能力。
`vendor/` 下任何文件都不修改。**

5 个接缝：

| # | 接缝 | 注入内容 | 位置 |
|---|---|---|---|
| 1 | `upstream.get_or_create_session` | session 按用户前缀化、历史恢复、补充状态回填 | `app.py:142` |
| 2 | `upstream._persist_agent` | 关系 EMA / turn_count 落盘 | `app.py:186` |
| 3 | `upstream.llm_client` | `RoutingLLMClient`：多 provider 路由 + 用量计量 | `app.py:451` |
| 4 | `upstream.chat_log_store` | `ScopedChatLog`：聊天记录按用户隔离 | `app.py:455` |
| 5 | `persona_loader.reload` | 人格 CRUD 后热重载 | `app.py:118` |

中间件顺序（外层 → 内层）：`AuthMiddleware` → `LimitsMiddleware` → `AlphaMiddleware` → 引擎。

## 为什么不是 HTTP 代理

引擎的核心状态是**模块级单例**：`persona_loader`、`chat_log_store`、`llm_client`、
`active_sessions`、`_ws_by_client`。

要做 HTTP 代理，就得把这些状态搬到进程外或做双写——工作量远大于收益，且会引入
「两边状态不一致」这类最难查的 bug。多一跳的延迟倒是无所谓（毫秒级）。

嵌进去之后，引擎的防抖、WS 分块、critic、Genome 全部原样可用，不需要重新实现。

## 为什么不算「改了上游」

`vendor/` 文件 0 修改，升级仍是替换目录。改的是运行时对象属性，不是源码。
代价是：**上游重构时接缝可能静默失效**。

## 后果

**正面**

- P1 的认证 / 限流 / 路由 / 隔离零改动生效
- 防抖（2s/3s）与 WS 流式分块原样保留，不用代理层重新实现
- 单进程部署，运维简单

**负面 —— 也是 #93 要解决的**

1. **接缝静默失效**：上游改函数名或签名，`getattr` 拿不到就静默跳过（`app.py:120` 就是
   `if loader is None: return`）。升级后可能「能跑但人格不重载」。
   → 对策：**启动时自检 5 个接缝，缺任何一条直接拒绝启动**（#93）
2. **上游危险端点默认可用**：因为 `app = upstream.app`，`POST /api/demo/inject`
   能向任意 `client_id` 的 WS 推消息，等于**绕过认证冒充用户发言**。
   → 对策：默认封禁，需 `ALPHA_ENABLE_DEMO_INJECT=1` 才开（#93）
3. **两套 API 并存对外**：前端既能看到上游 `/api/*`，也能看到 `shell/` 的 `/v1/*`。
   → 对策：文档约定前端只连 `shell/` 端点；上游 `/api/chat` 保留给调试

## 实施（#93）

1. 接缝守卫 `shell/seams.py`：启动自检，缺则 fail-fast 并打印缺失清单
2. 危险端点闸门：`/api/demo/inject` 默认 404
3. 五条验收固化成可执行脚本
