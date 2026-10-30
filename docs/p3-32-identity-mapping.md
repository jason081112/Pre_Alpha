# P3-32 身份字段 → SOUL.md frontmatter

对应看板 #32。实现 `bridge/profile2soul.py` 的身份字段映射；正文四槽属 #33–#38。

## 映射表

| `PersonProfile` 字段 | frontmatter 键 | 规则 |
|---|---|---|
| `identity.name` | `name` | `source`/`inferred` 写入 |
| `identity.name_zh` | `name_zh` | 同上；缺失不写 |
| `identity.age` | `age` | 转 `int`；缺失不写（loader 无默认值） |
| `identity.gender` | `gender` | 缺失不写，**但要报警**（见下） |
| `identity.mbti` | `mbti` | 缺失不写；产物通常没有 MBTI |
| `identity.lang` | `lang` | 必写，默认 `zh` |
| `meta.tags` | `tags` | 透传（`{zh: [...]}` 或 `[...]`） |
| `meta.bio` | `bio` | 透传；loader 不注入 prompt |

不写入的键：`genome_seed`（属 #33–#38）、任何 `layer_*`、任何 `basis`。

## 三态处理

| provenance | 行为 |
|---|---|
| `source` | 写值 |
| `inferred` | 写值，**`basis` 不写入**（loader 不读，且推断说明会污染人格文本） |
| `missing` | 不写该键；若该键有 loader 默认值，追加 warning |

## `gender` 陷阱

`PersonaLoader` 的实现是 `meta.get("gender", "female")` —— 不写 `gender` 会**悄悄变成 female**。
#30 §7.2 已在实测中确认这个损耗（小岚 gender 未提供，加载后显示 `female`）。

本实现的处理：

1. 默认模式：不写键，但在 `warnings` 里明确报告
   `gender 缺失：不写入 frontmatter，但 PersonaLoader 会回落为 'female'`；
2. `strict=True`：直接抛 `ValueError`，让调用方决定是补材料还是接受。

这样"不知道"不会被静默伪装成"女"。

## 输出结构

```python
profile_to_soul(profile) -> {
    "frontmatter": {"name": "小岚", "name_zh": "小岚", "lang": "zh"},
    "body": {},                      # #33–#38 填充
    "warnings": ["gender 缺失：…回落为 'female'"],
    "markdown": "---\nname: 小岚\n…\n---\n",
}
```

`markdown` 由 `render_frontmatter()` 手写渲染（不引入 PyYAML 依赖），
支持标量、列表与一层嵌套字典；含 `:` `#` 等字符的值自动加引号转义。

## 硬约束

| 约束 | 原因 |
|---|---|
| 只写 `LOADER_KEYS` 内的键 | 写别的键 loader 不读也不报错，等于静默丢弃 |
| 输出不得含 `Layer N` | #30 实测会被当成结构标记泄漏 |
| `basis` 不进入 frontmatter | 同上，且推断说明会污染人格文本 |
| 值为空时跳过并告警 | 防止 `name: ""` 这类空断言 |

## 验证

```bash
python3 tests/test_profile2soul.py     # 11 passed
python3 -m pytest tests/ -q            # 139 passed
```

覆盖：source 写入、missing 省略、gender 回落告警、strict 抛错、inferred 值写入但 basis 不泄漏、
只写白名单键、markdown 形状、引号转义、tags 透传、YAML 往返解析、`Layer N` 拒绝。

## 未覆盖

- 正文四槽映射（`personality` / `speaking_style` / `background` / `behavioral_rules`）→ #33–#38
- `genome_seed`（`drive_baseline` / `engine_params`）→ #33 / #34
- `style_memory` 种子 → #35
- `SHELL.md` → #40
- 与 golden 的快照回归 → #43
