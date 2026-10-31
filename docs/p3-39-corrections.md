# P3-39 · 纠错层：运行期可写约束

对应看板 #39。产出 `shell/corrections.py`、`alpha persona corrections`、
`tests/test_corrections.py`（28 项）。

## 为什么不写进 SOUL.md

三个理由，都是硬约束：

1. **golden 快照会被污染**。#43 用 sha256 锁住了 3 份 SOUL.md。若用户每次纠正都改 SOUL.md，
   快照天天漂移，回归失效。
2. **loader 没有多余槽位**。`PersonaLoader` 只精确匹配 4 个 H2（#28），纠错条目塞进去要么被忽略，
   要么污染「行为规则」段。
3. **纠错是每用户、每人格的运行期状态**，SOUL.md 是产物级静态文件，生命周期不同。

所以纠错条目独立存 SQLite，由 prompt 组装时**额外注入**。`render_section()` 产出的正是
要拼进去的那段文本，#44 组装 prompt 时调用它即可。

## 数据模型

```sql
CREATE TABLE corrections (
    id, persona_id, user_id, scene, wrong, correct,
    source,        -- runtime | distilled | import
    active,        -- 可停用而非只能删
    note, created_at, updated_at
);
UNIQUE (persona_id, user_id, scene, wrong)
```

三个设计决定：

| 决定 | 原因 |
|---|---|
| `active` 而非只删除 | 用户常只想**临时**压住一句话，停用可逆、可审计 |
| 唯一键落在 `(persona, user, scene, wrong)` | 同一句改三次不该得到三段互相矛盾的规则，而是覆盖 `correct` |
| `source` 区分 distilled / runtime | 注入时运行期条目优先（用户当场说的比蒸馏出的更当期） |

## 写入校验

拒绝以下条目，不静默接受：

- `wrong` / `correct` 为空，或两者相同（没有纠错意义）
- `wrong` / `correct` > 200 字，`scene` > 60 字
- 含动作/旁白标记 `（）` / `**` / `【】` —— 会被 `clean_action_markers` 剥掉（#35 实测）
- 含疑似隐私（手机号、邮箱）

## 注入文本

```text
【纠错规则】以下说法不要用，遇到对应场景时改用右边的说法：
- [对方道歉] 不要说「没关系，我理解」，改说「嗯，先这样。」
```

- 无条目时返回**空串**，不产生任何空壳标记。
- 上限 8 条（`MAX_SECTION_ITEMS`），多了会挤掉人格本身。
- 语言随 `lang` 切中英。

## 与静态纠错的关系

`profile.corrections`（蒸馏产物）用 `seed` 导入，**幂等**：已存在的键跳过，
**不覆盖**运行期的修改。用户改过的口径不会被重新蒸馏的结果冲掉。

## 用法

```bash
alpha persona corrections xiaolan add --scene 对方道歉 --wrong "没关系，我理解" --correct "嗯，先这样。"
alpha persona corrections xiaolan list [--all]
alpha persona corrections xiaolan disable --id 3     # 可逆
alpha persona corrections xiaolan remove --id 3 --yes
alpha persona corrections xiaolan section            # 看要注入 prompt 的文本
alpha persona corrections xiaolan seed --file profile.json
```

## 验证

```bash
python3 tests/test_corrections.py   # 28 passed
python3 -m pytest tests/ -q         # 308 passed
```

覆盖：校验 6 类（空/相同/超长/动作标记/隐私）、增改查、重复覆盖不堆积、停用与恢复、
删除、人格隔离、用户隔离、统计、静态导入幂等、**不覆盖运行期修改**、空段返回空串、
注入格式中英、条数上限、运行期优先、无空壳标记、**纠错操作不修改 SOUL.md 字节**、CLI 全流程。

## 边界

- 未接入对话链路：`render_section()` 是给 #44 用的接口，真实注入效果需引擎跑起来验证。
- 不做语义去重（"没关系"和"没事的"会被当成两条）。需要模型才能判断，属后续迭代。
- 纠错是否生效只能靠 #44 的 20 轮验收观察，本 issue 只保证存储、校验、注入文本三者正确。
