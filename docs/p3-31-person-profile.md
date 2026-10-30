# P3-31 `PersonProfile` 数据模型

对应看板 #31。产出 `bridge/model.py`（dataclass + 校验）与 `bridge/profile.schema.json`。
本 issue 只定模型与规则，不做映射（映射见 #32–#38）。

## 为什么不是「直接把 golden 塞成 JSON」

P2 的四个结论决定了模型形态：

| 来源 | 结论 | 对模型的影响 |
|---|---|---|
| #28 | 六层槽位跨族语义不同（Layer 5 在两族是两个东西） | 不用 `layer_5` 这类字段名，改用语义名（`memory_signature` / `conflict.boundaries`） |
| #28 | 口头禅无频次、无权重 | `habits` 只有文本，**不设 weight 字段**（避免凭空造数） |
| #29 | 模型会写 `(Insufficient source material; inferred: ...)` | 三态表达 + 校验器禁止 `inferred` 与 `insufficient` 并存 |
| #30 | `## Layer N` 会被当结构标记泄漏 | 字段名与正文 H2 都不含 `Layer` |

## 核心设计：三态 `Asserted`

每个字段都包在 `Asserted[T]` 里：

| provenance | 含义 | 强约束 |
|---|---|---|
| `source` | 直接来自材料 | 必须有 `value` |
| `inferred` | 推断 | 必须有 `basis`，且 `basis` 不得出现 `insufficient` |
| `missing` | 材料不足 | 必须有 `reason`，且 `value` 必须为 `null` |

这样「不知道」是一等公民，不会被默认值或空白悄悄填掉。

## 字段清单

```text
PersonProfile
├── schema_version      "2.0"
├── persona_id          ^[a-z0-9_-]{1,64}$
├── identity            name(必填) / name_zh / age / gender / mbti / lang
├── relation            label / status / defining_facts[]        ← Layer 1
├── expression          rhythm / habits[] / examples[]           ← Layer 2
├── logic               opens_up_when / pulls_away_when /
│                       defends_by / shows_care_by / priorities[] ← Layer 3
├── conflict            style / silence / repair / boundaries[]   ← Layer 4
├── memory_signature    []                                        ← Layer 5
├── hard_rules          []                                        ← Layer 0
├── corrections[]       scene / wrong / correct                   ← 纠错层
├── engine              drive_baseline{5} / engine_params{11} / calibration
└── meta                dict（开放，但禁原文键）
```

## 校验规则

`bridge.model.validate_profile()` 返回错误列表（不抛异常，便于批量报告）。

| 规则 | 理由 |
|---|---|
| `inferred` 必须有 `basis` | 否则推断无法追溯 |
| `basis` 不得含 `insufficient` | #29 F5：既说材料不足又补推断，读者会以为有依据 |
| `missing` 必须有 `reason` 且 `value` 为 `null` | 防止「标了缺失又偷偷给值」 |
| `source` 必须有 `value` | 防止空断言 |
| `identity.name` 不能是 `missing` | 没有名字的人格无法落地 |
| drive 键 ∈ {connection, novelty, expression, safety, play} | 来自 OpenHer `genome_engine.py` |
| engine_params 键 ∈ 11 个白名单 | 来自 `drive_metabolism.py` + `personas/*/SOUL.md`；防止写出 loader 不读的悬空字段 |
| 任何层级禁止 `raw_text` / `messages` / `chat_log` / `chat_history` / `transcript` / `conversation` | 与 `docs/refine-api.md` 的 422 规则一致；蒸馏产物不应携带原文 |
| `corrections` ≤ 50、`habits` ≤ 20、`memory_signature` ≤ 200 | 纠错 50 条来自 Distilly `correction_handler.md`；其余为防止产物失控 |
| `persona_id` 小写、含字母数字连字符、≤ 64 | 直接作为 OpenHer persona 目录名 |

## 与 OpenHer 的边界

模型里 `engine.drive_baseline` / `engine.engine_params` 的值全部是 `Asserted`，
且默认 `missing`。原因：#28/#30 已确认这两块**没有权威标定材料**，
`#33` / `#34` 做的是人工标定 —— 标定未完成时，模型必须显式说"不知道"，
而不是落 0.5 之类的默认值。

## 验证

```bash
python3 tests/test_bridge_model.py     # 15 passed（含 negative cases）
```

覆盖的负面用例：`inferred` 无 `basis`、`basis` 含 `insufficient`、`missing` 带 `value`、
`source` 空值、非法 drive 键、非法 engine_param 键、`meta` 里塞 `raw_text`、`corrections` 超 50、
`persona_id` 大写/为空。

有 `jsonschema` 时额外做一次严格 schema 校验（CI 零依赖环境自动跳过）。

## 未覆盖（留给后续）

- 映射实现：`bridge/profile2soul.py` 仍是占位（#32 起）
- `drive_baseline` / `engine_params` 的标定规则（#33 / #34）
- `style_memory` 种子格式（#35，`seeds.bin` 格式仍未确认）
- `SHELL.md` 是否需要（#40，取决于 P0-2 结论）
