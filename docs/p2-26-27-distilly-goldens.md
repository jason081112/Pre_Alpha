# P2-26 / P2-27 Distilly 合成 Skill 跑通

对应看板 #26、#27。目标：用 #25 的合成聊天样本，把 Distilly 的
`intake → analyze → build → skill_writer` 跑通，产出可回归的 colleague /
relationship Skill。

## 结论

- #26 colleague：已完成，生成 `demo-colleague` Skill。
- #27 relationship：已完成，生成 `demo-relationship` Persona Skill。
- 两套 golden 已提交到 Pre_Alpha：
  - `tests/fixtures/golden/demo-colleague/`
  - `tests/fixtures/golden/demo-relationship/`
- 没有提交到 `alpha-cloud`：它们是合成测试资产，不是真实用户数据或商业角色资产。
- 没有提交 key、原始聊天记录、绝对路径。

## 环境

| 项 | 值 |
|---|---|
| Codespace | `supreme-goggles-5r5v45w4xqqf44wv` |
| 仓库基线 | `c50be40` |
| Distilly | `0afc45c29c360ba7bc8260f1bb0bf34e9155f4fa`（`dot-skill`） |
| Writer | `vendor/distilly/tools/skill_writer.py` |
| Artifact authoring | OpenCode agent 按 Distilly prompt 生成 |
| Provider smoke | `ollama-cloud/gemma4:31b` |
| GLM | Codespace 未配置 GLM provider；按约定回退到 `gemma4:31b` |

Codespace 里只有 `DEFAULT_PROVIDER=openai`、`DEFAULT_MODEL=gemma4:31b` 和
`OPENAI_API_KEY`；没有 GLM/Zhipu 的 provider 或成功路由记录。因此两组 smoke
实际都使用 `ollama-cloud/gemma4:31b`，不是真正的双模型对比。

## 生成结果

两套都生成 7 个标准产物：

```text
SKILL.md
work.md
persona.md
work_skill.md
persona_skill.md
manifest.json
meta.json
```

### #26 demo-colleague

- 来源：`tests/fixtures/chats/colleague_zh.canonical.json`
- Work 侧：429 / `Retry-After`、并发测试、文档同步、日志只留 `last4`
- Persona 侧：短句、先结论、直接指出缺口、重视回归
- 七层结构：Layer 0–5 + Correction Log
- 质量：未知维度用 `insufficient source material`，没有编造公司/技术栈

### #27 demo-relationship

- 来源：`tests/fixtures/chats/relationship_zh.canonical.json`
- 六层结构：Layer 0–5 + Correction Log
- 覆盖：
  - 表达 DNA：短句、嘴硬、行动关心
  - 情绪触发：被忽略、重复失约
  - 冲突修复：先冷一下，再要求具体补救
  - 记忆签名：下雨、伞、地铁口
- Work 轨道没有材料，明确标记 `insufficient source material`，没有把关系样本硬编成工作经历

## Persona smoke

用生成的 `persona_skill.md` 直接调用 `ollama-cloud/gemma4:31b`，两个 family
各跑两轮、每轮 5 个用户回合：

| family | pass | provider/model | 回合 | 错误 | 泄露标记 | 耗时 |
|---|---|---|---:|---:|---|---:|
| relationship | GLM 回退 | `ollama-cloud/gemma4:31b` | 5 | 0 | 无 | 6.95s |
| relationship | Ollama Cloud | `ollama-cloud/gemma4:31b` | 5 | 0 | 无 | 5.07s |
| colleague | GLM 回退 | `ollama-cloud/gemma4:31b` | 5 | 0 | 无 | 4.67s |
| colleague | Ollama Cloud | `ollama-cloud/gemma4:31b` | 5 | 0 | 无 | 4.71s |

合计 **20/20 回合成功**，没有出现 `【内心独白】`、`【表达方式】`、`Layer 0`
或 `source material insufficient` 的泄露。

代表性回复：

```text
relationship / 我今晚加班，可能又赶不上吃饭了。
→ 算了，你忙。

relationship / 我不是故意的。
→ 我不是在说你故意。我是觉得你每次都这样。

colleague / 日志里能打完整 key 吗？
→ 不行。只留 last4。

colleague / 文档要不要更新？
→ 更新。别让人猜。
```

风格符合预期：relationship 短、嘴硬、要求具体行动；colleague 直接、先结论、
关注失败路径和回归。

## 结构校验

新增 `tests/test_golden_skill.py`，检查：

- 8 个文件（7 个产物 + `PROVENANCE.md`）齐全
- `meta.json` 的 `synthetic`、family、语言、fixture 来源
- `manifest.json` 的 entrypoints 存在
- `SKILL.md` 的 PART A / PART B
- 两套 Persona 的 Layer 0–5 与 Correction Log
- 无绝对路径、`sk-*` key、常见 PII

本地结果：`5 golden tests passed`。
`tests/test_fixtures.py`：`6 fixture tests passed`。
两者都已加入 `public-guard`。

## 产物哈希

见 [`docs/data/p2-26-27.json`](data/p2-26-27.json)。

## 复现

```bash
# Codespace / 开发机
scripts/dev/fetch_distilly.sh
cd vendor/distilly

python3 tools/skill_writer.py \
  --action create \
  --character colleague \
  --slug demo-colleague \
  --name "林舟" \
  --meta /tmp/colleague_meta.json \
  --work /tmp/colleague_work.md \
  --persona /tmp/colleague_persona.md \
  --base-dir ./skills/colleague

python3 tools/skill_writer.py \
  --action create \
  --character relationship \
  --slug demo-relationship \
  --name "小岚" \
  --meta /tmp/relationship_meta.json \
  --work /tmp/relationship_work.md \
  --persona /tmp/relationship_persona.md \
  --base-dir ./skills/relationship
```

生成后不要把 `skills/` 提交；只把合成 golden 复制到 `tests/fixtures/golden/`。

## 已知限制

- 没有真正的 GLM 运行；两组 smoke 都是 `gemma4:31b`，双模型对比留到配置 GLM 后。
- 这次 artifact authoring 由 OpenCode agent 完成，provider 只用于 Persona smoke；
  不是“provider 自动生成整份 Skill”。
- colleague 的 manager/report 关系、技术栈等没有材料，已标记不足，不应把推断当事实。
- `meta.json` 的 `source_context.is_real_person` 是 Distilly preset 默认值；
  以 `synthetic: true` 和 `PROVENANCE.md` 为准。
- golden 的 `created_at` 是生成时间；回归测试只校验结构，不比对时间戳。
