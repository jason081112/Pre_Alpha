# P4-50 · 会话历史落盘与导出

> 验收：`scripts/dev/verify_50_export.py`（12/12）。单测：`tests/test_export.py`（10/10）。

## 1. 落盘：本来就有，补齐一条路径

`engine/chat_log_store.py`（上游）把消息写进独立 SQLite（`chat.db`），与引擎状态完全分开：

```sql
CREATE TABLE chat_messages (
  id, client_id, persona_id, role, content, modality, image_url, created_at
);
```

三条写入路径都通：

| 路径 | 位置 |
|---|---|
| `POST /api/chat` | `main.py:806` |
| `WS /ws/chat`（上游） | `main.py:1238/1255` |
| `WS /ws/chat/stream`（#94，我们加的） | `shell/ws_stream.py` |

`client_id` 是前端 localStorage 的 UUID；外壳的 `ScopedChatLog` 会把它映射成
`<用户前缀>:<client_id>`，所以 A 用户读不到 B 的消息。

## 2. 导出：新增

```text
GET /api/export/chat/{persona_id}?client_id=...&format=md|json
GET /api/export/chat?persona_id=...&client_id=...&format=md|json   （等价）
```

响应头：`Content-Disposition: attachment; filename="alpha-luna-20261107.md"`，
外加 `X-Alpha-Messages: 128`（方便前端提示条数）。

### Markdown 样例

```markdown
# Luna · 对话记录

- 人格：`luna`
- 消息数：2
- 导出时间：2026-11-07 23:12

---

## 2026-11-07

**我** · 23:10

> 今天加班到现在

**她** · 23:10

> 我在。
> 快去把鞋换了。
```

几个决定：

- **按天分组**：长对话不分组会糊成一片
- **引用块**：多行内容保持在一个块里，不会和下一个说话人混淆
- **模态标注**：非文字消息写 `**她**（语音）`
- **空历史也返回合法文档**：`_（这段对话还没有任何消息）_`，而不是空串

### JSON 结构

```json
{
  "schema": "alpha.export.chat/v1",
  "exported_at": 1800000000,
  "exported_at_iso": "2026-11-07T23:12:00+00:00",
  "persona_id": "luna",
  "persona_name": "luna",
  "count": 2,
  "messages": [
    {"id": 1, "role": "user", "content": "…", "modality": "文字",
     "image_url": null, "created_at": 1800000000, "created_at_iso": "…"}
  ]
}
```

`schema` 字段是给以后的迁移留的：格式要变时能判断版本。

## 3. 前端入口

顶栏加「导出」按钮，点一下下载当前人格的 Markdown。
文件名从 `Content-Disposition` 里读，取不到就退回 `alpha-<persona>.md`。

## 4. 隔离怎么验的

不是"两个用户各自导出看到不同内容"这种弱断言，而是**B 用户拿 A 的 client_id 去导**：

```
[PASS] 9 隔离：B 拿 A 的 client_id 导出为空 — status=200 count=0
```

因为 `ScopedChatLog._client()` 会加用户前缀，同一个 client_id 落到不同的命名空间里。
这条如果挂了，说明隔离层有问题，比导出功能本身严重。

## 5. 验收

```text
[PASS]  1 未认证 → 401
[PASS]  2 用户 A 发出一条消息
[PASS]  3 A 导出 Markdown 200
[PASS]  4 带 Content-Disposition
[PASS]  5 md 含对话内容
[PASS]  6 消息计数头 x-alpha-messages=2
[PASS]  7 JSON schema = alpha.export.chat/v1
[PASS]  8 JSON 条数 count=2
[PASS]  9 隔离：B 拿 A 的 client_id 导出为空
[PASS] 10 空历史返回合法文档
[PASS] 11 非法 format → 422
[PASS] 12 缺 persona_id → 400
```

回归：`verify_46_ui.py` 23/23、`tests/ui_debug_smoke.mjs` 10/10。

## 6. 没做

- **人格定义 / 记忆 / 审计的导出** → #74（P5 数据导出）
- **分页**：一次最多 20000 条，超过会截断（没有做游标）
- **删除单条 / 清空** → #53 `alpha purge`
- **导出 zip**：现在是单文件，多个人格要分别点
