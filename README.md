# Pre_Alpha

**Local-first companion AI** — 把聊天记录蒸馏成一个人格，再让 OpenHer 引擎把她"活"起来。

> Distilly-style chat-log distillation (front-end) + OpenHer persona engine (back-end).

## 两阶段流水线

```
聊天记录(微信/飞书/QQ)
   │  ① 离线蒸馏  Distilly 流程
   ▼
Person Profile（六层人格 / 口头禅 / 纠错层）
   │  ② ★ 桥接  bridge/profile2soul.py
   ├─ 身份字段          → SOUL.md frontmatter
   ├─ 表达风格 + 口头禅 → style_memory 种子
   ├─ 事实/共同记忆     → SQLite FTS5 (+ EverOS 可选)
   ├─ 情绪触发·冲突     → drive_baseline 5 维
   └─ 纠错层            → critic / engine_params
   │  ③ 在线对话  OpenHer 引擎（25D→24D→8D 行为信号）
   ▼
REST / Web 对话
```

**核心创新点**：Distilly 产出的是**文本人格**，而 OpenHer 明确主张"性格不写在 prompt 里，而是从驱力与神经权重中涌现"。两者无法直接拼接 —— 中间的 `profile2soul` 映射层就是本项目要解决的问题。

## 状态

🚧 早期。65 项开发任务见 [Project Alpha](https://github.com/users/jason081112/projects/1)。

## 隐私

- 所有解析、清洗、索引均在本地完成；只有显式开启云端 provider 时才上送脱敏后的上下文。
- 原始聊天记录与 `.env` 一律不入库（见 `.gitignore`）。
- 蒸馏**他人**的聊天记录前，请先取得当事人同意。详见 `ETHICS.md`（待补）。

## 致谢与许可证

- [OpenHer](https://github.com/kellyvv/OpenHer)（Apache-2.0）· [Distilly](https://github.com/titanwings/distilly)（MIT）· [EverOS](https://github.com/EverMind-AI/EverOS)（Apache-2.0）
- 详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

本仓库采用 [Apache-2.0](LICENSE)。
