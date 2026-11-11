# Pre_Alpha

**把聊天记录蒸馏成一个人格，再让它在云端活起来。**

Web / PWA 优先 · 引擎云端运行 · 原始聊天记录不上传、不入库。

[![public-guard](https://github.com/jason081112/Pre_Alpha/actions/workflows/public-guard.yml/badge.svg)](https://github.com/jason081112/Pre_Alpha/actions/workflows/public-guard.yml)
[![phase](https://img.shields.io/badge/phase-P4%20%C2%B7%20%E4%BA%A4%E4%BA%92%E5%B1%82-6f8cff)](#开发进度)
[![license](https://img.shields.io/badge/license-Apache--2.0%20%2B%20AGPL--3.0-3fb950)](LICENSING.md)

[项目主页](https://jason081112.github.io/Pre_Alpha/) · [开发看板](https://github.com/users/jason081112/projects/1) · [架构说明](docs/architecture.md) · [Open Core 边界](docs/open-core.md)

---

## 这是什么

Pre_Alpha 是一个 **隐私优先的 C 端陪伴 AI**。它不是再包一层聊天框，而是在解决两个上游项目之间的根本冲突：

| | Distilly | OpenHer |
|---|---|---|
| 人格载体 | 文本：六层人格 / 口头禅 / 纠错层 | 数值：5 维驱力 + 引擎参数 + 风格记忆 |
| 人格如何生效 | 写进 Skill，由宿主 Agent 读取 | 性格不写进 prompt，从动力学系统里涌现 |
| 记忆 | 静态 Markdown | KNN 风格记忆 + FTS5 + 可选 EverOS |

直接把两者拼起来，引擎读不到蒸馏出来的人格。中间的 **桥接层 `profile2soul`** 就是本项目自研的部分：把文本人格翻译成 SOUL.md、种子语料、驱力基线、记忆与运行期约束。

## 现在能做什么

- **Web 聊天页**：登录、切换人格、实时流式回复、多段气泡逐条推送、历史导出；单文件、无前端构建。
- **人格 API**：`/v1/personas` 创建 / 编辑 / 软删除 / 恢复 / fork，带乐观锁、版本快照、跨用户隔离。
- **人格体检**：`alpha persona check` 检查结构、隐私、加载与试聊；缺环境时显式 `skipped`，不伪装 `pass`。
- **蒸馏流水线**：聊天记录 → 规范化 → Analyze → Build → 六层人格；两套合成 golden（同事 / 恋爱）与结构校验。
- **桥接层**：PersonProfile → SOUL.md / `seeds.bin` / FTS5 记忆 / 纠错层；含 20 轮语气验收与一致性检查。
- **接入层**：邮箱 + 邀请码登录、刷新令牌、限流与配额、平台 Key / BYOK、多 provider 路由与回退。
- **OpenAI 兼容端点**：`/v1/chat/completions`（流式 + 非流式）与 `/v1/models`，可用官方 SDK 调用。

尚未完成：P5 隐私合规（加密、注销、政策、审计）、P6 部署与容器化，以及 P4 的 Web 人格向导、多角色、主动消息。**当前不是生产可用版本。**

## 架构

```text
客户端         Web / PWA（先做） · CLI · [APK / 微信桥 搁置]
                 │ HTTPS + WebSocket
接入层         认证 / 会话 / 限流 / 配额 / OpenAI 兼容端点
                 │
桥接层         profile2soul：文本人格 → SOUL.md / seeds / 记忆 / 驱力
                 │
引擎层         OpenHer：按需加载状态 → 跑一轮 → 持久化
                 │ + 主动消息调度器（P4）
数据层         会话状态 · 每用户记忆（SQLite FTS5 / 可选 EverOS） · 账号 / BYOK
```

部署形态是 **云端引擎 + 轻量 Web/PWA**：手机只跑浏览器客户端，Termux 仅作开发环境。上游 OpenHer 保持原样，本项目在外部加「接入层 + 桥接层 + 数据 / 账号层」，决策见 [ADR-0001](docs/adr/0001-deployment-cloud-web-first.md) 与 [ADR-0002](docs/adr/0002-shell-as-product-layer.md)。

## 快速开始

需要 Linux（容器 / VPS / Codespaces）与 Python 3.11+。Termux / Android 不支持直接安装依赖，原因见 [`docs/p1-04-env-setup.md`](docs/p1-04-env-setup.md)。

```bash
git clone git@github.com:jason081112/Pre_Alpha.git
cd Pre_Alpha

./setup.sh --fetch                 # 拉取 OpenHer，创建 venv，安装依赖
# 编辑 vendor/OpenHer/.env，填入至少一个 provider 的 key
source vendor/OpenHer/.venv/bin/activate

python3 -m shell.app               # 默认 0.0.0.0:8000
```

打开 `http://localhost:8000/`。首次使用需要账号：

```bash
python3 scripts/dev/make_invite.py          # 若开启邀请码
# 或直接注册：POST /api/auth/register {"email","password"}
```

开发期可设 `ALPHA_AUTH_DISABLED=1` 跳过登录（所有请求按 `local` 用户处理）。

### CLI

```bash
python3 -m cli.main persona list
python3 -m cli.main persona new --from scratch --id xiaolan --name 小岚
python3 -m cli.main persona preview xiaolan
python3 -m cli.main persona check xiaolan
python3 -m cli.main route set --provider ollama-cloud --model gemma4:31b
python3 -m cli.main profile2soul .data/profile --out .data/soul --write
```

### API

| 端点 | 说明 |
|---|---|
| `POST /api/auth/register` / `login` / `refresh` | 邮箱 + 密码 + 邀请码 |
| `GET /v1/models` | 可用人格 / 模型列表 |
| `POST /v1/chat/completions` | OpenAI 兼容，支持流式 |
| `WS /ws/chat/stream` | Web 聊天页使用的真流式端点 |
| `/v1/personas` | 人格 CRUD、版本、软删除、fork |
| `/api/chat/history/{persona}` | 会话历史 |
| `/api/export/chat/{persona}` | 导出 Markdown / JSON |

契约见 [`docs/openapi-personas.yaml`](docs/openapi-personas.yaml)、[`docs/p1-68-openai-compat.md`](docs/p1-68-openai-compat.md)、[`docs/p4-83-personas-api.md`](docs/p4-83-personas-api.md)。

## 开发进度

截至 **2026-11-11**：**65 / 98** 项完成（66%）。

| 阶段 | 内容 | 进度 |
|---|---|---|
| P0 | 调研验证 | 10 / 10 |
| P1 | 后端跑通 | 18 / 18 |
| P2 | 蒸馏流水线 | 10 / 10 |
| P3 | 桥接层 | 17 / 18（#90 条件触发，不排期） |
| P4 | 交互层 | 7 / 22（2 项进行中） |
| P5 | 隐私合规 | 0 / 12 |
| P6 | 工程基建 | 3 / 8 |

完整任务、状态与最近活动以 [Project Alpha](https://github.com/users/jason081112/projects/1) 为准；[项目主页](https://jason081112.github.io/Pre_Alpha/) 每 15 分钟同步一次。

## 仓库结构

```text
Pre_Alpha/
├── shell/           # 接入层 + Web 聊天页 + 流式端点 + 人格 API
├── bridge/          # profile2soul / soul2profile 等映射
├── cli/             # alpha 命令行
├── tests/           # 零依赖测试 + 合成 golden
├── docs/            # 架构、ADR、各阶段实现与验收记录
├── site/            # 项目主页（GitHub Pages，静态、无构建）
├── scripts/dev/     # 环境恢复、验证、实验脚本
└── vendor/          # 上游 OpenHer / Distilly（不入库，按脚本拉取）
```

`vendor/` 不随仓库分发；`scripts/dev/fetch_openher.sh`、`fetch_distilly.sh` 固定版本拉取，保证可复现。

## 隐私与边界

本项目是 **open core**，不是全开源：

- **公开**：协议、客户端、CLI、单用户自托管、本地蒸馏、加密与导出工具
- **私有**：精修蒸馏、评测资产、托管控制面、审核风控、计费、托管记忆
- 原始聊天记录与 `.env` 不入库；`refine` 请求出现 `raw_text` / `messages` / `chat_log` 等键直接 422
- 云端模式承诺：传输加密、静态加密、不训练、不分享、可导出可删除、首次启动明示
- 蒸馏**他人**聊天记录前请取得当事人同意

细节：[`docs/open-core.md`](docs/open-core.md) · [`docs/refine-api.md`](docs/refine-api.md) · [`SECURITY.md`](SECURITY.md) · [`LICENSING.md`](LICENSING.md)

## 文档

- [架构说明](docs/architecture.md)
- [决策记录](docs/adr/)
- [P2 字段映射](docs/p2-30-field-mapping.md) / [P3 桥接](docs/p3-42-cli-profile2soul.md)
- [P4 Web 聊天页](docs/p4-46-web.md) / [流式 WS](docs/p4-94-stream.md) / [多段推送](docs/p4-96-segments.md)
- [人格体检](docs/p4-86-persona-check.md) / [人格 API](docs/p4-83-personas-api.md)

## 致谢

- [OpenHer](https://github.com/kellyvv/OpenHer)（Apache-2.0）— 人格引擎
- [Distilly](https://github.com/titanwings/distilly)（MIT）— 聊天记录蒸馏流程与六层人格
- [EverOS](https://github.com/EverMind-AI/EverOS)（Apache-2.0）— 可选长期记忆后端

完整版权与许可证见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。本项目代码以 [Apache-2.0](LICENSE) 为主；自托管服务端入口为 AGPL-3.0-or-later，文档为 CC BY 4.0，详见 [`LICENSING.md`](LICENSING.md)。
