# Licensing

Alpha is **open core**, not fully open source.

| Path | License |
|---|---|
| Everything not listed below | **Apache-2.0** |
| `shell/app.py`, `shell/api.py`（自托管服务端入口） | **AGPL-3.0-or-later** |
| `docs/**` | **CC BY 4.0** |
| `assets/**`, `persona/**`（角色 / 音色 / 立绘 / 主题） | **CC BY-NC 4.0** |
| `vendor/**`（不随仓库分发） | 上游原许可（Apache-2.0） |
| `alpha-cloud`（私有仓库） | Proprietary |

全文见 `LICENSES/`。

## 为什么这么切

- **core / 客户端 / CLI 用 Apache**：最大采用；私有云可以安全依赖，不会被 copyleft 传染；客户端也能上 App Store。
- **自托管入口用 AGPL**：防止有人拿去做竞品托管却不回馈社区。
- **角色资产用 CC BY-NC**：代码许可不覆盖立绘、音色、人设文案，这些不允许商用搬运。
- **私有云 Proprietary**：控制面、精修蒸馏、审核、计费不在本仓库。

## 边界

- 本仓库**不包含**精修 prompt、评测集、调参记录、托管控制面。
- 本仓库**不接收**原始聊天记录；`refine` 契约只接受结构化人格 JSON。
- 详细边界见 `docs/open-core.md`。

## 贡献

采用 **DCO**，不要求 CLA。提交时使用 `git commit -s`。
详见 `CONTRIBUTING.md`。

## 商标

代码许可不覆盖名称与 logo，见 `TRADEMARK.md`。
