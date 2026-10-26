# Synthetic Distilly Goldens

这两套 Skill 是 #26/#27 的合成验证产物，用于：

- 证明 Distilly `create` 流程能产出完整 Skill；
- 给 P3 `profile2soul` 提供稳定的输入样本；
- 做 Persona 结构与风格回归。

## 目录

| 目录 | 家族 | 来源 |
|---|---|---|
| `demo-colleague/` | colleague | `tests/fixtures/chats/colleague_zh.canonical.json` |
| `demo-relationship/` | relationship | `tests/fixtures/chats/relationship_zh.canonical.json` |

两套都来自**手工合成数据**，不对应任何真实人物、公司或聊天记录。

## 生成方式

- Distilly：`0afc45c29c360ba7bc8260f1bb0bf34e9155f4fa`（`dot-skill`）
- 写入器：`vendor/distilly/tools/skill_writer.py`
- Artifact authoring：OpenCode agent 按 Distilly 的
  `intake → analyze → build` prompt 生成
- Provider smoke：`ollama-cloud/gemma4:31b` 跑两轮（GLM 未配置，按规则回退到同一模型）
- 生成物未手改；`PROVENANCE.md` 记录来源和 smoke 结果

## 不要手改

需要更新时，重新走 Distilly `evolve` 或重新生成，再复制到本目录；不要直接改
`work.md` / `persona.md` / `SKILL.md`。`meta.json` 的
`source_context.is_real_person` 是 Distilly 的 preset 默认值，不代表真实人物；
以顶层 `"synthetic": true` 和本 README 为准。

## 校验

`tests/test_golden_skill.py` 会检查：

- 7 个产物是否齐全
- `meta.json` / `manifest.json` 的家族、语言、来源
- relationship 的 Layer 0–5 + Correction Log
- colleague 的 Layer 0–5 + Correction Log
- 无绝对路径、无 key、无 raw chat、无常见 PII
