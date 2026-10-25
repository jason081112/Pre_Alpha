# 脱敏聊天记录样本

本目录是 P2 #25 的合成样本，用于：

- 验证未来的 WeChat / QQ / social / photo 解析适配器；
- 跑通 Distilly 的 `intake → analyze → build`；
- 为 P3 的 `profile2soul` golden 快照提供稳定输入。

**所有内容都是手工合成的。** 没有真实姓名、账号、手机号、地址、链接或聊天记录。
名字使用“小岚 / 小舟 / 林舟 / 阿澈”等演示名，账号统一带 `demo` 前缀。

## 文件

| 文件 | 用途 |
|---|---|
| `relationship_zh.canonical.json` | 关系蒸馏主样本：表达 DNA、冲突修复、照顾模式、记忆锚点 |
| `colleague_zh.canonical.json` | 同事蒸馏样本：责任范围、API 标准、CR 关注点、工作流 |
| `wechat_like.txt` | 微信导出文本的近似格式，测试按行解析 |
| `wechat_like.csv` | 微信导出 CSV 的规范化列，测试表格解析 |
| `qq_like.txt` | QQ 消息管理器文本的近似格式，测试另一个聊天来源 |
| `social_posts.json` | 公开帖/社交导出的规范化样本，测试表达风格提取 |
| `photo_ocr.json` | 聊天截图 OCR 旁路数据，测试 photo 输入，不包含真实图片 |

文本样本以 `# SYNTHETIC FIXTURE` 开头；CSV 通过本 README 和 canonical
样本共同标记为合成数据。JSON 样本都有 `"synthetic": true`。

## Canonical schema v1

`*.canonical.json` 是内部标准输入，不绑定任何平台原始格式：

```json
{
  "schema_version": "1.0",
  "fixture_id": "relationship-zh-001",
  "synthetic": true,
  "language": "zh-CN",
  "source": "synthetic",
  "participants": [
    {"id": "user_demo", "display_name": "小舟", "role": "owner", "aliases": []},
    {"id": "persona_demo", "display_name": "小岚", "role": "target", "aliases": []}
  ],
  "conversation": {
    "id": "conv_demo_relationship_001",
    "platform": "wechat-like",
    "title": "小岚",
    "timezone": "Asia/Shanghai"
  },
  "messages": [
    {
      "id": "m001",
      "timestamp": "2026-01-05T20:12:00+08:00",
      "sender_id": "persona_demo",
      "type": "text",
      "text": "你今天怎么这么晚才回？",
      "reply_to": null,
      "attachments": []
    }
  ]
}
```

字段约定：

| 字段 | 说明 |
|---|---|
| `schema_version` | 当前为 `1.0` |
| `fixture_id` | 稳定、唯一、可读的样本 ID |
| `synthetic` | 必须为 `true`；真实数据不得提交 |
| `language` | BCP 47，例如 `zh-CN` / `en` |
| `source` | `synthetic` 或未来受控的来源标签 |
| `participants[].role` | `owner` / `target` / `observer` |
| `conversation.platform` | `wechat-like` / `qq-like` / `social` / `photo` |
| `messages[].type` | `text` / `voice` / `image` / `sticker` / `system` / `link` |
| `messages[].timestamp` | ISO 8601，带时区 |
| `messages[].reply_to` | 被回复消息的 `id`，没有则为 `null` |
| `messages[].attachments` | 附件元数据；不要放真实文件内容 |

## 安全规则

1. 真实聊天记录永不提交；原始导出放 `tests/fixtures/raw/`，该目录已被
   `.gitignore` 屏蔽。
2. 新增样本必须使用合成数据，或完成不可逆脱敏：
   - 姓名、昵称、群名全部替换；
   - 手机号、邮箱、身份证、地址、订单号、链接全部删除或替换；
   - 账号 ID 使用 `demo_*`；
   - 保留语言风格和互动结构，不保留可识别身份。
3. 不把真实图片、语音、二维码、头像放进 fixture；photo 样本只放 OCR 旁路。
4. 样本进入仓库前必须通过 `tests/test_fixtures.py` 的格式与 PII 初筛。

## 与解析器的关系

- `wechat_like.*` / `qq_like.txt` 只是**近似格式**，不声称等同于某个第三方
  工具的全部导出变体。真正接入时在 `shell/` 或 `distill-core` 增加独立
  adapter，把原始格式转换为 canonical schema。
- `social_posts.json` 对应公开帖/导出的规范化层；不在 P2 做平台抓取。
- `photo_ocr.json` 对应“宿主视觉模型读图 → OCR 旁路 → canonical messages”的
  照片路径；不引入本地 OCR 依赖。

## 相关文档

- `docs/p2-24-parser-inventory.md`
- `docs/p2-22-distilly-flow.md`
- `docs/open-core.md`
