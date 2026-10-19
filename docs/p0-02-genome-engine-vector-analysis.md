# P0-2 · OpenHer `engine/genome/genome_engine.py` 向量构成与外部种子影响点

> 阶段：P0 调研验证 · 任务：读 `engine/genome/genome_engine.py`：25D→24D→8D 输入向量构成，哪些维度可被外部种子影响

> ⚠️ **环境假设已修订（2026-10-19）**：文中"可在 Termux 直接跑"属当时的开发环境假设；部署形态已改为**引擎云端运行、Web/PWA 优先**（见 [`adr/0001-deployment-cloud-web-first.md`](adr/0001-deployment-cloud-web-first.md)）。**代码层结论不受影响。**

## 一、向量构成：25D → 24D → 8D

`engine/genome/genome_engine.py` 第 84–85 行：

```python
RECURRENT_SIZE = 8
INPUT_SIZE = N_DRIVES + N_CONTEXT + RECURRENT_SIZE   # 5 + 12 + 8 = 25
HIDDEN_SIZE = 24                                       # 24
N_SIGNALS = len(SIGNALS)                               # 8
```

### 25D 输入向量（`full_input`）

| 分段 | 维度 | 来源 | 说明 |
|---|---|---|---|
| Drive state | 5 | `self.drive_state[d]` | 5 维驱力当前值：connection / novelty / expression / safety / play |
| Context | 12 | `context.get(f, 0.0)` | 来自 Critic 判断 + EverMemOS 关系维度 |
| Recurrent state | 8 | `self.recurrent_state` | 上一轮隐藏层前 8 值（内部“情绪”状态） |

**Context 的 12 个子特征**（`CONTEXT_FEATURES`）：

| # | 特征 | 取值范围 | 来源 |
|---|---|---|---|
| 0 | user_emotion | -1=负面 → 1=正面 | Critic 判断 |
| 1 | topic_intimacy | 0=公事 → 1=私密 | Critic 判断 |
| 2 | time_of_day | 0=早晨 → 1=深夜 | Critic 判断 |
| 3 | conversation_depth | 0=刚开始 → 1=聊很久 | Critic 判断 |
| 4 | user_engagement | 0=敷衍 → 1=投入 | Critic 判断 |
| 5 | conflict_level | 0=和谐 → 1=冲突 | Critic 判断 |
| 6 | novelty_level | 0=日常话题 → 1=全新话题 | Critic 判断 |
| 7 | user_vulnerability | 0=防御 → 1=敞开心扉 | Critic 判断 |
| 8 | relationship_depth | 0=陌生人 → 1=老朋友 | EverMemOS 关系维度（新用户 0，老用户渐进） |
| 9 | emotional_valence | -1=负面基调 → 1=正面基调 | EverMemOS 关系维度 |
| 10 | trust_level | 0=无信任 → 1=高度信任 | EverMemOS 关系维度 |
| 11 | pending_foresight | 0=无 → 1=有待处理的前瞻 | EverMemOS 关系维度 |

### 24D 隐藏层

```python
hidden = []
for i in range(HIDDEN_SIZE):  # 24
    z = self.b1[i]
    for j, x in enumerate(full_input):  # 25
        z += self.W1[i][j] * x
    hidden.append(math.tanh(z))
```

- W1: 24×25，b1: 24，激活函数 tanh
- 隐藏层前 8 个值更新到 `self.recurrent_state`（下一轮作为 8D 输入的一部分）

### 8D 输出行为信号（SIGNALS）

| # | 信号 | 含义 |
|---|---|---|
| 0 | directness | 0=委婉暗示 → 1=直说 |
| 1 | vulnerability | 0=防御心理 → 1=袒露脆弱 |
| 2 | playfulness | 0=认真严肃 → 1=玩闹撒娇 |
| 3 | initiative | 0=被动回应 → 1=主动引导 |
| 4 | depth | 0=表面闲聊 → 1=深度对话 |
| 5 | warmth | 0=冷淡疏离 → 1=热情关怀 |
| 6 | defiance | 0=顺从 → 1=反抗/嘴硬 |
| 7 | curiosity | 0=无所谓 → 1=追问到底 |

输出层：W2 (8×24) + b2 (8)，缩放后 sigmoid → [0,1]。

## 二、哪些维度可被外部种子影响

外部种子 = Distilly 蒸馏产出的文本人格，通过 `profile2soul.py` 映射后写入 OpenHer。可注入的入口如下：

### 1. drive_baseline（5 维）—— 最直接的入口

`ChatAgent.__init__`（`agent/chat_agent.py` 第 106–111 行）：

```python
if persona.drive_baseline:
    for d, v in persona.drive_baseline.items():
        if d in self.agent.drive_baseline:
            self.agent.drive_baseline[d] = float(v)
            self.agent.drive_state[d] = float(v)
```

- SOUL.md frontmatter 的 `genome_seed.drive_baseline` → 直接覆盖 Agent 的 5 维驱力基线和当前状态
- 这是 `profile2soul.py` **最优先**映射的目标：Distilly 的情绪/社交/冲突信号 → connection/novelty/expression/safety/play

### 2. engine_params（13 个基因参数）

SOUL.md frontmatter `genome_seed.engine_params` → `Agent.__init__`：

| 参数 | 默认值 | 作用 |
|---|---|---|
| baseline_lr | 0.01 | 驱力基线演进学习率 |
| elasticity | 0.05 | 基线向原点回弹力度 |
| phase_threshold | 2.0 | 挫败超出该值触发相位转移（重置 b1/b2 偏置） |
| hebbian_lr | 0.02 | Hebbian 学习率 |
| connection_hunger_k | - | connection 驱力饥饿系数 |
| novelty_hunger_k | - | novelty 驱力饥饿系数 |
| frustration_decay | - | 挫败衰减系数 |
| hawking_gamma | - | 记忆衰减系数 |
| crystal_threshold | 0.50 | 结晶门控阈值 |
| temp_coeff | - | 温度系数 |
| temp_floor | - | 温度下限 |

这 13 个参数全部来自 `persona.engine_params`，由 SOUL.md frontmatter 提供。**`profile2soul.py` 需要把 Distilly 的冲突强度、爆发阈值、情感相变等特征映射到这里。**

### 3. signal_overrides（信号桶覆盖）

SOUL.md `genome_seed.signal_buckets` → `Agent.to_prompt_injection_from_signals()` 的 `signal_overrides` 参数：

- 用于覆盖 8 个行为信号的 `emoji_label` / `low_anchor` / `high_anchor`
- 影响 prompt 注入时的信号文本描述（不影响数值计算）

### 4. seed（随机种子）

`Agent.__init__(seed=...)` 决定 W1/W2/b1/b2/recurrent_state 的初始随机值。

- 不同 persona 用不同 seed → 初始性格差异
- `genome_seed` 中没有直接的 seed 字段，seed 由 `main.py` 从 `hash(persona_id)` 生成（第 640 行）

### 5. pre_warm（预热场景）

`simulate_conversation()` 通过 3 个场景（深夜心事/日常闲聊/吵架冲突）× 20 步 = 60 步模拟对话，塑造 W1/W2 权重。

- 这是**性格多样性的主要来源**——没有 pre_warm，所有 agent 从同一中性状态启动
- `profile2soul.py` 产出的 SOUL.md 之后，`main.py` 会自动执行 `pre_warm()`（新 agent 时）

## 三、prompt 注入形式（非数值，是文本）

`Agent.to_prompt_injection_from_signals()`（第 439–506 行）把数值转成注入 LLM system prompt 的文本：

```
【舞台指令：角色当前状态】
🎯 直接度: 0.75 (0低→1高)
💧 坦露度: 0.30 (0封闭→1袒露)
...

【舞台指令：角色内在需求】
🔗 联结: 0.40 (基线: 0.40, 渴望: 0.1)
✨ 新鲜: 0.80 (基线: 0.80, 渴望: 0.0)
...
```

**关键**：prompt 里只有**数值 + 标签**，没有完整的人格描述文本。SOUL.md 的 `personality / speaking_style / background / behavioral_rules`（完整文本人格）**当前代码中不进入 prompt**（见 P0-1 结论）。这是桥接层需要填补的空白。

## 四、对 P3 `profile2soul.py` 的映射建议

| Distilly 产出（文本人格） | OpenHer 落点 | 映射方式 |
|---|---|---|
| name/age/gender/mbti | SOUL.md frontmatter | 直接填 |
| 表达 DNA（句长、标点、语气词） | `style_memory` 种子 + `signal_overrides` | KNN 种子批量注入 |
| 口头禅库 | `style_memory` 高权重种子 | 高频短句，易被 KNN 召回 |
| 情绪触发点/冲突模式 | `drive_baseline` 5 维 | 启发式反推（connection/novelty/expression/safety/play） |
| 冲突强度/爆发阈值 | `engine_params.phase_threshold` / `temp_coeff` | 与“情感相变”直接相关 |
| 纠错层（Correction） | `engine_params.crystal_threshold` + 运行期约束 | 即时生效，不等重新蒸馏 |
| 事实/共同记忆 | `MemoryStore` FTS5（+ EverMemOS 可选）| 带 source 与时间戳 |

## 五、P1 跑通可行性（补充）

`requirements.txt` 无 torch/transformers/CUDA，纯 Python 库。Termux 上可直接 `pip install -r requirements.txt`，主变量是：
- LLM provider 网络连通性（openai/anthropic/dashscope/google-genai）
- `edge-tts` 的 ffmpeg 依赖（如需语音）

---

**P0-2 状态**：调研完成。下一步可进入 P0-3（读 `style_memory.py` 风格种子数据结构）或直接开始 P3 桥接层设计。
