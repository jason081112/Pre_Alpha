# 账号注销（#75）

用户路径：菜单 → 注销账号 → 输入「删除」确认 → 进入冷静期（默认 7 天，可配置）
→ 到期后台任务自动清除。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/me/delete` | 当前状态、冷静期天数、确认字、订阅提示 |
| POST | `/api/me/delete` | `{ "confirm": "删除" }`，不匹配返回 422 |
| POST | `/api/me/delete/cancel` | 冷静期内撤销 |
| POST | `/api/admin/deletions/run` | 头 `X-Alpha-Admin-Token`，强制跑一批到期的 |

到期检查每小时一次（服务启动时也跑一次）。

## 清除范围

- `auth.db`：auth_user / auth_identity / auth_token / auth_consent
- `limits.db`：token_usage
- `moderation.db`：moderation_report / moderation_action / moderation_ban
- `billing.db`：billing_trial / billing_subscription / billing_checkout，以及
  payload 里含该 user_id 的 billing_event
- `agent_extra.db`：agent_extra
- `routing.db`：user_provider / user_routing / provider_audit
- 引擎 `chat.db`：`client_id` 为 `session_prefix(user)`、`session_prefix:...`
  或原始 user_id 的消息
- 引擎 `openher.db`：chat_summary / genome_state / proactive_outbox / style_memory
  （`genesis_seed` 是全局人格种子，保留）
- 引擎 `memory.db`：memories（FTS 由触发器同步删除）
- 人格目录：`ALPHA_PERSONA_ROOT/<user_id>/`

每一项删除行数写入 `account_purge_log`，完整回执落在 `account_deletion.receipt`。

## 备份

- 配了 `ALPHA_BACKUP_DIR`：文件名含 user_id 的删除；超过
  `ALPHA_BACKUP_RETENTION_DAYS`（默认 30）的一并轮换清除，数量写回执。
- 没配：回执里明确写「未配置异地备份，数据只在本机」，不假装有。
- 删除失败（权限等）写进回执 `errors`，不会静默当成成功。

## 已知限制

- **Waffo 订阅不会自动取消**：注销不退款；用户需先邮件取消订阅。产品侧在确认框里明示。
- 若数据库正被长事务占用，删除会失败并在回执 `errors` 记录，下次任务重试。
- 日志/访问记录不在本次范围（服务端 access log 由运维轮换）。
