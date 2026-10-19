# P0-1 · OpenHer `agent/chat_agent.py` 调用链与 SOUL/SHELL 注入点分析

> 阶段：P0 调研验证 · 任务：通读 `agent/chat_agent.py`，画出一轮对话的完整调用链，确定 SOUL.md / SHELL.md 与 prompt 的关系（决定 P3 桥接层 `profile2soul.py` 怎么写）

> ⚠️ **环境假设已修订（2026-10-19）**：文中"可在 Termux 直接跑"属当时的开发环境假设；部署形态已改为**引擎云端运行、Web/PWA 优先**（见 [`adr/0001-deployment-cloud-web-first.md`](adr/0001-deployment-cloud-web-first.md)）。**代码层结论不受影响。**

## 一、一轮对话完整调用链

入口：`main.py` → `WebSocket /ws/chat` 或 `REST /api/chat` → `get_or_create_session()`（首次创建 `ChatAgent`，含 `pre_warm()`） → `ChatAgent.chat()` → `ChatAgent._chat_inner()`：

```
用户消息
   │
   ▼ Step -1  任务技能 ReAct 环（_run_task_skills）
   │    若有 task_skill_engine，先把真实数据注入 user_message
   │
   ▼ Step 0   EverMemOS 会话上下文（仅首轮；异步加载 profile）
   │
   ▼ Step 1   时间代谢（DriveMetabolism.time_metabolism）
   │    计算 delta_h，更新各驱力的 time-based 饥饿度
   │
   ▼ Step 2   Critic 知觉（critic_sense，LLM 判断）
   │    输出：8D context + frustration delta（5 维）+ relationship delta（3 维）
   │    输入：user_message, llm, frust_dict, user_profile, episode_summary, persona_hint
   │
   ▼ Step 2.5 半涌现关系更新（prior + delta + EMA）→ 4D relationship
   │    合并为 12D context
   │
   ▼ Step 3   LLM 代谢 → reward（DriveMetabolism.apply_llm_delta）
   │    frustration delta → reward，sync_to_agent(agent)
   │
   ▼ Step 3.5 驱力基线演进（弹性基线，向 persona 原点回弹）
   │
   ▼ Step 4   结晶门控（_should_crystallize）
   │    reward > 0.8 或 composite score > crystal_threshold → style_memory.crystallize()
   │
   ▼ Step 5   信号计算（Agent.compute_signals，12D context → 8D 行为信号）
   │
   ▼ Step 6   热力学噪声注入（apply_thermodynamic_noise）
   │
   ▼ Step 7   KNN 检索（ContinuousStyleMemory.build_few_shot_prompt）
   │    从风格记忆库取 top_k=3 条 few-shot 示例
   │
   ▼ Step 8   构建单次传递 prompt（PromptBuilderMixin._build_single_prompt）
   │    identity（name/age/gender）+ signal_injection（信号+趋势+时间）+
   │    few_shot → render_prompt("actor_single", ...)
   │    + modality skill 描述
   │
   ▼ Step 8.5 记忆注入（profile/episode/foresight/profile 画像，80/20 混合）
   │    仅当 _session_ctx.has_history 时执行
   │
   ▼ Step 9   单次 LLM 调用（system = single_prompt + history + user）
   │    输出：monologue + reply + modality
   │
   ▼ Step 9b  模态技能执行（ModalitySkillEngine.plan_and_execute）
   │    LLM 从 【表达方式】中规划技能（图片/语音/表情/照片/静默）
   │
   ▼ Step 10  Hebbian 学习（Agent.step，神经网络 W1/W2 更新）
   │
   ▼ Step 11  EverMemOS store_turn（后台异步，非阻塞）
   │
   ▼ Step 12  发射下一轮的异步检索（_evermemos_search_bg）
   │
   返回 reply + modality + 状态快照
```

### 调用栈关键文件
| 文件 | 职责 |
|---|---|
| `main.py` | FastAPI 网关，WebSocket/REST 入口，session 管理，状态持久化（StateStore） |
| `agent/chat_agent.py` | ChatAgent，12 步 Genome v10 生命周期，核心逻辑 |
| `agent/prompt_builder.py` | PromptBuilderMixin，构建单次传递 prompt（identity + signal_injection + few_shot） |
| `engine/prompt_registry.py` | 加载 `config/prompts/actor_single.md` 模板，string.Template 渲染 |
| `engine/genome/genome_engine.py` | Agent 神经网络（25D→24D→8D），compute_signals, step(Hebbian), to_prompt_injection_from_signals |
| `engine/genome/drive_metabolism.py` | DriveMetabolism，5 维驱力代谢，frustration, temperature, thermodynamic noise |
| `engine/genome/critic.py` | critic_sense，LLM 判断 8D context + frustration/relationship delta |
| `engine/genome/style_memory.py` | ContinuousStyleMemory，KNN 风格记忆，crystallize 结晶，build_few_shot_prompt |
| `persona/loader.py` | PersonaLoader，解析 `SOUL.md` → Persona 对象（frontmatter + body section） |
| `memory/memory_store.py` | MemoryStore，本地关键词记忆（user_message 持久化） |
| `providers/llm/client.py` | LLMClient，统一 LLM 调用（openai/anthropic/dashscope/google-genai） |

## 二、prompt 构建细节（`_build_single_prompt`）

`agent/prompt_builder.py` 第 17–95 行：

```python
identity = f"【角色】\n{persona.name}"          # 仅 name/age/gender
signal_injection = agent.to_prompt_injection_from_signals(...)
combined_injection = identity + "\n\n" + signal_injection
rendered = render_prompt("actor_single",
                         few_shot=few_shot,
                         signal_injection=combined_injection)
# 追加 modality skill 描述
# Step 8.5 追加 memory injection（profile/episode/foresight/profile）
```

`engine/prompts/actor_single.md` 模板结构：
```
[角色参考]    ← $few_shot（风格记忆 KNN 示例）
              ← $signal_injection（身份 + 行为信号 + 趋势 + 时间）

[指令]        固定：你就是这个角色，先写内心独白，再写实际回复，禁止括号动作描述
[输出格式]    【内心独白】 / 【最终回复】 / 【表达方式】
```

**关键观察**：`_build_single_prompt` 中的 `identity` 只包含 `name/age/gender`。**`persona.personality / speaking_style / background / behavioral_rules`（SOUL.md body 内容）在当前代码中未被注入 prompt** —— `Persona.build_system_prompt_section()` 能生成这些段落，但没有调用点。

## 三、SOUL.md / SHELL.md 与 prompt 的关系（P0-1 核心结论）

### SOUL.md 结构（以 `persona/personas/kelly/SOUL.md` 为例）

```
---
# Identity（注入 prompt — 仅事实身份）
name: Kelly
gender: male
age: 26

# Display（仅 UI 展示，不注入 prompt）
mbti: ENTP
tags: {en, zh}
bio: {en, zh}

genome_seed:
  drive_baseline:        # 5 维驱力基线（connection/novelty/expression/safety/play）
    connection: 0.40
    ...
  engine_params:         # 基因引擎参数（per-persona 调优）
    baseline_lr, elasticity, hebbian_lr, phase_threshold,
    connection_hunger_k, novelty_hunger_k, frustration_decay,
    hawking_gamma, crystal_threshold, temp_coeff, temp_floor
  signal_buckets:        # 信号桶覆盖（per-persona 描述覆盖）
---
# body（markdown H2 sections：性格/说话风格/背景故事/行为规则）
```

### SOUL.md 各字段落点（谁进入 prompt，谁不进入）

| SOUL.md 字段 | 进入 prompt？ | 去向 | 备注 |
|---|---|---|---|
| `name` / `age` / `gender` | ✅ 是 | `identity` 段落 → prompt 开头 | 仅事实身份 |
| `mbti` / `tags` / `bio` | ❌ 否 | UI 展示仅 | loader 注明“仅 UI 展示” |
| `genome_seed.drive_baseline` | ❌ 否（以数值形式）| `Agent.drive_baseline` → metabolism → signals → prompt | 以数值信号形式影响表达 |
| `genome_seed.engine_params` | ❌ 否（以参数形式）| `baseline_lr/elasticity/crystal_threshold/temp_coeff…` | 控制代谢与结晶阈值 |
| `genome_seed.signal_buckets` | ❌ 否（以覆盖形式）| `signal_overrides` → `to_prompt_injection_from_signals` | 影响信号文本描述 |
| body `性格/说话风格/背景故事/行为规则` | ❌ 否（当前未接入）| `Persona.build_system_prompt_section()` 生成但**无调用点** | **P3 桥接层的落点之一** |
| `SHELL.md`（voice 配置） | ❌ 否 | TTS voice description 仅 | 外部模态配置，不进对话 prompt |

### 结论

**OpenHer 主张“性格不写在 prompt 里”**：SOUL.md 的完整文本人格（personality/background/behavioral_rules）并没有被原样注入 LLM prompt。prompt 中只有：
1. **事实身份**（name/age/gender）
2. **数值行为信号**（从基因引擎 25D→24D→8D 计算而来，以文本桶描述形式注入）
3. **风格记忆 few-shot**（KNN 从 style_memory 检索）
4. **记忆注入**（profile/episode 片段，Step 8.5）

**这正是 P3 桥接层 `profile2soul.py` 要解决的问题**：Distilly 输出的是**文本人格**（完整的人格描述、说话风格、行为规则、口头禅等），而 OpenHer 只接受**数值驱力 + 种子语料**。中间必须有一个映射层，把文本人格翻译成：
- SOUL.md frontmatter（身份字段）→ 直接填
- 表达风格 + 口头禅 → `style_memory` 种子语料（KNN 可召回）
- 情绪触发/冲突模式 → `drive_baseline` 5 维 + `engine_params.phase_threshold/temp_coeff`
- 纠错层 → `engine_params.crystal_threshold` + 运行期约束
- 事实/共同记忆 → `MemoryStore`（SQLite FTS5）+ 可选 EverMemOS

## 四、P1 跑通可行性（依赖分析）

`requirements.txt` 中**没有 `torch`/`transformers`/`onnxruntime`**，没有 CUDA 依赖，全部是纯 Python 库：
- `fastapi/uvicorn/websockets`（Web 服务）
- `openai/anthropic/dashscope/google-genai`（LLM provider 客户端）
- `edge-tts`（TTS，可能需系统 ffmpeg）
- `chromadb`（向量，需 numpy）
- `aiosqlite/pyyaml/pydantic/python-dotenv`（工具）

**结论**：OpenHer 后端在 Termux 上**应该可直接跑**，主变量是 LLM provider 的网络连通性和 edge-tts 的 ffmpeg 依赖。

## 五、P3 桥接层映射建议（预研）

| Distilly 产出（文本人格） | OpenHer 落点 | 实现方式 |
|---|---|---|
| name/age/gender/mbti | SOUL.md frontmatter → `identity` | 直接填 |
| 表达 DNA（句长、标点、语气词） | `style_memory` 种子 + `signal_overrides` | KNN 种子批量注入 |
| 口头禅库 | `style_memory` 高权重种子 | 高频短句，易被召回 |
| 情绪触发点/冲突模式 | `drive_baseline` 5 维 | 启发式反推（connection/novelty/expression/safety/play） |
| 冲突强度/爆发阈值 | `engine_params.phase_threshold` / `temp_coeff` | 与“情感相变”直接相关 |
| 纠错层（Correction） | `engine_params.crystal_threshold` + 运行期约束 | 即时生效，不等重新蒸馏 |
| 事实/共同记忆 | `MemoryStore` FTS5（+ EverMemOS 可选）| 带 source 与时间戳 |

---

**P0-1 状态**：调研完成，下一步可进入 P0-2（读 `engine/genome/genome_engine.py` 确认 25D→24D→8D 向量构成）或直接开始 P3 桥接层设计。
