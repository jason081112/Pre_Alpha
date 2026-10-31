# P3-38 · 情绪触发 / 冲突 / 修复 → critic 特征 + phase_threshold

对应看板 #38。产出 `bridge/triggers.py`、`alpha persona triggers`、`tests/test_triggers.py`（23 项）。

## 三样产出

### 1. `critic_persona_section()` —— 最直接的落点

critic prompt 里本来就有 `persona_section` 槽位（见 `docs/p0-05-06`），用来回答「同一句话对这个
角色的需求满足感和挫败增量」。本模块生成的就是这段文本，内容只有四类：触发点、沉默/修复、边界、
判定口径。

```
角色：小岚
触发点：会退开：对方用忙碌当理由跳过承诺；会开口：对方先把自己的难处说出口；冲突时：先收短，再点破，不主动升级但也不退让
沉默/修复：用「嗯」「先这样」结束一轮，情绪没过去只是不再说；看对方有没有实际动作，而不是听道歉的话
边界：不接受拿忙碌略过已经答应的事
```

**材料不足时不注入无意义文本**：没有任何冲突/修复描述的 profile，section 会明确写
`（材料不足：未提供冲突与修复描述，不做角色特化判断）`，并把 `usable` 置为 `false`。
否则等于给 critic 一条"假装有个性"的提示。

### 2. `signal_priors()` —— 8 个行为信号先验

口径对齐 `signal_buckets.yaml`（directness / vulnerability / playfulness / initiative /
depth / warmth / defiance / curiosity）。

**只采信可数的结构事实，不用关键词猜性格**：

| 信号 | 依据 | 为什么这么定 |
|---|---|---|
| 直接度 | 例句平均字数 | 可测量；短句 ≈ 直给 |
| 坦露度 | `logic.opens_up_when` 是否存在 | 有无开口条件是结构事实 |
| 玩闹度 | 仅 `meta.playfulness` 显式给定 | 结构上测不出，不猜 |
| 主动度 | `defends_by` / `shows_care_by` 非空数 | 有具体主动行为才给分 |
| 深度 | `priorities` 条数 | 可数 |
| 温暖度 | `shows_care_by` 是否存在 | 有明确表达方式才算 |
| 倔强度 | `boundaries` + `hard_rules` 条数 | 可数 |
| 好奇度 | 例句中问句比例 | 可测量 |

第一版用过关键词词表，结果小岚（短句、收短、不提问）被判成「直接度 0.75」，明显违反事实。
按 #29 的教训（正则启发式信噪比低，65 个幻觉候选无一为真），改为结构事实优先：
**测不出来的就 `missing`，让引擎走中性处理，而不是吃一个编造的值。**

### 3. `phase_threshold()` —— 真正有区分度的一项

语义见 `docs/p0-04`：累积挫败超过阈值触发相位转移，**阈值越低越容易突变**。base 是引擎默认的 2.0。

| 规则 | 调整 | 依据 |
|---|---:|---|
| 压抑型（收短 / 不说 / 沉默 / 不主动升级） | +0.35 | 挫败不外发，积累更久才相变 |
| 当场外发型（当场 / 立刻 / 直接指出 / 不软化） | −0.40 | 不需要长时间累积 |
| 修复难（看实际行动 / 别扯上次） | +0.20 | 单次和解不足以清零 |
| 修复快（翻篇 / 不记仇） | −0.20 | 挫败衰减更快 |

输出截断到 `[0.8, 4.0]`，并在 basis 里写明"原始值已截断"。

实测：

| persona | phase_threshold | 依据 |
|---|---:|---|
| 小岚 | **2.35** | 压抑不外发，忍得更久 |
| 林舟 | **1.60** | 当场指出，不攒 |
| aria | missing | 未描述冲突反应，保持默认 2.0 |

## 已知局限（必须说清）

**`signal_priors` 对两个材料完整的人格几乎没有区分度**：小岚与林舟的 8 项里 7 项完全相同，
只差倔强度（0.65 vs 0.75）。

原因很直接：这套指标测的是**材料完整度**（有没有写、写了几条、句子多长），不是性格内容。
两个都写全了，测出来自然一样。

所以它的定位是「给 critic 一个带置信度的中性起点」+「与 critic 当场判定对比，偏差过大时作为
角色失真信号（#86 探针输入）」，**不是性格指纹**。真正区分人格的是正文内容与 seeds（#35），
以及 phase_threshold 这类由反应模式决定的参数。

要提升区分度，只能引入语义判断 —— 那需要模型，属于 #44 真机验证后的迭代方向，不在本 issue 假装解决。

## 用法

```bash
alpha persona triggers tests/fixtures/profiles/xiaolan.json
alpha persona triggers <persona_id>          # 从 persona 仓库读
alpha persona triggers <path> --json
```

## 验证

```bash
python3 tests/test_triggers.py   # 23 passed
python3 -m pytest tests/ -q      # 280 passed
```

覆盖：8 信号齐全、句长→直接度单调、无例句→missing、玩闹度仅在显式声明时给值、
问句比例→好奇度、边界数→倔强度、**每个有值的信号都必须带 basis**、值域、
压抑型升阈值 / 外发型降阈值 / 无描述保持 missing、截断、多证据累加、
驱力敏感度回落到 #33 校准、section 含触发点、英文分支、材料不足显式标注、`usable` 判定、CLI。

## 边界

- 未接真实 critic（需要模型调用），本 issue 只产出「要注入的内容」与「参数校准值」。
  critic 实际是否改善，归 #44 验收。
- `drive_sensitivity` 复用 #33 的 `drive_baseline`，没有新推断逻辑。
- 不写 `engine_params` 到 profile（那是 #33 的职责），本模块只输出可被写入的值与依据。
