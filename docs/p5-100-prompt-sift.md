# #100 Waffo Prompt Sift 最小接入

状态：代码与测试已落地，等待真实商户凭据做一次线上联调。

## 目标

在把用户消息交给模型生成之前，调用 Waffo Prompt Sift 做一次内容安全扫描。只扫描**当前这一轮用户输入**，不发送历史消息、人格内容或聊天记录。

这解决 Waffo AIGC 审核中的“生成前审核”要求；输出侧审核、违规处置与举报闭环仍属于 #100 的剩余范围。

## 行为

| 场景 | 行为 |
|---|---|
| `allow` | 正常进入路由、配额与生成 |
| `review` | 拒绝本轮生成，返回“内容正在复核” |
| `block` | 拒绝本轮生成，返回通用违规提示 |
| 超时 / 网络错误 / 5xx / 响应格式异常 | fail-closed，拒绝本轮生成 |
| 429 | 返回 429，并透传 `Retry-After` |
| 提示为空 / 超过 10,000 字符 | 本地拒绝，不发起网络请求 |

被拒绝的轮次不会创建 session、不会调用模型、不会写入聊天历史。

## 配置

```bash
# auto（默认）：有凭据才启用；本地开发没有凭据时跳过
ALPHA_PROMPT_SIFT=auto

# 生产：缺少凭据直接拒绝启动
# ALPHA_PROMPT_SIFT=enforce

WAFFO_MERCHANT_ID=...
# 二选一：
WAFFO_PRIVATE_KEY='-----BEGIN PRIVATE KEY-----\n...'
# 或
WAFFO_PRIVATE_KEY_PATH=/secure/path/waffo-private-key.pem
```

可选：

```bash
ALPHA_PROMPT_SIFT_LOCALE=zh          # ja / en / zh
ALPHA_PROMPT_SIFT_SEMANTIC=enforce   # off / shadow / enforce
ALPHA_PROMPT_SIFT_TIMEOUT=5          # 0.5–30 秒
```

`auto` 在缺少凭据时只打印一次 `off`，不会阻断本地启动。生产必须使用 `enforce`，否则“忘记配 key”会静默跳过审核。

## 签名

请求头：

- `X-Merchant-Id`
- `X-Timestamp`：Unix 秒，允许比服务器时间早 5 分钟、晚 1 分钟
- `X-Signature`：对以下规范请求做 RSA PKCS#1 v1.5 / SHA-256 签名后 Base64

```text
POST
/v1/actions/verification/scan-prompt
<TIMESTAMP>
BASE64(SHA256(BODY))
```

私钥只从环境变量或文件读取，不写入仓库、日志或数据库。`cryptography` 采用延迟导入：未启用扫描的本地环境不需要安装它。

## 接入点

- `shell/content_safety.py`：`PromptSiftClient`、签名、HTTP、结果映射。
- `shell/openai_api.py`：在 `extract_last_user_text()` 之后、建 session 之前调用。
- `shell/ws_stream.py`：在防抖合并出的本轮 `text` 之后、建 session 之前调用。
- `shell/app.py`：启动时按环境构造一次客户端，注入两个 router。

两条链路使用同一套拒绝语义；WS 错误事件额外带 `code`，限流时带 `retry_after`。

## 测试

```bash
python3 tests/test_content_safety.py
```

覆盖：allow、block/review 映射、429、5xx、坏 JSON、空消息、超长消息、三种启动模式与签名规范串。测试用注入的 fake transport/signer，不需要 `cryptography`、网络或真实商户凭据。

## 隐私披露

`site/legal/privacy.html` 与 `site/legal/terms.html` 已把 Waffo Prompt Sift 写为内容安全处理方，说明：

- 每轮输入的当前文本会发送给 Waffo；
- 服务无状态、不保留原文；
- 可能跨境处理；
- 审核不通过时不生成回复。

条款/隐私版本同步升到 1.2。

## 仍需完成

- 用真实商户凭据在 Codespace 跑一次 allow / block 联调，记录 `requestId`。
- 输出侧审核：流式与非流式都要覆盖。
- 违规处置：封禁、额度清零、内容删除、举报入口与申诉时限。
- Waffo 后台填写内容安全配置并完成网站自检。
