# P1-69 引擎状态外置运行时验收

- 状态：Codespace 运行时验收通过
- 环境：Codespace + `scripts/dev/mock_provider.py`，应用以 `ALPHA_AUTH_DISABLED=1` 启动
- 结果：warm 32/32、resume 29/29、metrics 3/3
- 原始指标：`docs/data/p1-69-state.json`

## 覆盖范围

- 两个用户、同一 persona（`luna`）各 5 轮，状态不串
- 同一用户、两个 persona（`luna` / `iris`）交替各 5 轮，状态不串
- 冷启动后 `turn_count`、关系 EMA、驱力、聊天历史继续
- 同版本 CAS 双写只成功一次
- 单用户状态体积、`load_session` 耗时、轮次延迟

## 关键结果

| 项 | 结果 |
|---|---|
| 单用户状态体积 | 38,464–38,660 bytes，均值 38,597 bytes |
| `StateStore.load_session` | 0.84–0.86 ms 均值 |
| `AgentExtraStore.load` | 0.046–0.048 ms 均值 |
| 冷启动后 turn_count | 5 → 6 |
| 冷启动后聊天历史 | 10 → 12 条 |
| 关系 EMA | 保留并继续增长，未重置 |
| CAS 顺序双写 | 第一次成功，第二次失败，版本 +1 |
| CAS 并发双写 | 8 线程中仅 1 次成功 |
| mock 轮次延迟 | 均值 155 ms |
| DB 体积 | `openher.db` 约 1.15 MB，`agent_extra.db` 约 28 KB |

> 155 ms 是 mock provider 的端到端轮次延迟，不代表真实模型延迟；状态体积和 load 耗时是实际 SQLite 数据。

## 测试脚本修正

首次运行时发现四处验收脚本问题，均不是状态层丢失：

- 新 Agent 会先 `pre_warm` 60 步，DB 的 `interaction_count` 从 65（60 + 5）开始，不应断言为 5。
- `AgentExtraStore` 落盘字段是 `relationship_depth`，脚本原先查 `depth`。
- `resume` 阶段误把 `db` 记录当 `extra` 取 `turn_count`。
- `metrics` 需要把仓库根目录加入 `sys.path` 才能 import `shell.agent_state`。

另外，「两用户同一 persona」原先用 seed / drive 值判断隔离；同一 persona + 相同 mock 输入会得到确定性相同值。现改为校验两行 `(user_id, persona_id)` 确实不同且各自归属正确。

## #66 关联

#66 认证运行时另跑一次，使用真实认证开启 + 每用户 mock provider，结果 19/19：

- 登录页公开访问
- 两用户注册 / 登录 / refresh / logout
- 跨用户 session / 历史 / 记忆 / 关系状态隔离
- `client_id` 用户命名空间

数据：`docs/data/p1-66-auth.json`
