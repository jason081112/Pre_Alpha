# P4-96 · 流式多段逐条推送

> 验收：`scripts/dev/verify_96_segments.py`（10/10）。单测：`tests/test_segmenter.py`（10/10）。

## 1. 为什么改

#94 为了「单条消息不空等」，把多段合并成一次 `chat_end`（带 `segments` 数组）。
但这丢了上游原本的**段间停顿**：

```python
# vendor/OpenHer/main.py 原逻辑
for i, seg in enumerate(segments):
    if i > 0:
        await ws.send_json({"type": "chat_start"})      # 重开打字指示
        await asyncio.sleep(max(delay, 300) / 1000.0)    # 最少 300ms
    await ws.send_json({"type": "chat_end", "reply": seg, ...})
```

三条气泡间隔 300ms，观感是人在连发微信。合并成一次给，变成一大段文字——**「灵」的来源没了**。

## 2. 实现

`shell/stream_parse.py` 新增 `Segmenter`：按空行切段，切出一段就推一段。

```text
【最终回复】我在。
                ← 空行 = 段边界
快去把鞋换了。
                ← 空行
先去洗个热水澡。
```

三个边界：

1. **分段符跨块**：`"\n"` + `"\n"` 两个 chunk —— 尾部保留可能是分隔符前缀的 1–2 个字符
2. **空段丢弃**：连续空行不产生空气泡，`segments` 里不会出现空串
3. **index 每段归零**：前端按 `segment` 分桶，用 `index` 排序

发送时序（`shell/ws_stream.py`）：

```text
chat_start                       ← prompt 就绪
chunk{segment:0,index:0}
chunk{segment:0,index:1}
segment_end{segment:0,reply:"我在。",pause_ms:300}
        ↓ sleep 300ms
chat_start                       ← 重开打字指示（惰性：只在下一段真有内容时才发）
chunk{segment:1,index:0}
...
chat_end{segments:[...], engine_segments:[...], chunks:7}
```

`chat_start` 是**惰性**的：`segment_end` 之后设 `pending_restart=True`，真等到下一个 chunk 才发。
否则最后一段结束后会多出一个空的「正在输入」。

## 3. 报文变化

新增字段，旧的都没动（向后兼容）：

| 字段 | 位置 | 说明 |
|---|---|---|
| `segment` | `chunk` | 第几段，从 0 开始 |
| `index` | `chunk` | **段内**序号，每段从 0 开始 |
| `reply` / `pause_ms` / `segment` | `segment_end`（新消息类型） | 该段全文与段后停顿 |
| `segments` | `chat_end` | **实际推送出去的段**（保证与 chunk 一致） |
| `engine_segments` | `chat_end` | 引擎自己算的段（可能是 `null`） |

### 为什么 `segments` 不是引擎的

引擎的 `segments` 来自 `split_messages` 技能，是整段生成完才算出来的，可能和按空行切的结果不同。
前端已经按我们推的段渲染了，如果 `chat_end` 再给一份不一样的，校验必然失败。

所以：

- `segments` = 实际推送的（与 chunk 一一对应）
- `engine_segments` = 引擎的（供参考，不一致时前端不该改绑定）

实测 mock 场景下 `engine_segments=None`（mock 不跑 split_messages 技能），`segments` 正常。

## 4. 段间停顿可调

```bash
ALPHA_SEGMENT_PAUSE_MS=300   # 默认，与上游一致
```

上限截到 2000，避免断太久像卡住。

上游还有 `delays_ms`（按段长计算），但那是**生成完才有**的数据，流式阶段拿不到。
所以这里用固定值；`chat_end` 之后前端若想更精细，可参考 `engine_segments` 与段长自己再调。

## 5. 验收

mock 分片间隔 500ms，三段回复：

```text
[PASS] 1 三段 → 3 个 segment_end          n=3
[PASS] 2 segment 递增 0/1/2               [0,1,2]
[PASS] 3a 段间停顿 >= 250ms               gaps=[501, 306]
[PASS] 4 index 每段从 0 连续               {0:[0], 1:[0], 2:[0]}
[PASS] 5 chat_end.segments 与 chunk 一致   ['我在。','快去把鞋换了。','先去洗个热水澡。']
[PASS] 6 chat_end 附引擎 segments 字段      engine_segments=None
[PASS] 7 段间 chat_start 重开打字指示       chat_start=3
[PASS] 8 单段只 1 个 segment_end           n=1
[PASS] 9 单段 segments 长度为 1             ['我在。\n别急。']
[PASS] 10 防抖：3 条 → 1 轮                chat_end=1
```

`gaps=[501, 306]` 值得注意：第一个 501ms 是 mock 分片间隔（LLM 出字慢），第二个 306ms 才是我们的段间停顿。
两者叠加，接近真实体验。

回归：#94 仍 9/9，`test_stream_parse` 10/10。

## 6. 没做

- 不做「按段长动态算停顿」（需要正文长度先知道，流式阶段没有）
- 不改 `vendor/`，不动上游 `/ws/chat`
- `chat_end` 仍只发一次（前端拿到完整 `segments` 后做最终绑定与落盘）
