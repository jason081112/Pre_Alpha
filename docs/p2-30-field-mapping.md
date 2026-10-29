# P2-30 字段对照表：Distilly 产出 → 内部 schema

对应看板 #30 的 **A 部分**（B 部分端到端验收见 issue 与本文件 §7）。

上游输入：`docs/p2-28-persona-structure.md`（结构归档）。
本文只写**可核实的转换规则**；#28 标注的 `insufficient source material` 在此保持"待确认"，
不填默认值。

## 0. 判定的三个前提（来自 `persona/loader.py` 实际行为）

### 0.1 正文只认 4 个 H2，其余内容进 `raw_content`

`PersonaLoader.SECTION_MAPPING` 把 H2 标题转小写后精确匹配：

| 目标字段 | 匹配标题（小写后） |
|---|---|
| `personality` | `性格`、`personality` |
| `speaking_style` | `说话风格`、`speaking style` |
| `background` | `背景故事`、`background`、`背景` |
| `behavioral_rules` | `行为规则`、`behavioral rules`、`rules` |

`## Layer 0: Core Relational Rules` 小写后为 `layer 0: core relational rules`，**不匹配任何键**
（`_parse_sections` 用它做 dict 查找，不是前缀匹配）。该节因此不进任何结构化字段。

### 0.2 `raw_content` 只在结构化节全空时才会拼进 prompt

`build_system_prompt_section()`：

```python
if not any([self.personality, self.speaking_style, self.background]):
    if self.raw_content:
        parts.append(f"\n{self.raw_content}")
```

**这是本次对照最重要的发现**：只要 `说话风格` 或 `背景故事` 任一非空，`raw_content`
就被丢弃。也就是说 —— **把六层原样塞进 `SOUL.md`，内容会静默消失**，模型看到的 prompt
里既没有 Layer 0 也不会有 Layer 3/4/5。映射必须把六层内容**改名或合并**到四个合法 H2。

### 0.3 注释与实现不一致：`mbti` / `tags` 实际会被注入

`persona/personas/*/SOUL.md` 的注释把 `mbti`、`tags`、`bio` 标为 "Display（仅 UI 展示，不注入 prompt）"，
但 `build_system_prompt_section()` 实际注入了 `name` / `age` / `gender` / `mbti` / `tags`
（`tags_zh` 优先）。**只有 `bio` 确实不注入。**

结论：`MBTI` 与 `tags` 是**人格内容的一部分**，不是纯展示字段，映射时不能当元数据随手填。

## 1. 主对照表

判定标记：**D** = direct（可直通）、**L** = lossy（需转换）、**M** = missing（无落点）。

| # | Distilly 来源 | 目标字段 | 判定 | 转换规则 |
|---|---|---|---|---|
| 1 | `meta.json` → `display_name` / Persona H1 标题 | frontmatter `name` | D | 取 `display_name`；H1 的 `— {family} Persona` 后缀剥离 |
| 2 | `meta.json` → `profile.age_range` | frontmatter `age` | L | `age_range` 是区间字符串（如"未提供"），`age` 要 int；**未提供时留空，不猜** |
| 3 | `meta.json` → `profile.gender` | frontmatter `gender` | L | 值可能是"未提供"；loader 默认 `female`。**未提供时不要落成 `female`**，应留空并由上层决定 |
| 4 | `meta.json` → `classification.language` | frontmatter `lang` | D | `zh-CN` → `zh`；其它同理取主语言码 |
| 5 | 无对应 | frontmatter `name_zh` | M | 需要时取 `display_name`（中文名） |
| 6 | `meta.json` → `classification.tags` / `tags` | frontmatter `tags.{en,zh}` | L | 两个 tags 数组语义不同（分类 vs 特质），需人工取舍；loader 会注入 prompt |
| 7 | `meta.json` → `mbti`（产物无此字段） | frontmatter `mbti` | **M/S** | 六层里不含 MBTI；`insufficient source material`，**留空** |
| 8 | 无对应 | frontmatter `bio` | M | 可用层级摘要生成，但属新写内容，需人工过目 |
| 9 | Layer 1「身份/关系」叙述 | 正文 `## 性格`（`personality`） | L | 见 §2 R1：把 Layer 1 + Layer 3 合并成散文，不得保留 `Layer N` 标题 |
| 10 | Layer 2 `Rhythm` / `How You Talk` | 正文 `## 说话风格`（`speaking_style`） | D | 段落直贴（去掉 H3 标题） |
| 11 | Layer 0 硬规则 | 正文 `## 行为规则`（`behavioral_rules`） | L | 见 §2 R2：逐条转写，首行加"最高优先级，任何情况下不得违反" |
| 12 | Layer 4 `Boundaries` / Layer 5 边界 | 正文 `## 行为规则`（续） | L | 与 #11 合并到同一节，边界放末尾 |
| 13 | `General Behavior Principles` | 追加到 `behavioral_rules` 末尾 | L | 属元指引，非人格内容；**relationship 族是否保留待 #29 结论** |
| 14 | Layer 2 `Signature phrases` / `Catchphrases` | `seeds`（`style_memory`） | L | 见 §2 R3：等权列表，无权重来源 |
| 15 | Layer 2 `Example replies` / `What You Would Say` | `seeds` + `speaking_style` | L | 场景标签保留为 seed 的 `scene`；正文里可作文例 |
| 16 | Layer 5 `Memory Signature` | `memories[]` | L | 见 §2 R4：`salience` 无来源，标注待定 |
| 17 | Layer 3 `Emotional Logic` | `drive_baseline` 五维 | M | **无标定规则**，不填；见 §3 待确认 1 |
| 18 | Layer 4 `Conflict*` / `Repair*` | `engine_params.{phase_threshold,temp_coeff}` | M | 无数值对应证据；见 §3 待确认 2 |
| 19 | `Correction Log` 条目 | 运行期 critic 输入 | M | 消费路径未核实；见 §3 待确认 3 |
| 20 | `work.md` 全篇 | 无 | M | 工作侧不进陪伴引擎，由宿主 Agent 读取 |
| 21 | `manifest.json` / `meta.json` / `PROVENANCE.md` | 无 | M | 打包与溯源元数据，不注入 |

## 2. 转换规则明细

### R1 · Layer 1 + Layer 3 → `## 性格`

```text
## 性格

{name} 与用户的关系是 {relationship_subtype}；当前状态：{current_status}。
关系建立在：{layer1 列表改写为一句}。

{opening 条件} 时会打开；{pulling away 条件} 时会退开。
防御方式是 {defends}；表达关心靠 {shows care}。
```

要求：**不得保留 `Layer N` 字样**（否则可能作为结构标记泄漏，B 部分验收会查这一点）。

### R2 · Layer 0 + Layer 4 Boundaries → `## 行为规则`

```text
## 行为规则

以下规则优先级最高，任何情况下不得违反：

- {Layer 0 条目 1}
- ...

边界：
- {Boundaries 条目}
```

末尾按 §1 #13 追加 `General Behavior Principles` 的 5 条（若 #29 结论为保留）。

### R3 · 口头禅 → seeds

```json
{"persona_id": "<id>", "seeds": [{"text": "算了，你忙。", "scene": null}, ...]}
```

- `weight` 字段**不在本阶段生成**：Distilly 不产频次，`insufficient source material`；
- `seeds.bin` 的序列化格式同样未确认（#28 §5.4），本阶段只产出上述 JSON，
  由适配器在 B 部分验收时决定如何喂给 `genesis_seed`。

### R4 · Memory Signature → `memories[]`

```json
{"summary": "下雨天、伞、地铁口", "salience": null, "tags": ["memory_signature"]}
```

`salience` 必填（Alpha schema 要求 0..1），但无来源。**不得填 0.5 之类的默认值**：
本阶段保留 `null` 并让校验器拒绝，或由人工标注后填入。选哪条由 B 部分决定。

## 3. 承接 #28 的 7 项待确认

| # | 待确认 | 对本文的影响 |
|---|---|---|
| 1 | Layer 3 → `drive_baseline` 的标定规则 | 第 17 行判定为 M，不产出数值 |
| 2 | Layer 4 → `engine_params` 的数值对应 | 第 18 行判定为 M |
| 3 | `Correction Log` 运行期消费路径 | 第 19 行判定为 M |
| 4 | `seeds.bin` 序列化格式 | R3 只产出 JSON，不产出 bin |
| 5 | `SHELL.md` 正文是否注入 prompt | 本文不映射任何内容到 SHELL.md |
| 6 | 口头禅权重来源 | R3 不产出 `weight` |
| 7 | `General Behavior Principles` 是否应存在于 relationship 产物 | 第 13 行标注"待 #29 结论" |

## 4. 与 Alpha Persona v1（`persona.schema.json`）的关系

本文件描述的是 **Distilly → OpenHer `SOUL.md`** 的映射。Alpha Persona v1 是**中间表示**
（我方 JSON schema），两者的字段并非一一对应：

| Alpha Persona v1 | 本文件的落点 | 说明 |
|---|---|---|
| `display_name` | 第 1 行 → `name` | 直通 |
| `relationship` | 第 9 行 → `personality` 文本 | schema 有字段但 OpenHer 无对应 |
| `traits{}` | 无 | 第 17 行判定 M，`traits` 暂不可填 |
| `speech_style{tone,habits,examples,avoid}` | 第 10/14/15 行 | `habits` 对应口头禅 |
| `memories[]` | 第 16 行 / R4 | `salience` 无来源 |
| `boundaries[]` | 第 11/12 行 | 直通 |
| — | `behavioral_rules` 承载 Layer 0 | Alpha schema 缺硬规则字段（#28 §8 已记） |
| — | `Correction Log` | Alpha schema 无字段 |

结论：**Alpha Persona v1 是映射的中间层，不是终点**；直接落 OpenHer 需要额外字段，
这部分属 #31（`PersonProfile` 数据模型）范围。

## 5. 未覆盖与风险

1. **静默丢失**（§0.2）：若映射后 `SOUL.md` 只保留 `## Layer N` 标题，prompt 里将看不到
   任何人格内容，但 `PersonaLoader` 仍会"加载成功"。B 部分验收必须用
   "风格是否生效"判断，不能只看加载返回值。
2. **`tags` 被注入 prompt**（§0.3）：分类标签混进人格描述可能造成风格漂移。
3. **关系标签无落点**（第 9 行）：OpenHer 没有 relationship 字段，只能写进散文。

## 6. 本文件的校验

新增 `tests/test_field_mapping.py`（离线断言，不需要 OpenHer 运行）：

- 对照表 21 行齐全，每行有判定标记（D/L/M）；
- 目标字段只出现于 `loader.py` 实际读取的集合（防止写出悬空字段）；
- 7 项待确认完整保留；
- `docs/data/p2-30.json` 与本文行数、判定统计一致。

## 7. B 部分验收（已执行，2026-10-29）

### 7.1 环境

| 项 | 值 |
|---|---|
| 机器 | Codespace `supreme-goggles-5r5v45w4xqqf44wv`（2C / 8GB） |
| Python | 3.14.2（`vendor/OpenHer/.venv`） |
| OpenHer | `ef5b214` |
| 仓库基线 | `9a62b40` |
| 启动 | `vendor/OpenHer/run.sh --bg` → `uvicorn main:app` :8000 |
| provider | 取自 `vendor/OpenHer/.env`（`DEFAULT_PROVIDER` / `DEFAULT_MODEL` / `OPENAI_API_KEY` 均已配置） |

本地 Termux 未安装任何依赖，验收全程在 Codespace 内完成。

### 7.2 加载（B1 / B2）

`GET /api/personas` 返回含 `xiaolan`：

```json
{"persona_id": "xiaolan", "name": "小岚", "name_zh": "小岚",
 "age": null, "gender": "female", "mbti": null,
 "tags_zh": ["短句", "不直接示弱", "用行动关心", "需要具体回应"],
 "description": "你和用户的关系是：亲密朋友 / 暧昧关系"}
```

`age` / `mbti` 为 `null`，符合对照表「未提供不猜」。
`gender` 为 `female`：SOUL.md 未写该键，`loader` 回落默认值 —— 对照表第 3 行提到的已知损耗，
**此处实测确认会发生，且无法在映射层避免**。

### 7.3 三轮对话（B3）

命令：

```bash
vendor/OpenHer/.venv/bin/python scripts/dev/verify_persona_e2e.py \
  --base http://127.0.0.1:8000 --persona xiaolan --turns 3
```

| # | 用户 | 回复 | 字数 / 句 |
|---|---|---|---|
| 1 | 我今晚加班，可能又赶不上吃饭了。 | 又是加班啊？＼ 你得记得在公司找点东西垫垫肚子，别到时候饿得胃疼。＼ 忙完告诉我，我等你。 | 45 / 3 |
| 2 | 我不是故意的。 | 我知道啦，谁想加班啊。＼ 我没在怪你，只是心疼你。＼ 快忙吧，记得喝水，我一直都在。 | 42 / 3 |
| 3 | 我到家了，你路上小心。 | 到家了就好。＼ 快去洗个热水澡放松一下，剩下的交给我。 | 27 / 2 |

原始输出：`docs/data/p2-30-e2e.json`。

**口径偏离（必须记录）**：本轮走的是 OpenHer 原生 `/api/chat`，**没有经过** Pre_Alpha 接入层的
`/v1/chat/completions`。后者需要额外启动 `shell/` 并配置上游转发，本轮先验证引擎侧的
「加载 + 触发」。接入层透传能力已在 P1 #68 单独验收，完整链路留到 P3 桥接层落地后补一次。

### 7.4 判定

| 项 | 结果 | 依据 |
|---|---|---|
| PersonaLoader 能加载 | **PASS** | `/api/personas` 含 `xiaolan`，`description` / `name` / `tags_zh` 非空 |
| 无结构标记泄漏 | **PASS** | 三轮均未出现 `Layer N`、`【内心独白】`、`【表达方式】`、`insufficient source material`、`Correction Log`、残留 H2 |
| 风格生效 | **PARTIAL** | 见下 |

风格对齐情况：

- **达成**：短句（平均 12.7 字/句、2.7 句/轮）、行动关心（垫肚子、喝水、洗澡）、先结论后补充。
- **未达成**：源人格的「嘴硬 / 把话收短」没有体现 —— 出现了 `我等你`、`我一直都在`、`心疼你`
  这类**直接示弱**表达，与 Layer 2「不会直接说"我想你"」相反；口头禅（`算了，你忙` / `你自己想`）
  一次也没出现。

根因判断：三条**未映射项**恰好承载「嘴硬」这类非文本特征 ——
`drive_baseline`（defiance / safety 倾向）、`engine_params`（相变阈值）、`style_memory` seeds
（口头禅召回）。只靠四段文本，模型回落到通用的温和陪伴风格。这与对照表 §3 的判定一致，
不是映射写错，而是**缺口本身**。

### 7.5 验收过程中踩到的两个陷阱（供后续复用）

1. **session 复用会伪造出「人格崩坏」**
   `get_or_create_session` 按 `(client_id, persona_id, user_name)` 复用会话。固定 `client_id`
   时上一轮验收的历史残留，模型把新消息当成「又发一遍」，回复变成
   「你是不是复读机」「我拒绝在这个剧本里继续对话」。
   修复：`verify_persona_e2e.py` 每次运行生成唯一 `client_id`（`9a62b40`）。
2. **元数据里的占位标注会被模型当剧本**
   `meta.json` 的 `relationship_subtype` 含「（示例）」，原样写进人格描述后，模型据此认为
   在演示例，出现「我拒绝在这个剧本里继续对话」。
   修复：映射时清洗 `（示例）` / `(sample)` 一类标注（`f5bebcc`）。

这两点都说明：**「无泄漏」不等于「行为正确」**，验收必须看回复内容本身。

### 7.6 结论与后续

- B 部分按 issue 口径完成：加载 PASS、无泄漏 PASS、风格 PARTIAL。
- **不声称「风格完全生效」**。缺口对应 #28 的待确认 1/2/6 与 `seeds.bin` 格式（待确认 4），
  归入 P3 桥接层（#31–#44）。
- 未做：`drive_baseline` / `engine_params` 标定、`seeds` 注入、多轮长对话稳定性（属 #29）。
