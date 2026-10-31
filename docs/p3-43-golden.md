# P3-43 · golden 快照回归

对应看板 #43。产出 `tests/golden/`（3 份 SOUL.md + manifest）、`scripts/dev/update_golden.py`、
`tests/test_golden.py`（11 项）。

## 范围修正：没有 SHELL 快照

issue 标题写的是「SOUL/SHELL 快照」。复核 `docs/p0-01-chat-agent-callchain.md:147`：`SHELL.md`
**不进对话 prompt**，只作 TTS 的 voice description。#40 已按 not planned 关闭、TTS 搁置。

因此本 issue 只锁 SOUL.md。快照目录里不出现 SHELL.md，并有测试守着
（`test_no_shell_md_generated`），防止将来有人照着标题把 SHELL 也生成出来。

## 三份 fixture

| persona_id | 领域 | 语言 | 材料特征 | 校验结论 |
|---|---|---|---|---|
| `xiaolan` | relationship | zh | 五层齐全，材料完整 | **pass** |
| `linzhou` | colleague | zh | 来源 `runs/gemma4-1/persona.md`（Distilly colleague 家族） | **pass** |
| `aria` | friend | en | **故意缺 logic**，只有表达与行为 | **warn** |

`aria` 存在的意义：把**退化路径**也锁住。它没有 `logic`，所以生成物里没有 `## Personality`
段 —— 这正是 #28 担心的静默丢失形态。有了快照，「生成器哪天把缺 logic 的 profile 也硬凑出一段
编造内容」这件事就会立刻红灯。

`linzhou` 用的是真实产出而非手编，让 colleague 家族（短句、零 emoji、结论先行、口头禅
「补上」「别让人猜」）的真实形态进入回归。

## 快照机制

```text
tests/golden/
├── manifest.json              # 每份的 sha256 / 字节 / 行数 / 校验结论 / 段落命中
└── soul/
    ├── xiaolan/SOUL.md
    ├── linzhou/SOUL.md
    └── aria/SOUL.md
```

核心断言是 `test_regeneration_is_byte_identical`：**当场重新生成，逐字节比对**。

快照的价值就在于让「生成逻辑变了」无法悄悄发生。改模板、改分隔符、改字段顺序、改段落标题，
都会立刻红。

## 更新流程

```bash
python3 scripts/dev/update_golden.py           # 重新生成 + 重写 manifest
python3 scripts/dev/update_golden.py --check   # 只比对，有漂移 → 退出码 1
```

改生成器后必须显式跑一次，并把 SOUL.md 的 diff 一起提交。**不接受**「快照跟着代码自动变」——
那等于没有快照。

## 顺带修掉的两处

### 1. 英文正文混进中文标点

`body_from_profile` 原来固定用全角 `：`、`、`、`；`，于是英文人格的 SOUL.md 出现
`- Rhythm：long, winding...`。改成随 `lang` 切换半角。快照把这条修正固定下来。

### 2. 单项 skipped 会拖垮整体判定

`_worst()` 原先按 `fail > warn > skipped > pass` 取最坏，导致「register 因无 `profile.json`
跳过」把整体压成 skipped —— 而纯 loader 部署是**合法形态**，这不是未验证。

改为：`fail > warn > pass`，只有**全部**检查都跳过时才整体 skipped。

### 3. 环境恢复脚本补 python 依赖

沙箱根文件系统重置会把 site-packages 一起清空，本次踩了两次（`No module named 'frontmatter'`）。
`scripts/dev/restore_env.sh` 新增 `restore_python_deps()`：检查 `frontmatter / yaml / jsonschema`，
缺了走阿里云镜像补装，装不上就明确告警「vendor 校验会降级为 skipped」。

## 验证

```bash
python3 tests/test_golden.py   # 11 passed
python3 -m pytest tests/ -q    # 257 passed
```

覆盖：manifest 与磁盘一致、fixture 集合与快照集合一致、重生成逐字节一致、哈希/字节/行数
一致、每份体检结论与 manifest 相符、pass 与 warn 两条路径都在、aria 的缺段被锁住、
标题白名单、无 `Layer N` / 空壳标记、无 SHELL.md、`update_golden --check` 通过。

## 边界

- 快照锁的是**生成结果**，不是「像不像这个人」（风格探针属 #86，仍为骨架）。
- 不涉及 seeds.bin（#35）与引擎参数（#33/#34）。
- `runs/gemma4-1` 是 gemma4 的生成产物，`linzhou` fixture 已把 inferred 字段与 source 字段分开标注。
