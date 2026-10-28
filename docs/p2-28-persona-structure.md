# P2-28 产出物结构归档：六层人格 / 口头禅 / 纠错层

对应看板 #28。目标是把 Distilly 产出的结构固化成**字段级口径**，作为 #30
（Distilly → 内部 schema 字段对照表）与 #31（`PersonProfile` 数据模型）的输入。

本文只记录**可从产物或上游源码直接核实**的结构。无法核实的项一律标注
`insufficient source material`，不做推断填充。

## 结论

1. `persona.md` 的六层是**固定槽位**（Layer 0–5 + Correction Log + General Behavior
   Principles），两族的槽位号一致，但 **Layer 1 / 3 / 4 / 5 的语义按 family 不同**。
   不能把 Layer N 当成跨族通用的字段名。
2. **口头禅没有独立字段**，散落在 Layer 2 的三个子节里；也没有权重、频次或场景标签，
   无法直接转成 `style_memory` 的引力质量，只能等权注入。
3. **纠错层在 golden 里是空表**（`(No entries yet)`、`corrections_count: 0`）。
   条目格式来自 Distilly 的 `prompts/shared/correction_handler.md`，属权威二手来源，
   不是从产物反推的。
4. OpenHer 的 `SOUL.md` 只接受 **4 个正文 H2 槽位**（性格 / 说话风格 / 背景故事 /
   行为规则），六层中至少两层无直接落点，必须靠 `behavioral_rules` 或 `raw_content`
   兜住 —— 这是 #30 必须解决的损耗点。

## 证据与版本

| 来源 | 版本 | 用途 |
|---|---|---|
| Pre_Alpha | `7145b6d`（main） | golden 产物、`persona.schema.json`、结构测试 |
| Distilly | `0afc45c`（`dot-skill`） | 纠错层格式、prompt bundle 组成 |
| OpenHer | `ef5b214`（main，2026-06-27） | `persona/loader.py`、`engine/genome/*` 落点字段 |

OpenHer 侧只取人格与引擎相关源码（`persona/`、`engine/`、`memory/`、`agent/prompt_builder.py`），
不含 `persona/personas/*/idimage/` 下的多模态素材。

## 1. 产出物清单

| 文件 | 角色 | 结构要点 | 落 OpenHer 的哪一层 |
|---|---|---|---|
| `SKILL.md` | 合并入口 | `PART A：工作能力` + `PART B：人物性格`，内嵌 `work.md` 与 `persona.md` 全文 | 不入引擎，供宿主 Agent 读取 |
| `persona.md` | 人格正文 | Layer 0–5 + Correction Log + General Behavior Principles | `SOUL.md` 主体 |
| `work.md` | 工作侧 | Scope / Technical Standards / Workflow / Output Style / Knowledge Base / How to Apply | 无对应，工作侧留宿主 Agent |
| `persona_skill.md` | 人格可执行入口 | 与 `persona.md` 同构（少一层外层标题） | `SOUL.md` 主体 |
| `work_skill.md` | 工作可执行入口 | 与 `work.md` 同构 | 无对应 |
| `manifest.json` | 装配清单 | `entrypoints{default,work,persona}`、`artifacts[]`、`capabilities[]`、`engine`、`toolchain` | `PersonaLoader` 不读，属打包元数据 |
| `meta.json` | 生成元数据 | `schema_version: "3"`、`character`、`classification`、`profile`、`knowledge_sources`、`synthetic`、`generation`、`source_context`、`engine`、`artifacts` | 不注入 prompt |
| `PROVENANCE.md` | 溯源声明 | 合成声明、源 fixture、Distilly/SHA、writer、smoke 结果 | 不入引擎 |

## 2. 六层人格字段清单

两族的**槽位号一致、语义不同**。下表两列均为产物中的实际标题原文。

| 槽位 | relationship（demo-relationship） | colleague（demo-colleague） | 语义归类 |
|---|---|---|---|
| Layer 0 | `Core Relational Rules` | `Core Personality (highest priority; never violate under any circumstances)` | 最高优先级硬规则 |
| Layer 1 | `Relationship Context` | `Identity` | 身份与关系上下文 |
| Layer 2 | `Expression DNA` | `Expression Style` | 表达 DNA |
| Layer 3 | `Emotional Logic` | `Decisions and Judgment` | 内在逻辑（情绪 / 判断） |
| Layer 4 | `Conflict and Repair` | `Interpersonal Behavior` | 冲突与人际 |
| Layer 5 | `Memory Signature` | `Boundaries and Sore Spots` | 记忆签名 / 边界雷区 |
| 附加 | `Correction Log` | `Correction Log` | 纠错层 |
| 附加 | `General Behavior Principles` | `General Behavior Principles` | 使用指引（编号 5 条） |

### 2.1 子节（H3）

| 槽位 | relationship 子节 | colleague 子节 |
|---|---|---|
| Layer 1 | 无子节；正文为 `You are {name}.` / `Your relationship to the user is …` / `Current status: …` / `What defined this connection:` + 列表 | 无子节；身份字段化叙述 |
| Layer 2 | `Signature phrases` / `Rhythm` / `Example replies` | `Catchphrases and Frequent Words` / `How You Talk` / `What You Would Say` |
| Layer 3 | `Opens up when` / `Pulls away when` / `Defends themselves by` / `Shows care by` | `Your Priorities` / `When You Push Things Forward` / `When You Stall or Deflect` / `How You Say "No"` / `How You Handle Being Challenged` |
| Layer 4 | `Conflict style` / `Silence pattern` / `Repair pattern` / `Boundaries` | `With Managers` / `With Reports / Junior Colleagues` / `With Peers` / `Under Pressure` |
| Layer 5 | 无子节；列表（气味/场景/情绪余像） | 无子节；列表（不可触碰项） |

> 注意边界字段的位置不一致：relationship 的 `Boundaries` 在 **Layer 4 之下**，
> colleague 的边界本身就是 **Layer 5**。提取 `boundaries` 时不能只找 Layer 5。

### 2.2 形态

- Layer 0 / 5 与 `Boundaries`：无序列表，每条一句，陈述式（"当……时，你不会……"）。
- Layer 1：叙述句 + 关键词列表，混合中英（`You are …` 为英文模板）。
- Layer 2 `Signature phrases` / `Catchphrases`：短列表，短语带中文引号。
- Layer 2 `Rhythm` / `How You Talk`：单段落描述。
- Layer 2 `Example replies`：引用块，按场景标注（`> When feeling close:` → `> "到家说一声。"`）。
- Layer 3 / 4：短段落，每节 1–3 句。
- `General Behavior Principles`：编号列表，固定引用 Layer 0/2/3/4。

### 2.3 可解析性

- 六层与 `Correction Log` 均可按 `^## Layer N` / `^## Correction Log` 稳定切分，
  现有 `tests/test_golden_skill.py` 已按此断言。
- H3 子节标题**跨族不一致**（见 2.1），不能用同一套子节名做通用解析。
- `General Behavior Principles` **不属于 relationship 族的规范结构**：关系族的
  `prompts/relationship/persona_builder.md` 输出到 `## Correction Log`（`(empty)`）结束，
  其后的 `## Rules` 是给生成者的写作指令，不是人格章节；而
  `prompts/colleague/persona_builder.md` 才有 `## General Behavior Principles` 模板。
  但 `demo-relationship/persona.md` 实际含有这一节（内容与 colleague 模板同构，
  第 5 条改写为 `Prefer concrete care actions over abstract reassurance`）。
  结论：这是**跨族结构漂移**，因此**不应补测试断言** —— 断言会把偏差固化成契约。

## 3. 口头禅字段

### 3.1 现有载体

| 载体 | 位置 | 形态 | 数量（golden） |
|---|---|---|---|
| `Signature phrases` | relationship Layer 2 | 列表，6 条 | 6 |
| `Catchphrases and Frequent Words` | colleague Layer 2 | 列表 | 见产物 |
| `Example replies` / `What You Would Say` | Layer 2 | 场景 → 例句引用块 | 4 / 见产物 |
| `Knowledge Base` | work 侧 `work.md` | 列表，工作场景口头结论 | 6 |

### 3.2 缺口

- **无频次、无权重、无场景标签**。`style_memory` 的种子需要引力质量（`genesis_seed.seeds`
  与 KNN 检索），当前只能全部等权。
- 口头禅与"例句"是两个不同性质的东西（前者是高频短句，后者是场景应答），
  产物里未加区分，仅靠所在子节名区分。

### 3.3 提案（非事实，待 #31 决定）

`PersonProfile.speech_style` 增加：

```text
habits:      [{text, weight?, scene?}, ...]   # 来自 Signature phrases / Catchphrases
examples:    [{scene, text}, ...]             # 来自 Example replies / What You Would Say
```

权重的默认值来源 `insufficient source material`（Distilly 不产频次）。

## 4. 纠错层

### 4.1 产物现状

两份 golden 的 `persona.md` 均为 `(No entries yet)`，`meta.json` 的
`corrections_count` 为 `0`。**没有任何真实条目样本**。

### 4.2 权威格式（来源：Distilly `prompts/shared/correction_handler.md`）

人格纠错（写入 `persona.md` 的 `Correction Log`）：

```json
{"scene": "...", "wrong": "...", "correct": "..."}
```

多条：

```json
{"persona_corrections": [{"scene": "...", "wrong": "...", "correct": "..."}]}
```

工作纠错走另一条路：输出 **markdown patch**（可替换的 H2 段），
由 `skill_writer.py --work-patch` 写入，不产生 correction JSON。

维护规则（同源）：

- 每个文件最多 **50 条**纠正；
- 超限时合并语义相近项，措辞取**最近一次**；
- 新纠正与既有规则冲突时先提示，由用户决定覆盖还是并存。

### 4.3 判定

- 字段名 `scene / wrong / correct` 可直接采用；
- 但**运行时如何被消费** `insufficient source material`：OpenHer 侧只看到
  `engine/genome/critic.py` 存在，未见读取 `Correction Log` 的代码路径，
  需在 #30 继续核实（可能由 prompt 注入而非结构化读取）。

## 5. OpenHer 落点字段（源码核实）

### 5.1 `SOUL.md` frontmatter

`persona/loader.py` 与 `persona/personas/luna/SOUL.md` 显示三段式注释：

| 段 | 键 | 是否注入 prompt |
|---|---|---|
| Identity | `name`、`name_zh`、`gender`、`age` | **注入**（`build_system_prompt_section`） |
| Display | `mbti`、`tags{en,zh}`、`bio{en,zh}` | 不注入；`mbti`/`tags` 仅作展示与标签 |
| Engine seed | `genome_seed.drive_baseline`、`genome_seed.engine_params`、`genome_seed.signal_buckets` | 不注入，交给引擎动力学 |

`drive_baseline` 五维（`engine/genome/genome_engine.py: DRIVES`）：
`connection` / `novelty` / `expression` / `safety` / `play`。

`engine_params` 可在 SOUL.md 覆盖的键（`drive_metabolism.py` + luna 样例）：
`baseline_lr`、`elasticity`、`hebbian_lr`、`phase_threshold`、`connection_hunger_k`、
`novelty_hunger_k`、`frustration_decay`、`hawking_gamma`、`crystal_threshold`、
`temp_coeff`、`temp_floor`。

### 5.2 `SOUL.md` 正文

`PersonaLoader.SECTION_MAPPING` 只认四类 H2（中英双写）：

| 落点字段 | 可识别标题 |
|---|---|
| `personality` | `性格` / `personality` |
| `speaking_style` | `说话风格` / `speaking style` |
| `background` | `背景故事` / `background` / `背景` |
| `behavioral_rules` | `行为规则` / `behavioral rules` / `rules` |

未命中以上标题的正文全部落 `raw_content`，仅在结构化节**全空**时才被拼进 prompt
（`loader.py::build_system_prompt_section`）。

### 5.3 `SHELL.md`

`persona_id` 目录下可放 `SHELL.md`，loader **优先**从它读 `voice`（`description`），
缺失时回退到 `SOUL.md` frontmatter。`image` 等同理，属模态配置层。
SHELL.md 的**正文**是否注入 prompt：`insufficient source material`（loader 未读取其 content）。

### 5.4 风格记忆

`engine/genome/style_memory.py`：

- 表 `genesis_seed(persona_id, seeds)`，首次启动由 `seeds.bin` 自动导入（无需手工步骤）；
- 检索为 context-space KNN（`retrieve(context, top_k=3, lang_preference=...)`），
  带霍金质量衰减与语言偏好惩罚；
- `crystallize()` 在对话后写入新记忆条目。

`seeds.bin` 的**序列化格式**：`insufficient source material`（未在本仓库展开）。
这意味着 #30 无法直接产出可导入的 seeds 文件，只能产出结构化 JSON 供适配器转换。

### 5.5 信号维度

`SIGNALS`（8 个，0–1）：`directness`、`vulnerability`、`playfulness`、`initiative`、
`depth`、`warmth`、`defiance`、`curiosity`。可作为 Layer 2/3 描述转数值的候选维度，
但**无官方映射规则**（见 §7）。

## 6. Distilly → OpenHer 映射判定

判定三级：**direct**（字段直通）、**lossy**（需压缩/重新组织）、**missing**（无落点）。

| Distilly 字段 | OpenHer 落点 | 判定 | 说明 |
|---|---|---|---|
| Layer 0 硬规则 | `behavioral_rules`（正文） | lossy | 优先级语义需在文本里保留（"最高优先级，永不违反"） |
| Layer 1 身份 | frontmatter `name` / `name_zh` / `age` / `gender` | direct | 产物里 Layer 1 是叙述句，需先抽取字段 |
| Layer 1 关系标签 | 无字段；`bio` 或正文 | missing | OpenHer 没有 relationship 字段 |
| Layer 2 `Rhythm` / `How You Talk` | `speaking_style` | direct | 段落直贴 |
| Layer 2 `Signature phrases` / `Catchphrases` | `style_memory` seeds | lossy | 无权重，只能等权 |
| Layer 2 `Example replies` | seeds + `speaking_style` | lossy | 场景标签需保留 |
| Layer 3 情绪/判断逻辑 | `drive_baseline` 五维 | missing | 无标定规则，需人工 |
| Layer 4 冲突与修复 | `engine_params.phase_threshold` / `temp_coeff` | missing | 无数值对应证据 |
| Layer 4 `Boundaries` / Layer 5 边界 | `boundaries`（Alpha schema） | direct | 列表直搬 |
| Layer 5 `Memory Signature` | `memories` / seeds | lossy | 是"情绪余像"而非事实，salience 需自定 |
| `Correction Log` | 运行期 critic | missing | 消费路径未核实 |
| `General Behavior Principles` | 追加到 `behavioral_rules` 末尾 | lossy | 属元指引，非人格内容 |
| `work.md` 全篇 | 无对应 | missing | 工作侧不进陪伴引擎 |

## 7. 待确认清单（insufficient source material）

1. **Layer 3 → drive_baseline 的标定规则**：无官方或社区材料，任何数值都是自造。
2. **Layer 4 → engine_params**：同上，且 `phase_threshold` 的语义（情感相变阈值）
   与"冲突强度"是否同构未确认。
3. **`Correction Log` 的运行期消费路径**：`critic.py` 存在，但未确认读取人格纠错条目。
4. **`seeds.bin` 格式**：未展开，无法直接生成。
5. **`SHELL.md` 正文用途**：loader 只取 frontmatter 的 `voice`/`image`。
6. **口头禅权重来源**：Distilly 不产频次，权重只能由外部给定。
7. **`General Behavior Principles` 是否该存在于 relationship 产物**：关系族模板未要求
   （见 §2.3），但产物里有。归 #29 观察，不在 #28 下结论。

以上 7 项在 #30 的对照表中必须保持"待确认"状态，不得填默认值冒充结论。

## 8. 与 `persona.schema.json`（Alpha Persona v1）的对齐

| 六层 | Alpha Persona v1 字段 | 对齐情况 |
|---|---|---|
| Layer 0 | 无专用字段 | 缺；需 `hard_rules[]` 或并入 `boundaries` |
| Layer 1 | `persona_id`、`display_name`、`relationship` | 部分对齐 |
| Layer 2 | `speech_style{tone,habits,examples,avoid}` | 结构对齐，语义需映射 |
| Layer 3 | `traits{}`（0..1 自由键） | 无标定，暂不可填 |
| Layer 4 | `boundaries[]` | 部分对齐（relationship 族在 Layer 4，colleague 族在 Layer 5） |
| Layer 5 | `memories[{summary,salience,tags,occurred_at}]` | 结构可承载，语义需转换 |
| Correction Log | 无字段 | 缺；需 `corrections[{scene,wrong,correct}]` |

结论：**Alpha Persona v1 无法无损承载六层**。缺口两处（Layer 0 硬规则、Correction Log），
是否升 v2 由 #31 决定；本文只记录事实，不改 schema。

## 9. 校验

新增 `tests/test_persona_structure.py`，断言：

- 本文列出的六层标题与两份 golden 的 `persona.md` H2 实际一致；
- 两族 `Correction Log` 均为空表（与 `meta.json` 的 `corrections_count: 0` 一致）；
- 纠错层三字段名 `scene` / `wrong` / `correct` 与 Distilly prompt 原文一致；
- `docs/data/p2-28.json` 记录的源文件 sha256 与磁盘一致。

运行：

```bash
python3 tests/test_persona_structure.py
```

## 10. 复现

```bash
scripts/dev/fetch_distilly.sh          # 需 vendor/distilly
# OpenHer 人格/引擎部分按需拉取（不含 idimage 素材）
```

OpenHer 侧未提供 fetch 脚本（`vendor/` 已 gitignore），拉取方式待 P2 收尾后补。
