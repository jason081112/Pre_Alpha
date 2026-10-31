# P3-41 · 生成物 ↔ 引擎一致性校验

对应看板 #41。产出 `bridge/consistency.py`、`alpha persona verify`、
`tests/test_consistency.py`（21 项）。

## 一个必须先讲清的事实

引擎里有**两套并行的人格体系**，很多人会把它们混为一谈：

| | `PersonaLoader` | `PersonaStore` |
|---|---|---|
| 输入 | `<dir>/<id>/SOUL.md`（markdown + frontmatter） | `<dir>/<id>/profile.json` |
| 用途 | **对话链路**：解析四段正文注入 prompt | 动态生成路径的持久化（LLM 生成 → 锁定 ⇒ JSON） |
| 是否影响对话 | 是 | **否** |

所以「store 注册成功」**不等于**「这个人格能被对话使用」。真正的判据是 loader 能否
把四段正文解析出来。本 issue 把这两条分开校验，不合并成一个"成功"。

## 三项契约

### 1. 文本契约（不需要 vendor，CI 必跑）

只读 SOUL.md 就能判：

| 检查 | 判级 |
|---|---|
| frontmatter 存在，含 `name` / `lang` | 缺 `name`/`lang` → warn |
| `lang ∈ {zh, en}` | 否则 warn（loader 只按 zh 处理） |
| `gender ∈ {female, male}`、`age` 是整数 | 否则 warn |
| 正文不含 `Layer N` | 否则 warn（loader 不识别该标题） |
| 正文不含 `【内心独白】`/`【最终回复】` | 否则 warn（few-shot 拼装残留） |
| 性格 / 说话风格 / 背景故事 三段 | 全空 → **fail**；缺一两段 → warn |
| 行为规则段 | 空 → warn（不阻断，但冲突反应会退化） |

这一层的存在意义：CI 里通常没有 `vendor/OpenHer`，如果只做加载校验，等于每次都 skipped、
等于没测。文本契约把能测的部分压到零依赖。

### 2. 加载契约（需 vendor）

交给真 `PersonaLoader`，断言四段的解析结果**都非空**。

`raw_content` 不能作为判据 —— 它是 `post.content`，也就是**整个 body**，永远非空。
真正的退化信号是「三核心段全空而 raw_content 有内容」，那说明标题写错了、人格静默丢失。
这一条是本校验的核心断言。

### 3. 注册契约（需 vendor）

`PersonProfile` → vendor `PersonaProfile` → `save()` → `load()`，逐字段比对
`id / name / gender / age / tags` 是否往返一致。

`personality / speaking_style / backstory` 直接从 SOUL 正文同源字段填充，**不另写一份**，
避免两套体系内容漂移。

## 判级规则

```
fail > warn > skipped > pass
```

**skipped 永远不等于 pass**。vendor 缺失时报 `skipped` 并写明原因，绝不把没跑过的检查
说成通过（`test_skipped_never_reported_as_pass` 锁死）。

## 用法

```bash
alpha persona verify .data/soul              # 目录级校验
alpha persona verify .data/soul --id xiaolan # 指定人格
alpha persona verify .data/soul --json       # 机器可读
alpha persona verify .data/soul --check      # CI：warn 也算不通过
```

输出：

```
[alpha] 校验 /tmp/cons（1 个人格）

✓ xiaolan  →  pass
   ✓ text     pass
   ✓ load     pass
   ✓ register pass

[alpha] 整体: pass
```

## 验证

```bash
python3 tests/test_consistency.py   # 21 passed
python3 -m pytest tests/ -q         # 246 passed
```

真实链路跑通：`profile2soul --write` 生成 → `verify` 三项全 pass
（text/load/register，`raw_content_present: true` 但未退化）。

## 边界

- 校验的是**契约**，不是"像不像这个人"。风格探针属 #86，需要运行中的引擎，仍为骨架。
- 不校验 seeds.bin（#35 的产物，与 SOUL.md 独立）。
- `profile.json` 缺失时注册契约报 skipped 而非 fail —— 走纯 loader 路径是合法部署形态。
