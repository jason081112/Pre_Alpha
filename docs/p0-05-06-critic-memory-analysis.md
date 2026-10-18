# P0-5 + P0-6 · Critic 感知器 / 信号桶 + 记忆存储 (FTS5 + EverOS 降级)

> 阶段：P0 调研验证 · 任务 P0-5：读 `critic.py` + `signal_buckets.yaml`：8 维行为信号→文本映射，纠错层落点  
> 阶段：P0 调研验证 · 任务 P0-6：读 `memory/soulmem.py` + `memory_store.py`：FTS5 与 EverOS 分层，EverOS 缺席时的降级路径

---

## P0-5 · Critic 感知器 + signal_buckets.yaml

### critic.py (`engine/genome/critic.py`)

Critic 是 LLM-based 的用户意图情感感知器（v10 Hybrid 架构）。输入一段用户消息，输出四组数据：

| 输出组 | 维度 | 内容 | 范围 |
|---|---|---|---|
| 1. 对话上下文 | 8D | user_emotion, topic_intimacy, time_of_day, conversation_depth, user_engagement, conflict_level, novelty_level, user_vulnerability | [-1,1] 或 [0,1] |
| 2. 驱力挫败变化 | 5D | connection / novelty / expression / safety / play 的挫败 delta | [-3, 3] |
| 3. 关系感知变化 | 3D | relationship_delta, trust_delta, emotional_valence | [-1, 1] |
| 4. 驱力需求满足 | 5D | connection / novelty / expression / safety / play 的满足量 | [0, 0.3] |

**关键区分（注释第 48–51 行）**：
- `frustration_delta`：反映"挫败变化"（负=缓解，间接情绪变化）
- `drive_satisfaction`：反映"需求被直接满足"（用户行为主动满足 Agent 内在渴望）
- 同一轮对话中，两者不应对同一个驱力同时有大幅变化

**Prompt 构建**：`render_prompt("critic", fallback=_FALLBACK_CRITIC, ...)`，注入：
- `frustration_json`：Agent 当前各驱力挫败值
- `user_profile_section`：EverMemOS 用户历史画像（关系感知辅助）
- `episode_section`：历史对话叙事（判断 conversation_depth / topic_intimacy）
- `persona_section`：P1 角色感知提示（不同性格对同一句话的需求满足感不同）

**容错机制**：JSON 解析失败时重试一次（追加"请只输出JSON，不要说其他话"）；重试仍失败则返回全 0/0.5 默认值。还会 strip Qwen3 的 `think` 标签和 Markdown 代码块。

**8D context 键**（与 `genome_engine.py` 的 CONTEXT_FEATURES 顺序一致，但顺序略有不同，Critic 使用 `user_emotion, topic_intimacy, time_of_day, conversation_depth, user_engagement, conflict_level, novelty_level, user_vulnerability`）：

| # | 键 | 含义 |
|---|---|---|
| 0 | user_emotion | -1=负面 → 1=正面 |
| 1 | topic_intimacy | 0=公事 → 1=私密 |
| 2 | time_of_day | 0=白天 → 1=深夜 |
| 3 | conversation_depth | 0=刚开始 → 1=聊很久 |
| 4 | user_engagement | 0=敷衍 → 1=投入 |
| 5 | conflict_level | 0=和谐 → 1=冲突 |
| 6 | novelty_level | 0=日常 → 1=全新 |
| 7 | user_vulnerability | 0=防御 → 1=敞开 |

### signal_buckets.yaml (`engine/prompts/signal_buckets.yaml`)

定义 8 个行为信号 + 5 个驱力的文本标签和尺度锚点，供 `to_prompt_injection_from_signals()` 生成 prompt 时的文本描述：

**8 个信号**（每个：label, emoji_label, low_anchor, high_anchor，英文变体）：

| 信号 | 中文标签 | 低端锚点 | 高端锚点 |
|---|---|---|---|
| directness | 🎯 直接度 | 委婉 | 直白 |
| vulnerability | 💧 坦露度 | 封闭 | 袖露 |
| playfulness | 🎪 玩闹度 | 正经 | 调皮 |
| initiative | 🚀 主动度 | 被动 | 主导 |
| depth | 🌊 深度 | 闲聊 | 探底 |
| warmth | 🔥 温暖度 | 疏离 | 热切 |
| defiance | ⚡ 倔强度 | 随和 | 硬杠 |
| curiosity | 🔍 好奇度 | 无感 | 追问 |

**5 个驱力**（每个：label, emoji_label）：

| 驱力 | 中文标签 |
|---|---|
| connection | 🔗 联结 |
| novelty | ✨ 新鲜 |
| expression | 💬 表达 |
| safety | 🛡️ 安全 |
| play | 🎭 玩闹 |

### 纠错层落点（P0-5 结论）

Critic 本身是**输入端感知器**，不是纠错层。纠错层（Correction Layer）在 P3 中的落点是：
- `engine_params.crystal_threshold`：结晶门控阈值，决定哪些对话值得长期记住
- `engine_params.phase_threshold`：相位转移阈值，挫败超出后性格突变
- `temp_coeff` / `temp_floor`：热力学噪声，控制行为不可预测性
- Critic 的 `drive_satisfaction` 输出 → `Agent.learn()` 的 `drive_satisfaction` 参数 → Hebbian 学习 + 驱力满足

**P3 映射建议**：Distilly 的"纠错层"（用户修正、偏好覆盖）→ 映射为 `crystal_threshold` 调整 + 运行期 `MemoryStore` 事实写入 + Critic 输入特征校准（`persona_hint` / `user_profile` 注入）。

---

## P0-6 · 记忆存储 (FTS5 + EverOS 降级)

### soulmem.py (`memory/soulmem.py`)

**SoulMem 行为记忆抽象基类**（常驻层，总是启用），定义接口：

| 方法 | 签名 | 说明 |
|---|---|---|
| `add` | (user_id, persona_id, content, category, importance, source_turn) → int | 写入一条记忆，返回 ID |
| `search` | (user_id, persona_id, query, limit=5) → list[Memory] | FTS5 全文检索 |
| `build_memory_context` | (user_id, persona_id, current_query="", max_items=8) → Optional[str] | 构造注入 prompt 的记忆上下文字符串 |
| `close` | () | 关闭存储 |

当前阶段**仅定义接口**，facade (`memory_store.py`) 保留不动。

### memory_store.py (`memory/memory_store.py`)

SQLite FTS5 实现的长期记忆存储，**无外部向量数据库依赖**，未来可升级为 sqlite-vec 混合检索。

**表结构**：

| 表 | 用途 |
|---|---|
| `memories` | 记忆主表 (id, user_id, persona_id, content, category, importance, source_turn, created_at) |
| `memories_fts` | FTS5 虚拟表，content 全文索引 |
| 触发器 | `memories_ai` / `memories_ad`：自动同步插入/删除到 FTS 索引 |
| 索引 | `idx_memories_user_persona`：(user_id, persona_id) 查询加速 |

**Memory dataclass**：
```python
memory_id, user_id, persona_id, content, category (conversation|fact|event|preference), 
importance (0.0-1.0), source_turn, created_at
```

**关键 API**：

| 方法 | 说明 |
|---|---|
| `add(...)` | 写入一条记忆，返回 ID |
| `add_facts(user_id, persona_id, facts)` | 批量写入高重要度事实（0.9），自动去重（按内容查重） |
| `search(user_id, persona_id, query, limit=5)` | FTS5 检索，按 rank 排序 |
| `get_recent(..., limit=10)` | 获取最近记忆 |
| `get_important(..., min_importance=0.7, limit=10)` | 获取高重要度记忆 |
| `build_memory_context(user_id, persona_id, current_query="", max_items=8)` | **策略**：1) 高重要度事实(>=0.8, 最多4条) → 2) 查询相关命中 → 3) 最近记忆填充，去重，拼接成 `- [分类] 内容` 字符串 |
| `count(user_id, persona_id)` | 记忆总数 |
| `close()` | 关闭数据库 |

**EverOS 降级路径**（在 `main.py` 中体现，`memory_store.py` 本身不依赖 EverOS）：

```python
mem_cfg = get_memory_config()
if mem_cfg["enabled"] and (mem_cfg["base_url"] or mem_cfg["api_key"]):
    evermemos = EverMemOSClient(base_url=..., api_key=...)
    if evermemos.available:
        await evermemos.verify_connection()
else:
    evermemos = None  # 降级到本地 MemoryStore
```

- **EverOS 缺席时**：`evermemos = None`，ChatAgent 的 `evermemos_status = "OFF"`，所有 EverMemOS 相关调用（`_evermemos_gather`, `_evermemos_store_bg`, `_evermemos_search_bg`）都会安全降级为空操作。
- **MemoryStore 是常驻层**：无论 EverOS 是否可用，SQLite FTS5 本地记忆始终可用，无需 API key。

### P3 映射建议（P0-6 结论）

| Distilly 产出 | OpenHer 落点 | 映射方式 |
|---|---|---|
| 事实/共同记忆 | `MemoryStore.add(..., category="fact", importance=0.9)` | 带 source_turn 和 created_at，FTS5 可检索 |
| 用户偏好 | `MemoryStore.add(..., category="preference")` | `build_memory_context` 优先返回高重要度事实 |
| EverOS episodes（可选） | `EverMemOSClient.store_proactive_turn(...)` / `store_turn` | 开关式，无 API key 时自动跳过，降级到本地 |
| 对话轮次记录 | `ChatAgent` Step 11 `memory_store.add(..., category="user_message")` | 每轮用户输入持久化 |

---

## 合并结论：P0-5 + P0-6 对 P3 桥接层的意义

| Distilly 文本人格 | OpenHer 落点 |
|---|---|
| 说话风格/口头禅 | `style_memory` 种子（P0-3 已调研）+ `signal_buckets.yaml` 标签覆盖 |
| 情绪触发/冲突模式 | Critic 输入特征（`persona_hint` / `user_profile` 注入）+ `drive_baseline` 5 维 |
| 冲突强度/爆发阈值 | `engine_params.phase_threshold` + `temp_coeff` |
| 纠错层（用户修正/偏好覆盖） | `MemoryStore.add(category="fact", importance=0.9)` + `crystal_threshold` |
| 事实/共同记忆 | `MemoryStore` FTS5（常驻，无 key 可用） + EverOS episodes（开关式，可选） |
| 身份字段 | SOUL.md frontmatter（P0-1 已调研） |

P0 阶段已完成 6/10。下一步可进入 P0-7（Termux 环境盘点）或直接开始 P3 桥接层设计。
