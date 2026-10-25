# P2-22 Distilly intake → analyze → build 时序

对应看板 #22。基线：`vendor/distilly` 固定 commit
`0afc45c29c360ba7bc8260f1bb0bf34e9155f4fa`（`dot-skill`）。

本文只整理上游流程，不改 Distilly 源码。结论先说：**它不是一条全自动流水线，
而是“收集 → 双轨分析 → 预览确认 → 写入”的人在环流程。**

## 总览

```text
用户请求
  │
  ▼
SKILL.md 路由
  ├─ owner      → prompts/flows/owner.md（不是角色 Skill）
  ├─ create     → prompts/flows/create.md（本文主线）
  ├─ evolve     → prompts/flows/evolve.md（追加材料 / 纠错）
  └─ manage     → prompts/flows/manage.md（列出 / 回滚 / 删除）
  │
  ▼ create
Step 0  选 family：colleague / relationship / celebrity
  │      celebrity 还要选 research_profile
  ▼
Step 1  Intake：问最少的问题，产出基础信息摘要
  │      ⛳ 人工确认：姓名、关系、状态、材料
  ▼
Step 2  Sources：Lark / 钉钉 / 链接 / 文件 / 粘贴 / 跳过
  │      原始材料只进本地 knowledge/，不把凭据放进对话
  ▼
Step 3  Analyze：两条轨道
  ├─ Track A  work_analyzer   → 工作方法、技术标准、流程、经验
  └─ Track B  persona_analyzer → 表达 DNA、情绪触发、冲突、记忆签名
  │      ⛳ 证据与推断分开；材料不足必须标记，不得编造
  ▼
Step 4  Build：work_builder + persona_builder
  │      生成 work.md / persona.md 的内容草稿
  │      ⛳ 人工确认：5–8 行 Work / Persona 摘要，可要求修改
  ▼
Step 5  Write：只通过 tools/skill_writer.py create
  │      生成 SKILL.md、work.md、persona.md、work_skill.md、
  │      persona_skill.md、manifest.json、meta.json
  │      ⛳ 可选：安装到宿主（Claude Code / OpenClaw / Codex 等）
  ▼
Step 6  Celebrity only：quality_check.py 质量检查
  │
  ▼
Step 7  source_grounding 失败时：补真实可追溯来源，或保留 FAIL 并说明缺口
```

## 1. 入口路由

`SKILL.md` 只负责把请求送到对应 flow：

| 用户意图 | 去向 |
|---|---|
| “设置我的 profile”“了解我”“distill myself” | `prompts/flows/owner.md` |
| `/distilly`、创建新 Skill、蒸馏某人 | `prompts/flows/create.md` |
| 追加材料、纠正“他不会这样” | `prompts/flows/evolve.md` |
| 查看、回滚、删除已生成 Skill | `prompts/flows/manage.md` |

`SKILL.md` 规定：

- 生成物写到用户工作区的 `./skills/colleague/{slug}`、
  `./skills/relationship/{slug}` 或 `./skills/celebrity/{slug}`。
- 只能通过 `tools/skill_writer.py` 创建/更新，禁止手写最终文件。
- App Secret、OAuth code、refresh token 等永不进入对话；用户在自己的终端完成
  `--setup` / `--login`。

## 2. Step 0：确认 family

`create.md` 先锁定一种角色家族：

| family | intake | persona analyzer | persona builder | merger | 输出根目录 |
|---|---|---|---|---|---|
| `colleague` | `prompts/colleague/intake.md` | `prompts/colleague/persona_analyzer.md` | `prompts/colleague/persona_builder.md` | `prompts/colleague/merger.md` | `./skills/colleague/{slug}` |
| `relationship` | `prompts/relationship/intake.md` | `prompts/relationship/persona_analyzer.md` | `prompts/relationship/persona_builder.md` | `prompts/relationship/merger.md` | `./skills/relationship/{slug}` |
| `celebrity` | `prompts/celebrity/intake.md` | `prompts/celebrity/persona_analyzer.md` | `prompts/celebrity/persona_builder.md` | `prompts/celebrity/merger.md` | `./skills/celebrity/{slug}` |

公共部分：

- Work analyzer：`prompts/shared/work_analyzer.md`
- Work builder：`prompts/shared/work_builder.md`
- Correction handler：`prompts/shared/correction_handler.md`

特殊分支：

- `owner` 不是角色 Skill，直接转 `owner.md`；最终由
  `tools/owner_profile.py` 写成宿主上下文，不走 `skill_writer.py`。
- `celebrity` 需要再选 `budget-friendly`（默认）或
  `budget-unfriendly`；后者会多出 audit / synthesis / validation 和更严格的
  质量阈值。

## 3. Step 1：Intake

Intake 的目标不是一次问完所有信息，而是拿到“足够开始”的最小上下文。除别名外
都可以跳过。

### relationship

三轮对话：

1. **基础信息**：称呼、关系、当前状态、性别 / MBTI / 星座 / 年龄区间、
   多久没见。
2. **一个最接近的记忆**：发生了什么、对方说了/做了什么、为什么记得。
3. **可提供的材料**：聊天记录、截图、照片、语音转写、日记/记忆笔记，
   或“没有文件，只凭记忆”。

产出字段：

- `display_name`
- `alias`
- `slug` 候选
- `relationship_subtype`
- `current_status`
- `profile` 线索
- 距离/时间信息
- 记忆锚点：场景、语气、节奏、情绪触发、冲突/照顾模式、口头禅
- 材料类型与存放目录

最后展示 Summary，等用户 `confirm` 或 `edit [field]` 后才继续。

### colleague

三个问题：

1. 别名 / 代号（生成 slug，中文转拼音，统一用 `-`）。
2. 公司、职级、岗位、性别，一句话回答。
3. 性格：MBTI、星座、性格标签、公司文化标签、主观印象。

确认摘要后才进入材料导入。

### celebrity

在 intake 阶段同时确定收集策略和 `research_profile`。默认
`budget-friendly`；只有用户明确要求更深、更慢、更高置信度时才用
`budget-unfriendly`。

## 4. Step 2：Sources

`sources.md` 提供五种入口，可混用，也可完全跳过：

| 选项 | 输入 | 说明 |
|---|---|---|
| A | Lark 自动收集 | 群聊/私聊、文档、表格；私聊需要 user token |
| B | 钉钉自动收集 | 文档、表格、浏览器抓消息 |
| C | Lark 链接 | 浏览器会话或 MCP |
| D | 上传文件 | PDF、图片、Lark JSON、`.eml` / `.mbox` |
| E | 粘贴文本 | 直接作为材料 |

关键约束：

- 凭据只由用户在自己的终端输入，模型不接收、不打印、不写入命令参数。
- 原始材料进入本地的 `knowledge/{slug}/` 或生成 Skill 的 `knowledge/`，
  不提交到 Pre_Alpha。
- 如果用户说“没有文件 / 跳过”，直接用 Step 1 的问答生成；这允许“只凭记忆”
  的轻量路径。
- 聊天记录、截图、语音转写都可能含 PII；后续生成物仍可能带原话，仓库要忽略
  `skills/` 和 `knowledge/`。

## 5. Step 3：Analyze（双轨）

### Track A：Work Skill

使用 `prompts/shared/work_analyzer.md`，提取：

- 负责的系统 / 模块 / 业务线 / 文档
- 责任边界
- 接任务、写方案、处理异常的工作流
- 文档/回复的输出偏好
- 技术标准、代码风格、命名、API 设计、CR 关注点（按岗位细分）
- 有原话支撑的经验结论

规则：只写有证据的内容；没有材料的维度写
`(Insufficient source material; consider adding relevant documents)`，
不能用“可能 / 倾向于”填空。

### Track B：Persona

使用 family 对应的 `persona_analyzer.md`。关系家族重点提取四类：

1. **Expression DNA**：口头禅、常用措辞、句子节奏、温柔/距离感、直接/间接、
   安全时怎么说、抽离时怎么说。
2. **Emotional Triggers**：什么让 ta 打开、关闭、防御、亲近、消失。
3. **Conflict Pattern**：冲突风格、防御机制、修复方式、沉默模式、边界反应。
4. **Memory Signature**：用户仍记得的场景、重复事件、象征物/地点/习惯、
   情绪余像。

输出规则：

- 用用户的语言；
- 证据与推断分开；
- 材料薄的区域标记 `（source material insufficient）`；
- 优先提取模式，不写传记摘要。

### celebrity 额外步骤

先走 `prompts/flows/celebrity_research.md`，再分析。`budget-unfriendly` 使用更深的
analyzer/builder；这一步不属于 P2 的 relationship 主路径。

## 6. Step 4：Build + Preview

- Work 内容：`prompts/shared/work_builder.md`
- Persona 内容：family 对应的 `persona_builder.md`

Builder 生成的是 `work.md` / `persona.md` 的**内容草稿**，还没写文件。

然后必须向用户展示 5–8 行摘要：

```text
Work Skill Summary:
  - Responsible for: ...
  - Tech stack: ...
  - CR focus: ...

Persona Summary:
  - Core personality: ...
  - Communication style: ...
  - Decision pattern: ...

Confirm generation? Or need adjustments?
```

这是第二个硬人工门。用户确认前不能进入 Step 5。

relationship 的 persona builder 结构正好对应 #28 要归档的“六层”：

| 层 | 内容 |
|---|---|
| Layer 0 | Core Relational Rules：亲近/不安全/冲突时怎么做，以及永远不直接说的话 |
| Layer 1 | Relationship Context：关系类型、当前状态、连接定义 |
| Layer 2 | Expression DNA：口头禅、节奏、四个场景的示例回复 |
| Layer 3 | Emotional Logic：何时打开、抽离、防御、表达关心 |
| Layer 4 | Conflict and Repair：冲突、沉默、修复、边界 |
| Layer 5 | Memory Signature：场景、象征物、情绪余像 |
| Correction Log | 运行期纠错记录，初始为空 |

## 7. Step 5：Write

确认后，先写三个临时文件：

- `/tmp/distilly_{slug}_meta.json`
- `/tmp/distilly_{slug}_work.md`
- `/tmp/distilly_{slug}_persona.md`

`meta.json` 至少包含：

- `name`
- `display_name`
- `character`
- `research_profile`（celebrity 必填）
- `classification.language`（必须匹配用户语言，如 `zh-CN` / `en`）
- `profile`
- `tags`
- `knowledge_sources`

然后调用：

```bash
python3 "{distilly_skill_root}/tools/skill_writer.py" \
  --action create \
  --character {character} \
  --research-profile {research_profile} \
  --slug {slug} \
  --name "{name}" \
  --meta /tmp/distilly_{slug}_meta.json \
  --work /tmp/distilly_{slug}_work.md \
  --persona /tmp/distilly_{slug}_persona.md \
  --base-dir {resolved_base_dir}
```

生成：

```text
{resolved_base_dir}/{slug}/
├── SKILL.md
├── work.md
├── persona.md
├── work_skill.md
├── persona_skill.md
├── manifest.json
├── meta.json
├── knowledge/          # 按预设创建的资料目录
└── versions/           # 后续更新时的版本备份
```

可选宿主安装：

- Claude Code：`--install-claude-skill`
- OpenClaw：`--install-openclaw-skill`
- Codex：`--install-codex-skill`
- Hermes / DeepSeek Harness / Pi / Grok Build / OpenCode：用
  `tools/install_generated_skill.py --host ...`

注意：安装器只复制自包含的 `SKILL.md` 和安装元数据，不复制整个生成目录，
避免把私有材料带进宿主。

## 8. Step 6/7：Celebrity 质量检查与来源失败

celebrity 写完后运行：

```bash
python3 "{distilly_skill_root}/tools/research/quality_check.py" \
  "{resolved_base_dir}/{slug}/SKILL.md" --profile {research_profile}
```

如果 `source_grounding` 仍失败：

- 可以补真实、具体、可追溯的 URL；
- 不能用站点根页、话题页、搜索结果页伪造 grounding；
- 没有可信来源就保留 FAIL，并说明还缺什么材料。

relationship / colleague 不走这个质量检查。

## 9. 三个硬人工门

| 门 | 位置 | 不通过会怎样 |
|---|---|---|
| G1 | Step 1 intake summary | 不进入材料导入 |
| G2 | Step 4 Work/Persona preview | 不写文件，先改草稿 |
| G3 | Step 5 host install（可选） | 只生成到 `skills/`，不安装 |

## 10. 与 P2 后续任务的接口

- **#23 安装/触发**：本流程只定义“生成后可选安装”；#23 要验证宿主实际发现和
  触发 `SKILL.md` 的方式。OpenCode 原生加载 Skill；其他宿主有各自目录/命令。
- **#24 解析器**：Step 2 的 D 入口依赖 `feishu_parser.py`、`email_parser.py`；
  wechat/qq/social/photo 的格式与本流程的 `knowledge/` 落点需要对齐。
- **#25 样本**：用脱敏材料走 Step 2 E 或 D，避免依赖 Lark/钉钉凭据。
- **#26/#27 跑通**：colleague 先验证通用 Work + Persona；relationship 是陪伴主路径，
  重点看 Layer 0–5 是否具体、是否泄露内心独白式判断。
- **#28 结构归档**：直接采用 relationship builder 的 Layer 0–5 + Correction Log。
- **#30 schema 映射**：把 `meta.json` + 六层 persona 映射到我方内部 schema；
  不能把原始聊天记录塞进 `raw_text`。
- **#29 质量基线**：同一份脱敏材料跑 3 次，比较稳定性、证据/推断边界、幻觉点。

## 11. 代码阅读发现

1. `create.md` 的 family matrix 列了 `merger`，但 create 流程本身不调用 merger；
   merger 在 `evolve.md` 的追加材料流程中使用。P2 首跑不要误调用。
2. Work 轨道对 relationship 也会执行；关系材料的“工作方法”通常很薄，必须使用
   insufficient 标记，不能为了填满模板编造工作经历。
3. `skill_writer.py` 是唯一写入口；手改 `work.md` / `persona.md` / `SKILL.md`
   会破坏 manifest、版本和 correction 机制。
4. `requirements.txt` 的核心依赖只有 `requests`；`skill_writer.py --help` 在
   未安装可选依赖时可正常运行。Lark/钉钉/Slack/Playwright 属于可选依赖。
5. `skills/` 和 `knowledge/` 都被 Distilly 自己忽略；Pre_Alpha 也不应提交生成物。
6. 关系家族的“六层”不是六个独立文件，而是 `persona.md` 内部的 Layer 0–5；
   `persona_skill.md` 是渲染后的可安装版本。

## 12. #22 验收对照

| 要求 | 结果 |
|---|---|
| 读 `SKILL.md` | 已完成：入口路由、存储、写入、凭据规则 |
| 读 `prompts/flows/create.md` | 已完成：Step 0–7 |
| 整理 intake → analyze → build 时序 | 已完成：本文总览与分步表 |
| 标出人工确认点 | G1 intake、G2 preview、G3 install |
| 标出产出物 | 7 个文件 + `knowledge/` + `versions/` |
| 标出后续接口 | #23–#30 |

## 参考源码

- `vendor/distilly/SKILL.md`：20–37 行
- `vendor/distilly/prompts/flows/create.md`：20–159 行
- `vendor/distilly/prompts/flows/sources.md`：26–264 行
- `vendor/distilly/prompts/flows/owner.md`：1–19 行
- `vendor/distilly/prompts/flows/evolve.md`：5–66 行
- `vendor/distilly/prompts/relationship/intake.md`：26–108 行
- `vendor/distilly/prompts/relationship/persona_analyzer.md`：21–108 行
- `vendor/distilly/prompts/relationship/persona_builder.md`：19–122 行
- `vendor/distilly/prompts/shared/work_analyzer.md`：12–181 行
- `vendor/distilly/prompts/shared/work_builder.md`：13–101 行
- `vendor/distilly/tools/skill_writer.py`：554–720 行
- `vendor/distilly/tools/skill_schema.py`：17–35 行
