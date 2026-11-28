# 容器化部署（#78）

生产形态：`app`（Python + vendor/OpenHer + shell）+ `caddy`（自动 HTTPS）+ 数据卷。

## 一条命令

```bash
cp .env.example .env      # 填真实密钥
chmod 600 .env
./scripts/dev/docker_deploy.sh
```

脚本做四件事：构建 `alpha:<git sha>` → `docker compose up -d` → 等 `/healthz` → 跑 smoke（`/readyz` 必须通过）。

首次构建会装 `vendor/OpenHer/requirements.txt`（chromadb 等），国内建议：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple ./scripts/dev/docker_deploy.sh
```

## 健康检查

| 端点 | 含义 | 谁用 |
|---|---|---|
| `/healthz` | 进程活着（不碰磁盘/网络） | 容器 HEALTHCHECK、负载均衡 |
| `/readyz` | 依赖可用：6 个 SQLite 可读、数据目录可写、引擎目录可写、默认路由可达（TCP，缓存 15s） | 部署脚本、监控 |

`/readyz` 失败返回 503，并逐项给出 `checks`。这样能区分“进程活着但数据库没挂上”和“进程挂了”。

## 数据卷

| 宿主机 | 容器 | 内容 |
|---|---|---|
| `ALPHA_DATA_DIR`（默认 `/var/lib/prealpha`） | `/var/lib/prealpha` | auth / limits / routing / billing / moderation / account `.db` |
| `ALPHA_ENGINE_DIR`（默认 `/opt/prealpha/vendor/OpenHer/.data`） | `/app/vendor/OpenHer/.data` | chat.db / openher.db / memory.db / genome |
| `ALPHA_BACKUP_DIR` | `/opt/prealpha/backups` | 备份（注销时按文件名清理） |
| `ALPHA_CADDY_DATA`（默认 `/var/lib/caddy/.local/share/caddy`） | `/data` | Let's Encrypt 证书（复用宿主机 Caddy 目录） |

数据库表结构在启动时幂等创建（`CREATE TABLE IF NOT EXISTS`），没有独立迁移步骤；部署脚本启动后由 `/readyz` 确认可读。

## 回滚

```bash
./scripts/dev/docker_rollback.sh           # 回 .deploy/previous_tag
./scripts/dev/docker_rollback.sh <tag>     # 回指定版本
```

回滚脚本会把旧 tag 重新 `compose up -d --no-build` 并等 `/healthz`。数据兼容性：

- 表结构只增不改，旧镜像读新库通常没问题；
- 若新版本做过破坏性变更（删列/改语义），先停容器、恢复 `/var/lib/prealpha` 与引擎 `.data` 备份，再回滚。

## 旁路验证（不抢 80/443）

新机器或首次切换时，先在 8080 验证，再切正式端口：

```bash
docker compose up -d app          # 只起 app
docker run --rm --network host curlimages/curl -fsS http://127.0.0.1:8000/healthz
```

或临时打开 `docker-compose.yml` 里 `ports: 127.0.0.1:8080:8000`。

## 从 venv 部署切换过来

```bash
systemctl stop alpha.service caddy     # 释放 127.0.0.1:8000 与 80/443
./scripts/dev/docker_deploy.sh
```

现有数据目录不变（compose 挂的是同一批路径），所以切换不需要搬数据。想切回去：

```bash
docker compose down
systemctl start alpha.service caddy
```

## 环境变量

全部见 `.env.example`。`.env` 不进镜像（`.dockerignore` 已排除）。

## 已知限制

- 镜像内含整个仓库（`site/` 静态页也在内），改文案要重新构建；数据与证书走卷不受影响。
- `caddy` 用 `sslip.io` 域名走 HTTP-01 签证书，需要 80 端口可达；compose 挂宿主机的 `/var/lib/caddy/.local/share/caddy`，切过来不会重新签发。
- 多实例横向扩展未做（SQLite 单写者）。
