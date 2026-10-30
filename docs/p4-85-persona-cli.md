# P4-85 A 部分：`alpha persona` CLI

对应看板 #85 的 **A 部分**（CLI 骨架）。Web `/personas` 页面属 B 部分，等 #46 / #72。

## 命令

```bash
alpha persona new --from scratch --id xiaolan --name 小岚
alpha persona new --from builtin:luna --id luna-copy --name 露娜
alpha persona list [--include-deleted]
alpha persona show <id>
alpha persona preview <id>
alpha persona edit <id> --set identity.mbti=INTJ
alpha persona delete <id>
```

- **默认 dry-run**，落盘加 `--yes`；删除也必须 `--yes`
- `--from` 支持 `scratch` / `builtin:<id>` / `distill:<path>`（后者依赖 #42，当前明确报未就绪）
- `--user` 可写在 `persona` 后或子命令后，默认取 `ALPHA_USER` 或 `default`

## 三档字段，避免一上来面对 13 个参数

| 档 | 内容 |
|---|---|
| 必填 | `identity.name` |
| `--set` 可改 | `identity.name/name_zh/age/gender/mbti`、`expression.rhythm`、`relation.label/status` |
| 明确拒绝 | `engine.*` 等高级字段（提示「不支持修改」，防止误改标定值） |

`--set` 只开放含义确定的字段；`engine_params` / `drive_baseline` 属人工标定（#33/#34），
CLI 不允许随手改。

## 内置复制：SOUL.md → PersonProfile

内置人格只有 `SOUL.md`，所以新增 `bridge/soul2profile.py` 做最小导入：

| SOUL.md | → PersonProfile | 说明 |
|---|---|---|
| frontmatter `name` / `name_zh` / `age` / `gender` / `mbti` / `lang` | `identity.*` | 直通 |
| 正文「说话风格」首段 | `expression.rhythm` | 直通 |
| 「说话风格」里的引号短句 | `expression.habits` | 提取 `“嗯。”` 这类，最多 20 条 |
| 「行为规则」的 bullet | `hard_rules` | 直通 |
| — | `relation` / `logic` / `conflict` | **标 `missing`**，不猜 |

解析器不依赖 PyYAML：内置 persona 的 frontmatter 只有标量与一层列表。

## 交互原则的落地

| 原则 | 实现 |
|---|---|
| 缺材料显性化 | `scratch` 创建时 age/gender/mbti 直接标 `missing`，dry-run 里可见 |
| 不隐藏损耗 | `gender` 缺失时打印「PersonaLoader 会回落为 'female'」 |
| 推断要依据 | `--set` 只能写 `source` 状态；填 `inferred` 需改 profile.json（不走 CLI） |
| 校验不抛栈 | 校验失败逐条中文输出，退出码 2 |

## 验证

```bash
python3 tests/test_cli_persona.py   # 12 passed
python3 -m pytest tests/ -q         # 169 passed
```

覆盖：dry-run 不落盘、`--yes` 落盘、缺 `--id`、gender 告警、`--set` 成功/非法字段/非法整数、
preview、删除需 `--yes`、内置导入（含 `name` 覆盖与 `relation` 标 missing）、未知内置 id、
空列表提示。

## 未覆盖（B 部分）

- Web `/personas` 三步向导（依赖 #46 / #72）
- 试聊沙箱（复用 #86 探针）
- 从蒸馏创建（依赖 #42 / #44）
