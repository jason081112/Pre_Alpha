# 订阅与试用（#102）

单一 BYOK 档：$9 / 月，$90 / 年；新用户免费 30 轮（按 AI 回复条数计）。
价格的唯一来源是 `shell/plans.py`，`site/pricing/index.html` 由
`scripts/dev/gen_pricing_page.py` 生成，CI 用 `--check` 防止手改漂移。

## 运行方式

| 项目 | 位置 |
| --- | --- |
| 试用轮数 / 订阅状态 | `$ALPHA_DATA_DIR/billing.db`（默认 `/var/lib/prealpha/billing.db`） |
| 用户侧接口 | `GET /api/billing/status`、`POST /api/billing/checkout` |
| Waffo 回调 | `POST /api/billing/webhook`（原始 body + `X-Waffo-Signature`） |
| 试用拦截 | WS 返回 `{"type":"error","code":"trial_exhausted"}`；`/v1` 返回 402 |

订阅有效状态：`active`、`canceling`、`past_due`（`past_due` 保留 7 天宽限）。
`canceled` 或 `refund.succeeded` 立即失去权益；试用轮数不因订阅而清零。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `WAFFO_MERCHANT_ID` | Waffo 商户号 |
| `WAFFO_STORE_ID` | 店铺 ID（`STO_...`） |
| `WAFFO_PRIVATE_KEY_B64` | base64 的 PKCS#8 私钥（签名用） |
| `WAFFO_WEBHOOK_PUBLIC_KEY_B64` | base64 的 Webhook 公钥（验签用） |
| `WAFFO_PRODUCT_BYOK_MONTHLY` | 月付商品 ID（`PROD_...`） |
| `WAFFO_PRODUCT_BYOK_YEARLY` | 年付商品 ID |
| `ALPHA_PUBLIC_URL` | 支付成功回跳的站点根地址 |
| `ALPHA_BILLING_DB` | 可选，覆盖 billing.db 路径 |

密钥只在服务器 `/etc/prealpha/env`（600），不进仓库、不进日志。

## 首次配置 / 换环境

```bash
# 在服务器上，用 venv（本机没有 cryptography）
ssh root@47.239.51.212
set -a; . /etc/prealpha/env; set +a
/opt/prealpha/vendor/OpenHer/.venv/bin/python /opt/prealpha/scripts/dev/waffo_setup.py
```

脚本会：列出商品 → 缺的按 `plans.py` 价格创建 → 发布到生产 →
注册 webhook（`ALPHA_PUBLIC_URL + /api/billing/webhook`）→ 打印商品 ID。
把打印出的 `WAFFO_PRODUCT_BYOK_MONTHLY/YEARLY` 写回 `/etc/prealpha/env` 后重启。

## 手工核对

```bash
# 商品与价格
curl -s https://47.239.51.212.sslip.io/api/billing/status   # 需登录 token

# 回调必须用原始 body 验签；测试卡：成功 4576 7500 0000 0110 / 拒付 ...0220
```

测试订单完成后，`billing.db` 的 `billing_subscription.status` 应为 `active`，
`billing_event` 里能看到 `subscription.activated` 等事件；重复投递只留一条。

## 已知限制

- 自助取消/客户门户还没接入（`issue-session-token` + portal），目前走邮件人工取消。
- 订阅页只做单档；多档/升级降级未做。
- `past_due` 宽限 7 天，之后仍不恢复则视为无权益（`is_subscribed` 返回 False）。
