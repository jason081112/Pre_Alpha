# P2-21 Distilly fork 与 vendor 基线

对应看板 #21。目标：把 Distilly 的 `dot-skill` 分支固定成可复现的本地基线，
供 P2 后续任务读取、跑通和对比。

## 结论

- GitHub fork：<https://github.com/jason081112/distilly>
- 上游：<https://github.com/titanwings/distilly>
- 分支：`dot-skill`
- 固定 commit：`0afc45c29c360ba7bc8260f1bb0bf34e9155f4fa`
- commit 时间：`2026-10-13T05:42:45Z`
- 许可证：MIT，`LICENSE` 原样保留
- 本地路径：`vendor/distilly`
- 文件数：135（不含 `.git`）
- 目录体积：约 2.2 MB（不含 `.git`）

fork 的 `dot-skill` 与上游同一 commit；本地 checkout 也固定在该 commit，
工作区干净。没有把 token、聊天记录或生成产物带进仓库。

## 为什么不提交 vendor/

本仓库的 `.gitignore` 明确忽略 `vendor/`，和现有的 `vendor/OpenHer` 一致：

```gitignore
# 上游代码（fork 后维护，不提交本仓库）
vendor/
```

因此 `vendor/distilly` 是本地开发依赖，不是 Pre_Alpha 的源码。这样做有三个原因：

- 保持公开仓库体积可控；
- 避免把上游更新混进业务 commit；
- 让上游许可证、版权和变更历史留在其自己的仓库里。

为了不丢失“到底用的哪一版”，新增固定 SHA 的拉取脚本：

```bash
scripts/dev/fetch_distilly.sh
```

本机 GitHub 直连不稳定时可以用镜像前缀（只拉公开源码，不带 token）：

```bash
ALPHA_GIT_MIRROR=https://v4.gh-proxy.org scripts/dev/fetch_distilly.sh
```

脚本会：

1. clone `jason081112/distilly` 的 `dot-skill` 分支；
2. fetch 并 detach 到固定 SHA；
3. 校验 `HEAD` 必须等于固定 SHA；
4. 校验 `LICENSE`、`SKILL.md` 和 `package.json`；
5. 把 `titanwings/distilly` 配成 `upstream` remote。

## 许可证处理

- Distilly 是 MIT；本仓库是 Apache-2.0。
- `vendor/distilly/LICENSE` 不做修改。
- `THIRD_PARTY_NOTICES.md` 已登记 Distilly 的仓库、许可证和角色。
- 生成的人格 Skill 默认写入 `./skills/...`，该目录也被 Distilly 自己忽略；
  不把生成产物提交进 Pre_Alpha。

## 验证记录

| 检查 | 结果 |
|---|---|
| fork 存在 | `jason081112/distilly` |
| fork 分支与上游一致 | `0afc45c29c360ba7bc8260f1bb0bf34e9155f4fa` |
| 本地 `HEAD` | 同 SHA |
| 工作区 | clean |
| `LICENSE` | MIT |
| `SKILL.md` / `package.json` | 存在 |
| `skill_writer.py --help` | 可执行，参数正常 |
| 核心依赖 | `requirements.txt` 仅 `requests>=2.28.0` |

## 后续

- #22：读 `SKILL.md` + `prompts/flows/create.md`，整理 `intake → analyze → build` 时序。
- #23：验证 Skill 安装与触发；涉及宿主运行环境，优先级放在样本和流程理解之后。

## 相关文件

- `scripts/dev/fetch_distilly.sh`
- `THIRD_PARTY_NOTICES.md`
- `docs/p2-22-distilly-flow.md`
