# P1-17 / P1-18 基线

- 日期：2026-10-24
- 路由：`ollama-cloud/gemma4:31b`
- 方式：luna、iris 各 5 轮；同一 persona 复用 `session_id`；轮间隔 1s
- 结果：10 / 10 轮成功，token 统计无缺失
- 原始数据：[`docs/data/p1-17-18-baseline.json`](data/p1-17-18-baseline.json)
- 脚本：[`scripts/dev/baseline.py`](../scripts/dev/baseline.py)

## 汇总

| persona | 轮数 | 平均时延 | 平均 tokens | 时延范围 | prompt tokens | completion tokens |
|---|---:|---:|---:|---:|---:|---:|
| luna | 5 | 5216 ms | 779 | 3330–7122 ms | 3599 | 297 |
| iris | 5 | 6019 ms | 734 | 3826–9516 ms | 3471 | 201 |
| **合计** | **10** | **5618 ms** | **757** | 3330–9516 ms | 7070 | 498 |

## 逐轮

| persona | 轮 | 时延 | total tokens | prompt | completion | 回复字符数 |
|---|---:|---:|---:|---:|---:|---:|
| luna | 1 | 5798 ms | 816 | 728 | 88 | 67 |
| luna | 2 | 4886 ms | 758 | 712 | 46 | 16 |
| luna | 3 | 7122 ms | 855 | 756 | 99 | 87 |
| luna | 4 | 4945 ms | 785 | 728 | 57 | 34 |
| luna | 5 | 3330 ms | 682 | 675 | 7 | 11 |
| iris | 1 | 9516 ms | 731 | 690 | 41 | 9 |
| iris | 2 | 3826 ms | 733 | 689 | 44 | 13 |
| iris | 3 | 6743 ms | 748 | 702 | 46 | 20 |
| iris | 4 | 5582 ms | 778 | 715 | 63 | 49 |
| iris | 5 | 4430 ms | 682 | 675 | 7 | 10 |

## 观察

- **prompt 占大头**：10 轮里 prompt tokens 占 93.4%，completion 只占 6.6%。成本主要来自人格/记忆/系统上下文，不是回复长度。
- **时延波动大**：同一 persona 内从 3.3s 到 9.5s；iris 第 1 轮 9.5s，可能是上游排队或冷启动。单次平均值不足以做 SLA。
- **短回复也会慢**：luna 第 5 轮只回 7 tokens，仍要 3.3s，说明固定上下文和上游排队占主导。
- **人格差异**：luna 回复更短、更口语；iris 更克制。两者 token 结构接近，差异主要在生成内容。

## 限制

- 这是**单次、串行、无并发**的样本，不能代表高峰期。
- 时延来自服务端 `latency_ms`，包含上游排队，不含客户端网络。
- token 来自 provider `usage`；未单独统计 embedding、TTS、工具调用。
- 未做 p50/p95；要作为成本基线，应在目标并发下重复至少 3 组。

## 复现

```bash
python scripts/dev/baseline.py
```

默认输出 `/tmp/baseline.json`，可用 `ALPHA_BASELINE_OUT` 改路径。
