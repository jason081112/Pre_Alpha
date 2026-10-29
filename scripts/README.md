# scripts

开发与部署辅助脚本。

## 计划

| 脚本 | 用途 | 阶段 |
|---|---|---|
| `setup.sh` | 安装依赖、初始化环境 | P1（#14） |
| `dev/restore_env.sh` | 容器根文件系统被重置后恢复 gh / 凭据 / git 身份 | 运维 |
| `dev/fetch_distilly.sh` | 拉取钉住的 Distilly fork 到 `vendor/distilly` | P2（#21） |
| `dev/fetch_openher.sh` | 拉取钉住的 OpenHer 到 `vendor/OpenHer`（默认跳过模态素材） | P2（#30） |
| `dev/map_persona_to_soul.py` | Distilly `persona.md` → OpenHer `SOUL.md` | P2（#30） |
| `dev/verify_persona_e2e.py` | 跑 OpenHer `/api/chat`，检查风格与结构标记泄漏 | P2（#30） |
| `dev/verify_limits_http.py` | 限流/配额真实 HTTP 冒烟 | P1（#67） |
| `bootstrap` | 云端/容器一键拉起 | P6（#78） |

约定：脚本需幂等，可重复执行；涉及密钥时只读 `.env` 或既有凭据备份，不写入明文。
`dev/restore_env.sh` 读取的凭据备份位置：`--backup` 参数 → `/sandbox/workspace/.gh_backup/hosts.yml`
→ `$HOME/.gh_backup/hosts.yml`，按序取第一个存在的。
