# P4-86 人格体检

对应看板 #86。产出 `shell/persona_check.py`、`alpha persona check` 子命令、
`tests/test_persona_check.py`（12 项）。

## 为什么需要它

#30 实测结论：**"能加载"不等于"像这个人"**。小岚当时加载成功、无泄漏，但完全没有"嘴硬"。
用户造完人格后，需要一个地方告诉他"现在处于什么状态、缺什么"。

## 四块检查

| 块 | 类型 | 内容 | 不可用时 |
|---|---|---|---|
| `structure` | 静态 | 校验错误、`Layer N` 泄漏、`insufficient + inferred` 并存、内容充实度 | 总是可跑 |
| `privacy` | 静态 | 手机号 / 身份证 / 邮箱 / 银行卡 / 绝对路径 | 总是可跑 |
| `load` | 动态 | `PersonaLoader` 能否读到，关键字段是否非空 | `skipped` + 原因 |
| `probe` | 动态 | 3 轮固定提问，看句长 / 口头禅命中 / 结构标记 | `skipped` + 原因 |

**降级必须显式**：缺环境时返回 `skipped` 并写清原因，绝不伪装成 `pass`。

## 判定

```
任一块 fail  → 整体 fail（阻断保存）
否则有 warn  → warn（可继续，提示补什么）
全 pass/skip → pass
```

## 内容充实度（structure 的 gaps）

| 检查 | 阈值 | 缺失后果 |
|---|---|---|
| `expression.rhythm` | 非 missing | SOUL.md 的「说话风格」为空 |
| `expression.habits` | ≥ 3 条 | 口头禅召回不了（#35） |
| `hard_rules` | ≥ 1 条 | 人格缺底线 |
| `conflict.boundaries` | ≥ 1 条 | 冲突场景易失控 |
| `drive_baseline` | 5 维全标定 | 行为倾向走引擎默认值 |

gap 只产生 `warn`，不阻断 —— 用户可以先造一个骨架，再逐步补。

## 修法映射（fixes）

报告不只说"缺什么"，还给出对应 issue：

- 口头禅不足 → 「补 3 条以上口头禅，写入 `expression.habits`（#35 会灌进 style_memory）」
- drive 未标定 → 「标定 `drive_baseline` 五维（#33/#34）」
- `Layer N` 泄漏 → 「去掉文本里的 Layer N 标题：映射时应改名到四槽（#30 §0.1）」
- `insufficient + inferred` → 「basis 里不要写 insufficient（#29 F5）」

## CLI

```bash
alpha persona check <id>
alpha persona check <id> --json
alpha persona check <id> --probe    # 需引擎运行
```

退出码：`fail` → 1，其余 → 0（便于脚本阻断）。

实测输出：

```
[alpha] 体检结果: warn
  structure warn     问题 0 项，缺口 4 项，口头禅 0 条
  privacy   pass     0 处命中
  load      skipped  无法导入 PersonaLoader：No module named 'persona'
  probe     skipped  未启用（加 --probe 且需引擎运行）
建议：
  - 补 expression.rhythm（说话节奏），它直接进 SOUL.md 的说话风格
  - 补 3 条以上口头禅，写入 expression.habits（#35 会灌进 style_memory）
  - 补 hard_rules（最高优先级行为规则），否则人格缺底线
  - 补 conflict.boundaries，避免角色在冲突场景失控
```

## 验证

```bash
python3 tests/test_persona_check.py   # 12 passed
python3 -m pytest tests/ -q           # 181 passed
```

覆盖：完整 profile 通过、空 profile 产生 gaps、`Layer N` 失败、`insufficient+basis` 失败、
隐私命中手机号与邮箱、加载降级、探针降级、判定聚合、fixes 可执行性、CLI 集成。

## 未完成

**风格探针（probe）只有骨架**：实现需要运行中的引擎与可用 provider（Codespace + `.env`），
沙箱不具备。当前一律 `skipped` 并说明原因。补齐后应与 #44 的 20 轮验收共用观测口径，
避免出现两套判定标准。
