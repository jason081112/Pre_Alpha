# 运营通知（举报 / 告警）

举报入库时会同时走三条通道，任意一条配好就能收到：

| 通道 | 配置 | 说明 |
|---|---|---|
| 服务日志 | 默认开启 | `journalctl -u alpha.service` 或 `docker compose logs app` 里一行 `新举报 #N …` |
| JSONL 文件 | `ALPHA_REPORT_LOG`（默认 `/var/lib/prealpha/reports.log`） | 每行一条 JSON，便于归档/二次处理 |
| 邮件 | `ALPHA_SMTP_HOST` 等五项 | 异步发送，SMTP 失败只写日志，不影响举报接口 |
| Webhook | `ALPHA_REPORT_WEBHOOK` | POST `{text, content, report}`，可接飞书/企业微信/Slack/Telegram |

## 邮件（163 为例）

163 必须用**授权码**，不是登录密码；发件人必须等于登录账号。

```dotenv
ALPHA_SMTP_HOST=smtp.163.com
ALPHA_SMTP_PORT=465          # 465=SSL，587=STARTTLS
ALPHA_SMTP_USER=prealpha@163.com
ALPHA_SMTP_PASS=<授权码>
ALPHA_SMTP_FROM=             # 可空，默认同 USER
ALPHA_NOTIFY_EMAIL=          # 可空，默认同 USER
```

- 变量写在 `/etc/prealpha/env`（venv 部署）或 `/opt/prealpha/.env`（容器部署）；两处都跑时两边都写。
- 邮件标题：`[Alpha] 新举报 #12 · 自伤/自杀`；正文含编号、分类、时间、用户、对象、举报内容。
- 自测：重启后在页面提交一条举报，看日志出现 `[report] 邮件已发送 #N`；没出现就看 `[report] 邮件失败: …`。
- 未配置时 `from_env()` 返回 `None`，完全不发信，也不会报错。

## 为什么用授权码

邮箱服务商普遍禁止第三方用登录密码发信；授权码可单独吊销，泄露后只影响 SMTP。
授权码只放服务器 env（600），不进仓库；`scripts/dev/check_repo_hygiene.py` 会检查跟踪文件里没有明文密钥。
