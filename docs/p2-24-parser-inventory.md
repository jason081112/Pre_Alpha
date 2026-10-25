# P2-24 解析器盘点：WeChat / QQ / social / photo

对应看板 #24。目标：在写解析器之前，先确认现有能力、输入格式、缺失依赖和
合规边界，避免把“能读数据库”误当成“已有解析器”。

## 结论

1. Distilly 现有解析器只有飞书、钉钉、Slack、邮件；**没有 WeChat / QQ 解析器**。
2. Distilly README 写“微信聊天记录 ✅ SQLite”，但仓库里没有对应的 SQLite
   读取代码，只在关系 intake 里提示可以试 WeFlow。README 的能力声明不能当成
   已实现能力。
3. WeChatMsg 当前仓库只剩 README / doc，项目长期未更新；PyWxDump 已被作者
   删除代码，仓库只保留一份“因法律风险移除”的说明。两者都不能作为公开产品
   的运行时依赖。
4. 因此 P2 不做微信/QQ 数据库解密，也不捆绑第三方提取工具。只接收用户**已经
   合法导出**的 HTML / CSV / TXT / JSON / 图片，再转成内部 canonical schema。
5. 社交只做用户提供的导出或公开帖规范化；photo 走宿主视觉模型的 OCR 旁路，
   不引入本地 OCR/ASR 依赖。

## 现有能力

| 来源 | 当前实现 | 输入 | 依赖 | 状态 |
|---|---|---|---|---|
| 飞书消息 | `vendor/distilly/tools/feishu_parser.py` | JSON / TXT | 标准库 + `requests` | 有解析器 |
| 飞书自动采集 | `feishu_auto_collector.py` / `feishu_browser.py` / `feishu_mcp_client.py` | API / 浏览器 / MCP | `requests`、可选 Playwright | 有采集器 |
| 钉钉 | `dingtalk_auto_collector.py` | API + 浏览器 | 可选 Playwright | 有采集器 |
| Slack | `slack_auto_collector.py` | API | `slack-sdk` | 有采集器 |
| 邮件 | `email_parser.py` | `.eml` / `.mbox` / TXT | 标准库 | 有解析器 |
| PDF / 图片 / 截图 | 宿主原生 `Read` | PDF / PNG / JPEG | 宿主视觉能力 | 无本地解析器 |
| Markdown / 粘贴文本 | 宿主 `Read` / 对话 | 文本 | 无 | 直接使用 |
| 微信 | README 声称 SQLite | 外部导出 | **无代码、无固定依赖** | 未实现 |
| QQ | 未声明 | 外部导出 | **无** | 未实现 |
| 社交导出 | `owner_sources.py` 只做发现；`xquik_public_posts.py` 做公开 X 研究 | ZIP / API | 可选第三方 Xquik | 无通用解析器 |
| 照片 / 截图 | 宿主视觉模型；无 OCR 管线 | 图片 | 宿主 | 只到“可读”级 |

`tools/owner_sources.py` 能识别 `twitter-*.zip`、`instagram-*.zip`、
`facebook-*.zip` 等文件名，但这只是“发现可导入的档案”，不是解析内容。

## 微信：能力声明与真实代码不一致

### Distilly 侧

- README 的 Supported Data Sources 表写：
  `WeChat chat history | ✅ SQLite | Export first with WeChatMsg or PyWxDump`。
- 但 `tools/` 下没有 `wechat_parser.py`、`wechat_collector.py` 或 SQLite 读取器；
  `rg "sqlite|MSG.db|PyWxDump|WeChatMsg"` 没有命中实现。
- `prompts/relationship/intake.md` 只写“可以试 WeFlow”，没有定义导出格式。
- 结论：这是**文档承诺**，不是可复用代码。P2 不能直接依赖。

### WeChatMsg（留痕）

- 仓库：<https://github.com/LC044/WeChatMsg>
- 当前仓库以 README / doc 为主，README 明确说项目很久没有更新。
- 公开说明中常见导出格式包括 HTML、Word、CSV、TXT、SQLite；不同版本的
  字段和目录可能变化。
- 现实用法是用户在 Windows 上自行运行外部程序，再把结果交给我们的解析器。
- 不把 WeChatMsg 代码或二进制放进 Pre_Alpha；不把它写进依赖。

### PyWxDump

- 仓库：<https://github.com/xaoyaoo/PyWxDump>
- 当前仓库已被作者替换为“项目移除通知”：作者称收到微信方面律师函，已移除
  全部代码与提交历史，并建议停止使用旧版本。
- 历史版本常见能力是解密本地数据库、导出 CSV / HTML / JSON；具体表结构随
  微信版本变化，不能作为稳定公开契约。
- 结论：**不作为依赖，不要求用户安装，不在文档里提供绕过加密的步骤。**

### 微信输入的产品边界

推荐支持顺序：

1. **TXT / CSV**：用户从任意合法工具导出后直接给文件；最容易做稳定解析。
2. **HTML**：微信导出/网页归档常见；解析 DOM 或纯文本，保留时间和发送者。
3. **JSON**：如果用户已有结构化导出，优先作为 canonical 输入。
4. **SQLite**：暂不作为 P2 目标。若未来支持，必须由用户提供已经解密的数据库，
   并且按版本做 adapter；不在服务端做解密、密钥提取或数据库注入。

不做：

- 不实现微信数据库解密 / 内存取密钥 / 注入；
- 不捆绑 WeChatMsg、PyWxDump 或类似工具；
- 不把“原始数据库”上传到云端；
- 不在日志里输出聊天原文或账号标识。

## QQ：官方格式与第三方格式

### 官方消息管理器

PC QQ 常见导出：

- `.bak`：加密备份，可重新导入 QQ，但不适合直接解析；
- `.mht`：网页归档，含文字和部分图片，不能重新导入；
- `.txt`：纯文本，不含图片，适合作为最低成本输入。

### NTQQ 数据库

QQ NT 的本地数据库常见文件名包括 `nt_msg.db`、`group_info.db`、
`profile_info.db`；消息表字段包含时间、发送者、消息内容等，消息内容可能是
protobuf。不同版本字段会变化，直接读库的维护成本高。

### 第三方导出器

- `shuakami/qq-chat-exporter`：GPL-3.0，支持 TXT / JSON；**不直接 vendor**
  到 Apache-2.0 仓库。
- `zywstudy123/qq-bak-chat-history-export`：MIT，支持 TXT / JSON / CSV；
  可以作为用户侧工具参考，但 P2 不把它变成运行时依赖。

### P2 决策

- 只支持用户已导出的 TXT / CSV / JSON（以及后续 HTML/MHT）。
- 不实现 `.bak` 解密，不直接读 NTQQ 数据库。
- 若以后接数据库，必须先有独立的、按版本隔离的 adapter，并且只处理用户
  明确提供的本地文件。

## Social：X / Meta / 公开帖

### X / Twitter archive

典型 ZIP 内结构：

```text
data/
├── tweets.js
├── direct-messages.js
├── follower.js
└── ...
```

`tweets.js` 不是严格 JSON，开头是：

```js
window.YTD.tweets.part0 = [ ... ]
```

解析时需要先去掉 `window.YTD... =` 前缀。`direct-messages.js` 同理。
媒体在 `tweet_media/` 等目录。X 官方归档可能包含公开帖、回复、DM、媒体。

### Meta / Instagram / Facebook

`owner_sources.py` 能识别 `instagram-*.zip` / `facebook-*.zip`。Meta 导出通常是
ZIP + JSON/HTML，字段和目录随产品变化；P2 不承诺通用解析。

### Distilly 现状

- `xquik_public_posts.py` 只用于 celebrity 研究的公开 X 候选，且依赖按量计费的
  第三方服务；不是通用社交解析器。
- `owner_sources.py` 只做来源发现，不解析帖子内容。
- P2 的社交输入先收 canonical JSON；平台 adapter 后续再做。

## Photo / 截图

- Distilly 的 sources 表把 PDF / 图片 / 截图列为“手动上传”，由宿主 `Read`
  原生读取；没有 OCR、版面分析或聊天气泡切分代码。
- 关系 intake 接受聊天截图，但后续分析依赖宿主视觉模型能否读出文字。
- P2 的 fixture 用 `photo_ocr.json` 表示 OCR 旁路：图片 → OCR blocks →
  canonical messages；不在本机安装 OCR 依赖。
- 真实接入时要保留降级：视觉模型不可用时，要求用户粘贴文本或上传 CSV/TXT。

## 依赖现状

Distilly 核心：

```text
requests>=2.28.0
```

可选：

```text
pypinyin
playwright
slack-sdk
python-docx
openpyxl
keyring
```

微信 / QQ 没有出现在依赖里，也没有 SQLCipher / 数据库驱动。这个状态应保持：
**核心解析器不引入平台专用解密依赖。**

## P2 解析器设计

建议新增一个薄的 adapter 层，统一输出 #25 的 canonical schema：

```text
raw export (TXT / CSV / HTML / JSON / OCR)
        │
        ▼
source adapter (wechat / qq / social / photo)
        │
        ▼
canonical messages
  {id, timestamp, sender_id, type, text, reply_to, attachments}
        │
        ▼
Distilly intake / analyze / build
```

第一阶段（P2）：

- canonical JSON：直接读取，作为 golden 输入；
- TXT：按“时间 + 发送者 + 文本”解析，无法识别的行保留原文并标记；
- CSV：固定列 `timestamp,sender,type,text`，缺失列报错；
- social：规范化帖子数组；
- photo：OCR 旁路 JSON。

第二阶段（P2 之后）：

- 微信 HTML / 导出 JSON adapter；
- QQ MHT / 导出 JSON adapter；
- X `tweets.js` / `direct-messages.js` adapter；
- Meta 导出 adapter；
- 可选、按版本隔离的 SQLite adapter（仅用户已解密文件）。

## 隐私与合规

- 原始聊天记录只在用户本地处理；不把 raw export 上传到云端。
- canonical 化之前先做 PII 清洗：姓名、手机号、身份证、地址、邮箱、链接、
  账号 ID、群名、订单号。
- 生成物仍可能包含原话引用，因此 `skills/`、`knowledge/`、`tests/fixtures/raw/`
  继续被 gitignore。
- 不提供微信/QQ 数据库解密、密钥提取或反检测步骤。
- 公开仓库只放合成样本；真实样本永不提交。

## #24 验收对照

| 要求 | 结果 |
|---|---|
| 盘点 WeChat 解析器 | 无实现；README 声明与代码不一致 |
| 盘点 QQ 解析器 | 无实现；记录官方/第三方格式 |
| 盘点 social | 只有来源发现和公开 X 研究，无通用解析器 |
| 盘点 photo | 宿主视觉读取；无 OCR/ASR 管线 |
| 缺失依赖 | WeChatMsg / PyWxDump 不可依赖，记录法律与维护风险 |
| 导出格式 | TXT / CSV / HTML / JSON / MHT / SQLite 的取舍已记录 |
| 下一步 | #25 合成 canonical + 格式样本；#26/#27 先跑样本 |

## 证据链接

- Distilly 数据源表：`vendor/distilly/README.md`
- Distilly 来源发现：`vendor/distilly/tools/owner_sources.py`
- Distilly 来源流程：`vendor/distilly/prompts/flows/sources.md`
- WeChatMsg：<https://github.com/LC044/WeChatMsg>
- PyWxDump 移除说明：<https://github.com/xaoyaoo/PyWxDump>
- QQ exporter（GPL-3.0）：<https://github.com/shuakami/qq-chat-exporter>
- QQ bak exporter（MIT）：<https://github.com/zywstudy123/qq-bak-chat-history-export>
- X archive 说明：<https://help.x.com/en/managing-your-account/how-to-download-your-x-archive>
