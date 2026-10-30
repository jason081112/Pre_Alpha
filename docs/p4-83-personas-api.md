# P4-83 人格创建/编辑 API

对应看板 #83。产出 `shell/persona_repo.py`（核心，零依赖）、`shell/personas_api.py`（HTTP 层）、
`docs/openapi-personas.yaml`（契约）、`tests/test_personas_api.py`（18 项）。

## 分层

| 层 | 文件 | 职责 | 依赖 |
|---|---|---|---|
| 核心 | `shell/persona_repo.py` | 校验、版本、软删除、原子写、镜像 | 纯 Python |
| HTTP | `shell/personas_api.py` | If-Match、状态码、身份头、审计 | FastAPI |
| 契约 | `docs/openapi-personas.yaml` | 端点与错误码 | — |

核心层不依赖 FastAPI，因此能在零依赖环境单测；HTTP 层只做语义映射。

## 端点

| 方法 | 路径 | 语义 |
|---|---|---|
| GET | `/v1/personas` | 列表（`include_deleted` 可选） |
| POST | `/v1/personas` | 创建，201 |
| GET | `/v1/personas/{id}` | 详情（`profile` + `state`） |
| PUT | `/v1/personas/{id}` | 全量替换，**必须** `If-Match` |
| PATCH | `/v1/personas/{id}` | JSON merge patch，**必须** `If-Match` |
| DELETE | `/v1/personas/{id}` | 软删除 |
| POST | `/v1/personas/{id}/restore` | 恢复 |
| POST | `/v1/personas/{id}/fork` | 复制为可编辑副本 |

## 错误码（固定）

| 状态 | `error` | 触发 |
|---|---|---|
| 400 | `bad_request` | 缺 If-Match 或不是整数 |
| 403 | `readonly` | 内置人格只读（#84 启用） |
| 404 | `not_found` | 不存在，**或越权（统一 404，不泄漏存在性）** |
| 409 | `version_conflict` | 版本不匹配 / 重名 |
| 422 | `invalid_profile` | `validate_profile()` 有错 |
| 422 | `raw_text_not_allowed` | 任意层级出现原文键 |
| 429 | `quota_personas` | 超过人格数量上限 |

## 写入链路

```
请求体 → 递归查禁键 → profile_from_dict → validate_profile
      → 原子写 profile.json / state.json / versions/vNNNN.json
      → 渲染 SOUL.md（失败不阻断，记 state.render_error）
      → 镜像到引擎 personas 目录
      → on_change(user, id) → PersonaLoader.reload(id)
```

`profile.json` 是真相来源，`SOUL.md` 是派生物。渲染失败只记警告，不丢用户数据。

## 版本与快照

- `state.version` 单调递增；PUT/PATCH 必须带 `If-Match: <version>`
- 每次写入落 `versions/vNNNN.json`，默认保留最近 10 个（`keep_versions`）
- 冲突返回当前版本号，便于客户端重取后再改

## 软删除

- `DELETE` 只写 `state.deleted_at`，并从引擎镜像目录移除
- 列表默认过滤已删除，`include_deleted=true` 可见
- 物理清理（30 天）属 #84

## 与 #84 的边界

本 issue 的实现是**可用雏形**：

- 存储已按 `<user>/personas/<id>/` 分目录，但**配额、加密、回收站定时清理未做**
- 内置人格 fork 目前只支持"复制本人已有人格"；跨用户/内置库的复制待 #84 定布局
- `mirror_dir` 是"让引擎读到"的最小实现，真正的按用户人格目录由 #84 接管

## 验证

```bash
python3 tests/test_personas_api.py   # 18 passed（核心 15 + HTTP 3）
python3 -m pytest tests/ -q          # 见提交说明
```

覆盖：创建/重复冲突、非法 slug、深层原文键、乐观锁、PATCH 合并、改坏后校验失败、
软删除与恢复、配额、跨用户隔离（404）、fork、快照裁剪、`on_change` 回调、镜像写入与移除、
HTTP 201/400/409/422/404 全路径、审计事件。

HTTP 部分在无 FastAPI 环境自动跳过（打印 SKIP），保证零依赖 CI 仍可跑。
