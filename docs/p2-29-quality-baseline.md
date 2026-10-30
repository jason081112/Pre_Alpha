# P2-29 质量基线：同一数据跑 3 次

对应看板 #29。口径 A（pipeline 驱动），双模型各 3 次，评估**结构稳定性**与**幻觉点**。

## 结论

1. **gemma4:31b 稳定**：三次 H2 结构完全一致（Jaccard 1.0），条目数波动 CV ≤ 0.14，元数据一致。
2. **gpt-oss:120b 不稳**：第三次把 Layer 0 标题改写成中文，结构 Jaccard 掉到 0.7778；Layer 1 条目数
   0/4/0（CV 1.41）。
3. **发现一处真实的规范性违规**：gemma 在 Layer 4 写 `(Insufficient source material; inferred: ...)`，
   既承认材料不足又补了推断内容 —— 违反"不把推断当事实"。
4. **自动幻觉启发式信噪比太低**：57 + 8 个候选里没有一个是真幻觉，全是构词与命名噪声。
   这一项必须记为方法论缺陷，不能当"零幻觉"的证据。

## 方法与配置

| 项 | 值 |
|---|---|
| 口径 | pipeline 驱动（A）：prompts 程序化喂给 LLM，`skill_writer.py` 组装 |
| 运行器 | `scripts/dev/run_distilly_pipeline.py` |
| 对比器 | `scripts/dev/compare_runs.py` |
| 输入 | `tests/fixtures/chats/colleague_zh.canonical.json`（3.9 KB，合成） |
| family | `colleague` |
| 步骤 | intake → work_analyzer / persona_analyzer → work_builder / persona_builder → skill_writer |
| 采样 | temperature 0.7，max_tokens 8000 |
| 模型 | `gemma4:31b`、`gpt-oss:120b`（均为 ollama-cloud / OpenAI 兼容端点） |
| 次数 | 每模型 3 次，共 6 次（每次 18–22 秒） |
| 基线 commit | `da69a4e` |
| 阈值 | 结构 Jaccard ≥ 0.8；条目 CV ≤ 0.30；文件齐全与元数据一致须 100% |

Distilly 本身没有自动 pipeline（`bin/distilly.mjs` 只是安装器），所以"pipeline 驱动"是
**按 `prompts/flows/create.md` 程序化复刻**。这一点必须写明：它测的是「prompts + 模型」的
可重复性，**不等于**原设计里 agent 手写产物的表现。

## 稳定性结果

| 指标 | gemma4:31b | gpt-oss:120b |
|---|---|---|
| 文件齐全（8 文件） | 3/3 | 3/3 |
| 元数据一致 | 100% | 100% |
| H2 Jaccard（最小） | **1.0** | **0.7778** |
| 条目 CV（最大） | 0.14（Layer 5） | **1.41**（Layer 1） |
| 3-gram 漂移均值 | 0.474 | 0.252 |
| 判定 | **pass** | **warn** |

### 条目规模

| Layer | gemma4:31b | gpt-oss:120b |
|---|---|---|
| Layer 0 | 5 / 5 / 5 | 9 / 10 / 8 |
| Layer 1 | 0 / 0 / 0 | 0 / 4 / 0 |
| Layer 2 | 4 / 5 / 4 | 22 / 17 / 27 |
| Layer 3 | 4 / 4 / 3 | 15 / 10 / 9 |
| Layer 4 | 0 / 0 / 0 | 14 / 10 / 10 |
| Layer 5 | 6 / 7 / 5 | 7 / 8 / 6 |

gpt-oss 产出粒度细一个量级，代价是波动也大一个量级。Layer 1 / Layer 4 在 gemma 里是纯段落
（无 bullet），所以计为 0 —— 这是标记方式差异，不是内容缺失。

## 逐条发现

**F1 · gemma4:31b 结构完全稳定（pass）**
三次 H2 集合逐字一致，条目数小幅浮动，无缺文件、无元数据漂移。

**F2 · gpt-oss:120b 标题语言漂移（warn）**
第三次把 `## Layer 0: Core Personality (highest priority; never violate under any circumstances)`
写成 `## Layer 0: Core Personality (最高优先级；绝不可违背)`。
结构比对因此判为缺少一节 + 多出一节。**同一模型、同一 prompt、同一温度，输出语言都不稳**。

**F3 · gpt-oss:120b 粒度不稳（warn）**
Layer 1 条目数 0 / 4 / 0。同一模型三次里，两次写成段落、一次写成列表。

**F4 · 详细度与漂移正相关（info）**
gpt-oss 的 Layer 2 条目是 gemma 的 5 倍，但文本 3-gram 相似度只有 0.252（gemma 0.474）。
写得多，重复性差。

**F5 · 「假 insufficient」违规（violation）**
gemma 的 Layer 4 出现：

```text
(Insufficient source material; inferred: Direct, report-oriented...)
```

前半句符合约定，后半句直接补了推断。**这比完全不标注更危险** —— 读者会以为推断有依据。
#26/#27 的 golden 里没有这种写法（那是人工把关的），pipeline 会自动犯。
建议：#31 的校验器把 `insufficient` 与 `inferred` 同时出现判为违规。

**F6 · 自动幻觉启发式失效（method）**
57（gemma）+ 8（gptoss）个候选，逐条查看：

| 类型 | 例子 | 实际是什么 |
|---|---|---|
| 连字符构词 | `self-explanatory`、`report-oriented`、`stability-first` | 正常英文复合词 |
| 命名/元数据 | `lin-zhou`、`gpt-oss`、`user-invocable`、`in-repo` | slug、模型名、文档用词 |

**没有一条是真幻觉。** 现有的实体/数字正则只能当"检索器"用，判定必须人工。
#29 的"幻觉点"因此只能给**零命中**这个弱结论，不能说"幻觉率 0"。

**F7 · 未检出无源数字（info）**
年龄、年限、日期、金额四类规则零命中。这是正向信号，但受样本量限制。

## 判定

| 模型 | 结果 | 理由 |
|---|---|---|
| gemma4:31b | **pass** | 结构 1.0、条目 CV ≤ 0.14、元数据一致、文件齐全 |
| gpt-oss:120b | **warn** | 结构 0.7778 < 0.8；Layer 1 条目 CV 1.41 > 0.30 |

**基线结论**：如果 P3 之后要做回归对比，**用 gemma4:31b 做基线模型**——它的可重复性足够
支撑"只有 prompt 变了才应该出现差异"的推断。gpt-oss:120b 适合做"更丰富但需人工把关"的对照。

## 限制

1. 两个模型走同一 provider（ollama-cloud），不代表跨 provider 稳定性。
2. 只覆盖 colleague 一个 family、一个 fixture；relationship / celebrity 未验证。
3. 3 次样本量小，CV 只能作方向性参考，不是统计显著性证据。
4. 程序化复刻与 agent 手写产物的差异未量化。
5. 幻觉检测的自动部分信噪比不足（F6），人工判读只覆盖 65 个候选。

## 复现

```bash
# Codespace / 有 Distilly 与 provider 的环境
set -a; . vendor/OpenHer/.env; set +a
python3 scripts/dev/run_distilly_pipeline.py \
  --fixture tests/fixtures/chats/colleague_zh.canonical.json \
  --distilly vendor/distilly --character colleague \
  --slug qa-gemma-1 --name 林舟 --model gemma4:31b --out runs/gemma4-1
# 三次后
python3 scripts/dev/compare_runs.py runs/gemma4-1 runs/gemma4-2 runs/gemma4-3 \
  --fixture tests/fixtures/chats/colleague_zh.canonical.json \
  --label gemma-1 --label gemma-2 --label gemma-3 --out /tmp/cmp-gemma.json
```

数据快照：`docs/data/p2-29.json`（含 6 次 persona.md 的 sha256、指标、发现清单）。

## 对 P3 的输入

1. **选 gemma4:31b 作为回归基线模型**，gpt-oss 作丰富度对照。
2. **校验器要拦 `insufficient + inferred` 共存**（F5）。
3. **标题语言要固定**（F2）：colleague 族模板是英文标题，可在 `skill_writer` 侧做 schema 校验。
4. 幻觉检测需要更强的判据（实体词表 + 人工），现有正则只做检索。
