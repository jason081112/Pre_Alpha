# P3-36 · 事实 / 共同记忆 → SQLite FTS5

对应看板 #36。产出 `shell/memory.py`、`alpha persona memory`、`tests/test_memory.py`（30 项）。

## 为什么不直接用 vendor 的 `MemoryStore`

vendor（`memory/memory_store.py`）的表只有 `content / category / importance / source_turn`，
**没有 role，也没有 source**。本 issue 要求「role / 时间戳 / source」，装不下。

桥接层的职责本来就是把我们的模型映射到引擎，所以自建，并留 `export_for_engine()` 把条目转成
vendor `MemoryStore.add()` 能吃的参数（丢掉 role/source，保留 content/category/importance/source_turn）。

与 `shell/corrections.py` 的分工：纠错层是「不要说这个」，记忆层是「发生过这些事」。

## 中文检索：这里踩了一个真实的坑

实测 SQLite 3.46：

| tokenizer | `加班`（2 字） | `临时失约`（4 字） | 整句 |
|---|---|---|---|
| `trigram` | **0 条** | 1 条 | 1 条 |
| `unicode61` | 0 条 | 0 条 | 1 条 |

- `trigram` 对**少于 3 个字**的查询直接返回空 —— 而「加班」「对方」正是最常用的中文词。
- `unicode61` 把整句当一个 token（无空格不切分），只能整句匹配。

所以采用两段式：

1. **召回层**：写入时把中文逐字切分（`对方多次因加班` → `对 方 多 次 因 加 班`）喂给
   `unicode61` 索引，查询同样切分后 `MATCH`。
2. **准确层**：召回结果再用 `content LIKE '%query%'` 精筛，消除逐字切分带来的误命中。

FTS 无解时（单字、纯符号）退化为朴素 `LIKE` 查询，保证「店」「累」这类单字也能查到。

## 表结构

```sql
CREATE TABLE memories (
    id, user_id, persona_id,
    role,        -- user | assistant | system | fact
    content,
    category,    -- fact | event | preference | conversation
    source,      -- runtime | distilled | import
    importance,  -- 0..1
    source_turn, -- 来源定位（profile.json / turn-id）
    tokens,      -- 切分后的索引串
    created_at, updated_at
);
UNIQUE (user_id, persona_id, content)         -- 同一件事不重复记
CREATE VIRTUAL TABLE memories_fts USING fts5(tokens, content='memories', content_rowid='id');
-- 三个触发器：插入 / 删除 / 更新同步索引
```

重复写入不会产生两条：唯一键触发 `ON CONFLICT`，`importance` 取 `MAX`（旧的高重要度不会被低的覆盖）。

## API

| 方法 | 说明 |
|---|---|
| `add(...)` | 单条写入，带 role/category/source/importance/source_turn |
| `add_facts(...)` | 批量高重要度事实，空串跳过，重复吸收 |
| `seed_from_profile(profile)` | 从 `relation.defining_facts` + `memory_signature` 导入，幂等 |
| `search(persona, query)` | 召回 + 精筛 |
| `get_recent` / `get_important` | 最近 / 高重要度 |
| `build_context(persona, query)` | 注入文本：重要事实 → 相关命中 → 最近补齐，去重，上限 8 条 |
| `export_for_engine(persona)` | 转成 vendor 参数形状 |
| `stats` | 按 role 计数 |

`build_context` 无记忆时返回**空串**，不产生空壳标记。

## 校验

拒绝：空内容、超 500 字、非法 role/category、疑似隐私（手机号/邮箱）、含 `【内心独白】`/`【最终回复】` 空壳标记。

## 用法

```bash
alpha persona memory xiaolan seed --file tests/fixtures/profiles/xiaolan.json
alpha persona memory xiaolan add --content "对方多次因加班临时失约" --role fact --importance 0.9
alpha persona memory xiaolan search --query 加班
alpha persona memory xiaolan context --query 店
alpha persona memory xiaolan export          # vendor 参数形状
```

实测（小岚 fixture）：导入 3 条事实 → `加班` 命中 1 条 → `context --query 店` 正常注入。

## 顺带修掉的两处

1. **CI 缺依赖**：`public-guard.yml` 从来没有 `pip install`，vendor 相关测试在 CI 上会
   `ModuleNotFoundError` 直接红。新增独立步骤安装 `python-frontmatter / pyyaml / jsonschema`。
2. **测试不该因环境缺依赖而假失败**：`test_loader_accepts_output_when_vendor_present`
   捕获 `ModuleNotFoundError` 并打印 `SKIP`，而不是 fail —— 跳过不等于通过（#41 的
   consistency 测试仍会报 `skipped`，不伪装 pass）。

## 验证

```bash
python3 tests/test_memory.py   # 30 passed
python3 -m pytest tests/ -q    # 338 passed
```

覆盖：中文逐字切分、**2 字词检索**（trigram 会失败的那条）、单字检索、空查询退化为最近、
重复吸收、importance 截断、人格/用户隔离、删除同步 FTS、重要度排序、按 role 统计、
批量导入去重、seed 幂等与 source 标记、隐私条目跳过、context 格式/上限/去重/英文、
export 形状、CLI 全流程。

## 边界

- 未接对话链路：`build_context()` 是给 #44 的接口，真实注入效果需引擎验证。
- 不做语义去重（"加班失约"与"因为加班没来"算两条），需模型，属后续迭代。
- 不走向量检索（`sqlite-vec` 未装），P0-6 已把混合检索列为未来升级项。
