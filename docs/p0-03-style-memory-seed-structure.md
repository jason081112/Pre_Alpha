# P0-3 · OpenHer 风格种子数据结构与写入 API

> 阶段：P0 调研验证 · 任务：读 `style_memory.py` + `persona/store.py` + `seeds.bin`：风格种子数据结构与写入 API

## 一、ContinuousStyleMemory（`engine/genome/style_memory.py`）

### 核心设计

单一记忆池，无公开/私有区分。记忆带**质量（mass）**，随时间按 **Hawking radiation** 指数衰减，检索时按 **质量加权欧氏距离** 做 KNN 召回。

### 数据结构（单条 memory）

```json
{
  "vector": [0.0, 0.5, 0.2, 0.0, 0.0, 0.0, 0.0, 0.5],   // 8D 上下文向量（CONTEXT_KEYS 顺序）
  "monologue": "……",                                   // 内心独白文本
  "reply": "……",                                       // 最终回复文本
  "user_input": "……",                                  // 用户输入（调试/展示用）
  "mass": 1.0,                                          // 原始质量（genesis=1.0, 新记忆=2.0, 结晶+1）
  "lang": "zh",                                         // 语言标记，用于跨语言过滤
  "created_at": 0.0,                                    // 创建时间戳
  "last_used_at": 0.0                                   // 最后使用时间戳（Hawking 衰减依据）
}
```

**CONTEXT_KEYS**（8 维，与 `genome_engine.py` 的 Critic 输出对齐）：

| # | 特征 | 取值 |
|---|---|---|
| 0 | conflict_level | 0=和谐 → 1=冲突 |
| 1 | user_emotion | -1=负面 → 1=正面 |
| 2 | user_engagement | 0=敷衍 → 1=投入 |
| 3 | user_vulnerability | 0=防御 → 1=敞开 |
| 4 | topic_intimacy | 0=公事 → 1=私密 |
| 5 | conversation_depth | 0=刚开始 → 1=聊很久 |
| 6 | novelty_level | 0=日常 → 1=全新 |
| 7 | time_of_day | 0=早晨 → 1=深夜 |

### 持久化

SQLite（`openher.db`），两张表：

| 表 | 用途 | 主键 |
|---|---|---|
| `genesis_seed` | 天生种子（从 `seeds.bin` 自动导入）| persona_id |
| `style_memory` | 用户个人学习记忆 | (persona_id, user_id) |

### 关键 API

| 方法 | 说明 |
|---|---|
| `ContinuousStyleMemory(agent_id, db_dir, persona_id, ...)` | 初始化，自动从 DB/ seeds.bin 加载 |
| `retrieve(context, top_k=3, lang_preference=None)` | KNN 检索，返回 top_k 条 memory（带 mass_eff、distance） |
| `crystallize(context, monologue, reply, user_input)` | 结晶：距离 < 0.25 则质量 +1 并刷新时间；否则新建记忆（mass=2.0）并持久化 |
| `build_few_shot_prompt(context, top_k=3, lang='zh')` | 把检索结果格式化为 few-shot prompt 片段（带质量标签） |
| `save_genesis_to_db(persona_id, seeds, db_path)` | 静态方法，批量写入 genesis seeds（清洗 action markers 后 upsert） |
| `stats()` | 返回 genesis_count / personal_count / total_mass 等统计 |

### Hawking radiation（质量衰减）

```python
mass_eff = 1.0 + (mass_raw - 1.0) * e^(-γ * Δt_hours)
```
γ = 0.001/小时，半衰期约 29 天。质量基准 1.0 永不低于（天生基因不会蒸发殆尽）。

### 检索距离公式

```
effective_distance = physical_distance / √mass_eff
```
质量越高，有效距离越小，越容易被召回。同语言 seeds 有 25% 距离加成，跨语言被惩罚。

---

## 二、seeds.bin（天生种子库）

`persona/seeds.bin` —— gzip 压缩的 JSON，9 个 persona，每个 ~70 条 seeds。

| persona | seeds 数量 | 语言 |
|---|---|---|
| ember | 72 | en |
| iris | 72 | zh |
| kai | 72 | zh |
| kelly | 72 | zh |
| luna | 71 | zh |
| mia | 71 | zh |
| rex | 71 | zh |
| sora | 69 | zh |
| vivian | 72 | zh |

每条 seed 字段：`vector`(8D), `monologue`, `reply`, `user_input`, `mass`(1.0), `lang`。

示例（kelly）：
```json
{
  "vector": [0.0, 0.5, 0.2, 0.0, 0.0, 0.0, 0.0, 0.5],
  "monologue": "哟，来了。看看今天能聊出什么花来。",
  "reply": "哟，什么风把你吹来了？",
  "user_input": "",
  "mass": 1.0,
  "lang": "zh"
}
```

### 自动导入机制

`ContinuousStyleMemory._auto_import_seeds()`：首次启动时，如果 DB 的 `genesis_seed` 表为空，自动从 `seeds.bin` 导入所有 persona 的 seeds 到 DB。无需手动步骤。

---

## 三、persona/store.py（PersonaStore）

动态生成角色的存储层，与 SOUL.md 静态路径并行存在。

### 数据结构

**PersonaProfile**（dataclass）：
```python
id, gender, name, age, personality, speaking_style, tags[], backstory,
voice: VoiceProfile(description, ref_text),
avatar: AvatarProfile(prompt, path, style),
locked, created_at, locked_at
```

**PersonaStore**（目录 `personas/{persona_id}/`）：
```
personas/
└── {persona_id}/
    ├── profile.json   # 人设数据
    ├── voice_ref.wav  # 参考音频（可选）
    └── avatar.png     # 头像（可选）
```

### 关键 API

| 方法 | 说明 |
|---|---|
| `save(profile)` | 持久化到 profile.json，更新缓存 |
| `load(persona_id)` | 从磁盘加载（带内存缓存） |
| `lock(persona_id)` | 锁定角色（解锁其他，标记 locked=True + locked_at） |
| `get_locked()` | 获取当前唯一已锁定角色 |
| `list_ids()` / `list_all()` | 列出所有角色 |
| `delete(persona_id)` | 删除角色目录 |

**注意**：PersonaStore 是**动态生成路径**（LLM 生成人设 + 锁定后持久化），与 `style_memory.py` 的 genesis/personal 记忆池是**两套独立系统**。`build_system_prompt()` 生成的文本直接注入 System Prompt（这是 P0-1 发现的 SOUL.md body 未进入 prompt 的对应项——动态生成的角色会通过这个方法进入 prompt）。

---

## 四、对 P3 `profile2soul.py` 的映射建议

| Distilly 产出（文本人格） | OpenHer 落点 | 映射方式 |
|---|---|---|
| 表达 DNA（句长、标点、语气词） | `style_memory` 种子语料（monologue/reply） | 批量注入为 genesis seeds，mass=1.0 |
| 口头禅库 | `style_memory` 高权重种子 | 设 mass=2.0+，提高 KNN 召回优先级 |
| 说话风格 | `PersonaProfile.speaking_style`（动态路径）或 SOUL.md body（静态路径） | 取决于走哪套系统 |
| 身份字段 name/age/gender | SOUL.md frontmatter / PersonaProfile.id/name/age | 直接填 |
| 向量语境（冲突/亲密/情绪等） | `style_memory` seed 的 `vector` 字段 | 按 CONTEXT_KEYS 顺序填 8 维值 |

### 关键决策点

1. **走 SOUL.md 静态路径 还是 PersonaStore 动态路径？**
   - SOUL.md：现有 9 个内置角色已有 SOUL.md，loader.py 可直接加载
   - PersonaStore：支持 LLM 动态生成 + 锁定，更灵活但需要额外 UI/流程
   - P3 建议优先走 SOUL.md 路径（与现有代码集成成本低），PersonaStore 作为 P4+ 扩展

2. **style_memory 种子注入 API**
   - 批量注入：`ContinuousStyleMemory.save_genesis_to_db(persona_id, seeds, db_path)`
   - 单条写入：`crystallize()`（会自动持久化 personal 记忆到 style_memory 表）
   - genesis seeds 重启后从 DB 重新加载，personal 记忆也在同一 DB

---

## 五、P1 跑通可行性（补充）

`requirements.txt` 无 torch/transformers/CUDA，纯 Python 库。`style_memory.py` 仅依赖 sqlite3（标准库），无额外依赖。Termux 上可直接跑。

---

**P0-3 状态**：调研完成。下一步可进入 P0-4（读 `drive_metabolism.py`）或直接开始 P3 桥接层设计。
