# P3-35 口头禅 → seeds.bin

对应看板 #35。产出 `bridge/seeds.py`、`alpha persona seeds`、`tests/fixtures/seeds/xiaolan.txt`、
`tests/test_seeds.py`（17 项）。

## 先解决阻塞：`seeds.bin` 是什么

此前 #28 把它标为 `insufficient source material`。实际读代码 + 解压后确认：

**它是 gzip 压缩的 JSON。**

```python
# engine/genome/style_memory.py::_auto_import_seeds
data = json.loads(gzip.decompress(f.read()).decode("utf-8"))
for pid, seeds in data.items():
    save_genesis_to_db(pid, seeds, state_db_path)
```

结构为 `{persona_id: [seed, ...]}`，每条 seed：

| 字段 | 含义 | 实测值域 |
|---|---|---|
| `user_input` | 触发这句话的用户输入 | 文本 |
| `monologue` | 内心独白 | 文本 |
| `reply` | 实际说出口的话 | 文本 |
| `vector` | 8 维情境坐标 | 见下 |
| `mass` | 引力质量 | 1.0 / 2.0 |
| `lang` | 语言 | zh / en |

上游共 642 条，9 个人格（nova 没有）。`vector` 顺序由 `CONTEXT_KEYS` 写死：

```
conflict_level · user_emotion · user_engagement · user_vulnerability
topic_intimacy · conversation_depth · novelty_level · time_of_day
```

`user_emotion` 可为负（−1..1），其余 0..1。

## 为什么 monologue 必须人工写

两条硬证据：

1. **它会进 prompt**。`build_few_shot_prompt()` 在单次模式下拼的是：
   ```
   --- 潜意识切片 1 [基因] ---
   【内心独白】{monologue}
   【最终回复】{reply}
   ```
   空的 monologue 会留下一个空壳标记 —— 正是 #30 验收要防的 `【内心独白】` 泄漏形态。
2. **Distilly 不产这个字段**（#28 §4 已记录）。模型生成等于编造"她当时在想什么"。

所以本实现**只接受人工素材**，不生成、不猜测。

## 素材格式（四行一段）

```text
场景: 对方说今晚加班，又赶不上吃饭了
心里: 第三次了。上回也是这么说的。我要是说"没事"，下次大概还这样。
回复: 算了，你忙。
情境: 被忽略
---
```

校验规则：

| 规则 | 阈值 |
|---|---|
| `心里` 长度 | 15–80 字 |
| `回复` 长度 | ≤ 40 字 |
| `场景` 长度 | ≤ 60 字 |
| `心里` ≠ `回复` | 必须有落差 |
| 禁动作/旁白 | `（…）`、`*…*`、`【…】` |
| 禁隐私 | 手机号、邮箱 |
| 情境标签 | 必须在预设表内 |
| 覆盖要求 | 至少含「被忽略」与「冲突升级」 |

不合格逐条报错，不静默丢弃。

## 情境 → 8 维坐标

| 标签 | conflict | emotion | engage | vuln | intimacy | depth | novelty | time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 日常闲聊 | 0.0 | 0.2 | 0.5 | 0.1 | 0.4 | 0.2 | 0.2 | 0.5 |
| 被忽略 | 0.5 | −0.5 | 0.4 | 0.2 | 0.5 | 0.4 | 0.1 | 0.5 |
| 对方道歉 | 0.3 | −0.2 | 0.6 | 0.4 | 0.6 | 0.5 | 0.1 | 0.5 |
| 表达关心 | 0.0 | 0.4 | 0.7 | 0.3 | 0.8 | 0.5 | 0.1 | 0.5 |
| 亲密靠近 | 0.0 | 0.7 | 0.8 | 0.5 | 0.9 | 0.6 | 0.2 | 0.5 |
| 冲突升级 | 0.8 | −0.7 | 0.3 | 0.3 | 0.5 | 0.5 | 0.1 | 0.5 |
| 深夜低落 | 0.2 | −0.3 | 0.4 | 0.6 | 0.6 | 0.6 | 0.1 | 0.9 |

这些数值是**人工设计**（标 `inferred` 的依据），不是从上游统计出来的 —— 上游 seed 的 vector
由作者逐条手写，没有可回归的规则。

## mass 策略

`被忽略` 与 `冲突升级` 给 **2.0**（更易召回），其余 1.0。理由：这两类是人格的核心反应区，
#30 实测最大的缺口正是“被忽略时没有收短”。

## 产物与部署

```bash
alpha persona seeds xiaolan --material tests/fixtures/seeds/xiaolan.txt          # dry-run 看表
alpha persona seeds xiaolan --material tests/fixtures/seeds/xiaolan.txt --yes    # 写 .data/seeds.bin
```

默认写 `.data/seeds.bin`，**不碰 `vendor/OpenHer/persona/seeds.bin`** —— 那是上游文件，
CLI 误写会污染内置人格（#33 踩过一次）。要生效需显式合并到引擎目录：

```bash
python3 -c "
import gzip, json
from pathlib import Path
mine = json.loads(gzip.decompress(Path('.data/seeds.bin').read_bytes()))
up = json.loads(gzip.decompress(Path('vendor/OpenHer/persona/seeds.bin').read_bytes()))
up.update(mine)
Path('vendor/OpenHer/persona/seeds.bin').write_bytes(gzip.compress(json.dumps(up, ensure_ascii=False).encode()))
"
```

## 本次素材（小岚，8 条）

| # | 情境 | mass | 回复 |
|---|---|---:|---|
| 1 | 被忽略 | 2.0 | 算了，你忙。 |
| 2 | 被忽略 | 2.0 | 嗯，先这样。 |
| 3 | 对方道歉 | 1.0 | 几点？别又临时有事。 |
| 4 | 冲突升级 | 2.0 | 忙不是理由，别再拿它糊弄我。 |
| 5 | 表达关心 | 1.0 | 多穿点，到了说一声。 |
| 6 | 亲密靠近 | 1.0 | 记得还挺清楚。什么时候去？ |
| 7 | 深夜低落 | 1.0 | 我在。要跟我说说吗？ |
| 8 | 冲突升级 | 2.0 | 别扯上次，先把这次说清楚。 |

素材本身入库（`tests/fixtures/seeds/xiaolan.txt`），可追溯、可复现。

## 验证

```bash
python3 tests/test_seeds.py   # 17 passed
```

覆盖：fixture 解析与校验零错误、情境覆盖、8 维值域（含 emotion 可负）、mass 策略、
seed 字段与上游一致、6 类负面用例（心里过短/回复过长/无落差/含动作标记/非法情境/缺核心情境）、
gzip 往返与合并、覆盖同人格、路径构建。

## 未完成

**召回效果未验证**。seeds 是否真让“被忽略时收短”出现，需要引擎跑起来（Codespace），
属于 #44 的验收范围。当前只保证：格式与上游一致、内容经人工撰写、写入不破坏其他人格。
