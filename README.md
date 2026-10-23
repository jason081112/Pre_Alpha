# Pre_Alpha

**陪伴型 AI 引擎** — 把聊天记录蒸馏成一个人格，再让 OpenHer 引擎把她"活"起来。**云端运行、Web/PWA 优先。**

> Distilly-style chat-log distillation (front-end) + OpenHer persona engine (back-end).

## 开源边界

本项目是 **open core**，不是全开源：

- 公开：core、Web/PWA 客户端、CLI、单用户自托管、本地蒸馏框架
- 私有：托管控制面、精修蒸馏、评测资产、审核、计费
- 边界细节：[`docs/open-core.md`](docs/open-core.md)
- 许可：[`LICENSING.md`](LICENSING.md)
- 人格契约：[`persona.schema.json`](persona.schema.json) / [`docs/refine-api.md`](docs/refine-api.md)

## 部署形态

- **引擎统一云端运行**，手机上只跑轻量 Web/PWA 客户端（Termux 仅作开发环境）
- **上游 OpenHer 加壳、不改核心**：外围叠加「接入层 + 主动消息调度器 + 数据 / 账号层」
- 决策记录见 [`docs/adr/0001-deployment-cloud-web-first.md`](docs/adr/0001-deployment-cloud-web-first.md)

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
Web/PWA 客户端 ← HTTPS / WebSocket → 云端引擎
```

**核心创新点**：Distilly 产出的是**文本人格**，而 OpenHer 明确主张"性格不写在 prompt 里，而是从驱力与神经权重中涌现"。两者无法直接拼接 —— 中间的 `profile2soul` 映射层就是本项目要解决的问题。

## 状态

🚧 早期。78 项开发任务见 [Project Alpha](https://github.com/users/jason081112/projects/1)；P0 调研验证 10/10 完成。

## 隐私

- **云端模式**：传输加密 + 静态加密 + 不训练 + 不分享 + 可导出可删除 + 首次启动明示
- **本地模式**：作为未来的第二个 Store 实现保留（架构不锁死），当前阶段不实现
- 原始聊天记录与 `.env` 一律不入库（见 `.gitignore`）
- 蒸馏**他人**的聊天记录前，请先取得当事人同意。详见 `ETHICS.md`（待补）

## 文档

- 架构总览：[`docs/architecture.md`](docs/architecture.md)
- 决策记录：[`docs/adr/`](docs/adr/)
- P0 调研：`docs/p0-0*.md`

## 致谢与许可证

- [OpenHer](https://github.com/kellyvv/OpenHer)（Apache-2.0）· [Distilly](https://github.com/titanwings/distilly)（MIT）· [EverOS](https://github.com/EverMind-AI/EverOS)（Apache-2.0）
- 详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

本仓库采用 [Apache-2.0](LICENSE)。
