# P3-42 · `alpha profile2soul`

对应看板 #42。产出 `cli/profile2soul_cmd.py`、`tests/fixtures/profiles/xiaolan.json`、
`tests/test_cli_profile2soul.py`（17 项），并修掉两个上游遗留缺陷。

## 用法

```bash
# dry-run：只预览 + diff，不写文件（默认）
alpha profile2soul tests/fixtures/profiles

# 写入
alpha profile2soul tests/fixtures/profiles --out .data/soul --write

# CI 模式：有任何变更或告警 → 退出码 1
alpha profile2soul tests/fixtures/profiles --out .data/soul --check

# 严格模式：gender/lang 回落即失败
alpha profile2soul <dir> --strict
```

`<dir>` 下所有 `*.json` 都会处理，也接受直接传单个 `profile.json`。输出路径是
`<out>/<persona_id>/SOUL.md`。

## 做了什么

`#32` 只交付了 frontmatter（身份字段），body 是空的 —— 那样的 SOUL.md 喂给 `PersonaLoader`
会落到 `raw_content`，人格实际进不了 prompt。本 issue 补齐 body。

| 段落（H2） | 数据来源 |
|---|---|
| 性格 | `logic.opens_up_when / pulls_away_when / defends_by / shows_care_by / priorities` |
| 说话风格 | `expression.rhythm / habits / examples` |
| 背景故事 | `relation.label / status / defining_facts` + `memory_signature` |
| 行为规则 | `hard_rules` + `conflict.style / silence / repair / boundaries` |

四个标题严格取 loader 能匹配的字面量（中文 `性格/说话风格/背景故事/行为规则`，英文对应），
**不会**出现 `## Layer N`（该形式 loader 不识别，会让人格静默丢失而加载仍成功）。

材料缺失时**不编造**：该行/该段直接跳过，并在 warnings 里写明缺哪段、后果是什么。
三个核心段（性格/说话风格/背景）全空时额外告警：

```
⚠ 三个核心段全空 → loader 会退到 raw_content，人格实际不会进 prompt
```

## 顺手修掉的两个缺陷

### 1. `profile_from_dict` 反序列化不对称（`bridge/model.py`）

`to_dict` 把 `list[Asserted]` 写成 `list[dict]`，但 `_build_dataclass` 只对**非 list** 调用
`_asserted_from_dict`，于是 `logic.priorities`、`conflict.boundaries`、`relation.defining_facts`
读回来是原始 dict —— 渲染时直接漏成 `{'kind': 'asserted', 'value': ...}`。

修法：list 分支逐项还原。测试 `test_list_deserialization_is_symmetric` 锁死。

### 2. 隐性缺失不告警（`bridge/profile2soul.py`）

`put()` 里 `asserted is None` 直接 return，而 `gender` 字段整个没给时正是 `None`
（不是 `Asserted.missing`）。结果：**真正会发生回落的情况反而没有任何 warning**，
`--strict` 也拦不住。

修法：`None` + 键有 loader 默认值 → 同样告警。测试 `test_strict_mode_rejects_fallback` 锁死。

## 关键验证：产物真能被 loader 加载

不是断言"看起来对"，而是把生成的 SOUL.md 交给真 `PersonaLoader`：

```python
loaded = PersonaLoader(str(tmp)).get("xiaolan")
assert loaded.personality and loaded.speaking_style and loaded.background
```

三个核心段必须非空 —— 这是区分"真解析"与"落到 raw_content"的唯一硬判据。
（vendor 缺失时跳过，完整校验留给 #41。）

## 输出样例（小岚）

```markdown
---
name: 小岚
name_zh: 小岚
gender: female
mbti: INFP
lang: zh
---

## 性格

- 愿意开口的时候：对方先把自己的难处说出口
- 会退开的时候：对方用忙碌当理由跳过承诺
- 防御方式：把话收短，不给对方继续解释的台阶
- 表达在意的方式：叮嘱具体的小事，比如穿够衣服、到了报平安
- 在意的事：说话算数、被认真对待而不是被快速安抚

## 说话风格

- 说话节奏：短句，常在半句停住，留话给对方接
- 语言习惯：把委屈说成陈述句，不提问、关心裹在叮嘱里，不直接说在意
- 例句：
  - 对方又临时失约：“算了，你忙。”

## 背景故事
## 行为规则
```

## 验证

```bash
python3 tests/test_cli_profile2soul.py   # 17 passed
python3 -m pytest tests/ -q              # 225 passed
```

覆盖：四段齐全、标题白名单、无 `Layer N` 泄漏、list 字段渲染为文本、缺段落告警、
部分字段保留、英文标题、strict 拒绝回落、目录/单文件定位、diff 形状、
dry-run 不写、写入幂等、`--check` 退出码、路径不存在、反序列化对称、真 loader 加载。

## 边界

- 只处理 `PersonProfile`（我方 schema）→ SOUL.md。Distilly 六层原始产出走 `#30` 的
  `scripts/dev/map_persona_to_soul.py`，两者不混。
- `corrections` 不在 SOUL.md 里（属纠错层，见 #39）。
- golden 快照回归是 #43，本 issue 只保证"一次生成是对的、重复生成是幂等的"。
