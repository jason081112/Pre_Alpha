# 架构说明

## 为什么需要桥接层

两个上游项目的设计哲学**互相冲突**，这是本项目存在的理由：

| | Distilly（前端） | OpenHer（后端） |
|---|---|---|
| 人格载体 | 文本：六层 Persona（硬规则 → 身份 → 表达 → 决策 → 人际 → 纠错） | 数值：5 维驱力基线 + 13 个引擎参数 + 神经网络权重 |
| 人格如何生效 | 写进 SKILL.md，由宿主 Agent 读取 | "没有任何一行 prompt 描述她的性格"，人格从动力学系统中涌现 |
| 记忆 | 蒸馏产物是静态 Markdown | 风格记忆（KNN 引力晶化）+ FTS5 + 可选 EverOS |

直接拼接的结果是：后端根本**读不到**前端产出的人格。必须有一个把"文本人格"翻译成"数值驱力 + 种子语料"的映射层。

## 映射设计（待 P0 调研校准）

| Distilly 产出 | OpenHer 落点 | 备注 |
|---|---|---|
| name / age / gender / mbti | `SOUL.md` frontmatter | 直接对应 |
| 表达 DNA（句长、标点、语气词） | `style_memory` 种子语料 | KNN 召回，引力质量加权 |
| 口头禅库 | `style_memory` 高权重种子 | 高频短句，易被召回 |
| 情绪触发点 / 冲突模式 | `drive_baseline`（connection / novelty / expression / safety / play） | 需人工标定启发式 |
| 冲突强度、爆发阈值 | `engine_params.phase_threshold` / `temp_coeff` | 与"情感相变"直接相关 |
| 纠错层（Correction） | 运行期可写约束 / critic 输入 | 即时生效，不等重新蒸馏 |
| 事实、共同记忆 | `memory_store` FTS5（可选 EverOS episodes） | 带 source 与时间戳 |

**关键未知（P0 逐项确认）**
1. `SOUL.md` 与 `SHELL.md` 各自是否被注入 prompt？决定"表达风格"写哪里。
2. `persona/store.py` + `seeds.bin` 是否支持批量注入种子？
3. EverOS 缺席时引擎的降级路径是否完整（决定默认离线方案）。

## 目录规划

```
Pre_Alpha/
├── vendor/OpenHer       # 后端引擎（fork，保留上游 LICENSE）
├── vendor/distilly      # 前端蒸馏（fork @ dot-skill 分支）
├── bridge/              # ★ 本项目自研：profile2soul 及映射规则
├── cli/                 # alpha 命令入口
├── docs/                # 本文件、P0 调研笔记
├── tests/fixtures/      # 脱敏聊天样本、golden 快照
└── THIRD_PARTY_NOTICES.md
```

## 阶段路线

65 项任务拆解见 [Project Alpha](https://github.com/users/jason081112/projects/1)：

- **P0 调研验证**（10）—— 确认上面三个未知项
- **P1 后端跑通**（10）—— OpenHer 在本机起 `:8000`
- **P2 前端跑通**（10）—— Distilly 产出第一份 Person Profile
- **P3 桥接层**（14）—— 核心：`profile2soul` + 20 轮对话语气验收
- **P4 交互层**（8）—— Web / CLI / 微信桥
- **P5 隐私合规**（7）—— 脱敏、一键清除、审计、伦理
- **P6 工程基建**（6）—— License、CI、打包
