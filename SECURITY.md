# Security Policy

## 报告漏洞

请不要在公开 issue 里贴漏洞细节、Key、聊天记录或可复现的生产数据。

优先使用 GitHub Security Advisories（仓库 Security → Report a vulnerability）。
如果不可用，开一个不含敏感信息的 issue，说明“需要私下联系”。

## 范围

重点关注：

- 用户 Key 的存储、解密与回显（`shell/crypto.py`、`shell/custom.py`）
- 认证与会话隔离（#66）
- 自定义 provider 的 SSRF / DNS rebinding（#81）
- 提示注入导致的越权或数据外泄
- 导出 / 删除路径是否漏数据

## 已知边界

- 当前认证尚未接入，`X-Alpha-User` 可伪造；**不要**把未加认证的实例暴露到公网。
- 静态加密防的是数据库/备份被单独拿走，不防整机沦陷。
- 原始聊天记录不应上传；如果发现任何路径会把原文发到服务端，请按漏洞报告。

## Key 处理原则

- Key 只在提交时经过浏览器，之后由服务端 AES-GCM 加密落库。
- 列表接口只返回 `has_key`，不回显 Key。
- 日志、异常、响应体不得包含明文 Key 或密文。
- 主密钥与数据库备份分离存放。

## 支持版本

仅 `main` 分支。
