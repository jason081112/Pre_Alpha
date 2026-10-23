# Contributing

感谢愿意帮忙。这个项目是 **open core**：公开仓库只包含 core、客户端、CLI 和单用户自托管服务端；托管控制面与精修蒸馏在私有仓库。

## 提交前

- 不要提交原始聊天记录、真实用户数据、任何 Key。
- 不要提交精修 prompt、评测集、调参记录（这些不属于公开仓库）。
- 不要引入 NSFW、爬取他人聊天记录、骚扰/跟踪类功能。
- 跑一遍零依赖测试：

```bash
python3 tests/test_routing.py
python3 tests/test_custom_provider.py
python3 tests/test_smoke.py
python3 scripts/secret_scan.py
```

## DCO

本项目使用 Developer Certificate of Origin，不要求 CLA。每次提交加签名：

```bash
git commit -s -m "fix: ..."
```

提交信息里会出现：

```
Signed-off-by: Your Name <you@example.com>
```

## 许可

- 你提交到 core / 客户端 / CLI 的代码，按 Apache-2.0 授权。
- 你提交到 `shell/app.py`、`shell/api.py` 的代码，按 AGPL-3.0-or-later 授权。
- 文档按 CC BY 4.0。
- 角色资产按 CC BY-NC 4.0。

请只提交你有权提交的代码。

## PR 范围

欢迎：

- provider 适配、路由/回退改进
- 本地蒸馏框架、解析器、PII 清洗
- 客户端可访问性、性能、移动端适配
- 文档、测试、自托管体验

不适合公开仓库：

- 托管计费、多租户、审核策略
- 精修 prompt 与评测数据
- 任何需要保密才能成立的“质量层”

## 行为

尊重、不骚扰、不泄露他人隐私。违反者会被移出协作。
