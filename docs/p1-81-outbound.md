# P1-81 provider 出站策略与 DNS rebinding 防护

- 日期：2026-10-25
- 结果：零依赖单测 **13 / 13 PASS**；真实 HTTP transport smoke **PASS**
- CI：<https://github.com/jason081112/Pre_Alpha/actions/runs/40154278011>
- 原始结果：[`docs/data/p1-81-outbound.json`](data/p1-81-outbound.json)
- 范围：自定义 provider 的创建、更新、探活、真实请求
- 代码：[`shell/outbound.py`](../shell/outbound.py)
- 单测：[`tests/test_outbound.py`](../tests/test_outbound.py)
- 真实 HTTP 冒烟：[`scripts/dev/verify_outbound_http.py`](../scripts/dev/verify_outbound_http.py)
- CI：[`outbound-smoke.yml`](../.github/workflows/outbound-smoke.yml)

## 问题

自定义 provider 允许用户填写任意 `base_url`。原实现只在创建时做一次 DNS
解析，校验与真正请求之间存在 TOCTOU：

1. 创建时域名解析到公网；
2. 真正请求时 DNS 被改成 `127.0.0.1` 或 `169.254.169.254`；
3. 请求打到内网或云元数据。

## 方案

所有出站路径统一走 `plan_outbound()`：

```
URL -> 解析主机名 -> 校验全部 IP -> 固定一个 IP -> 后续请求不再查 DNS
```

| 能力 | 实现 |
|---|---|
| 私网拒绝 | 环回、私网、链路本地、保留、组播、未指定、非 global 全部拒绝 |
| DNS rebinding | 一次解析、固定 IP；混合公网/私网答案整体拒绝 |
| Host 头 | 保持原始域名，避免上游虚拟主机路由失败 |
| HTTPS 证书 | 用 `sni_hostname` 扩展保持原始域名，证书仍按域名校验 |
| 重定向 | `follow_redirects=False`；探活遇到 3xx 直接拒绝 |
| 本地调试 | `ALPHA_ALLOW_PRIVATE_PROVIDERS=1` 放行本机/局域网 |
| 错误信息 | 只返回通用原因，不回显内网 IP |
| SDK 重试 | 自定义 provider `max_retries=0`；平台 provider 也归零，回退只由 `shell.routing` 决定 |

### 代码位置

| 文件 | 改动 |
|---|---|
| `shell/outbound.py` | 新增策略层、固定 IP transport、自定义 OpenAI 兼容客户端 |
| `shell/custom.py` | `assert_public_host()` 改为复用 `plan_outbound()` |
| `shell/api.py` | `/models` 探活改用固定 IP 客户端；拒绝重定向 |
| `shell/routing.py` | `custom-*` 使用固定 IP 客户端；平台 provider 关闭 SDK 重试 |

### 为什么不用“只校验 DNS”的方案

只校验 DNS 仍然会在真正连接时重新解析，攻击者可以把 TTL 调到 0，
在两次解析之间切换答案。固定 IP 后，连接目标在客户端创建时就确定，
后续 DNS 变化不会影响已经创建的 transport。

### 为什么 HTTPS 还能正常用

固定 IP 后 URL 的 host 变成 IP，如果直接请求，TLS 会拿 IP 去校验证书。
这里把 `sni_hostname` 设为原始域名，同时保留 `Host` 头，效果等价于：

```python
client.get(
    "https://93.184.216.34/v1/models",
    headers={"Host": "api.example.com"},
    extensions={"sni_hostname": "api.example.com"},
)
```

## 验收

### 零依赖单测

```bash
python3 tests/test_outbound.py
python3 tests/test_routing.py
```

覆盖：

- 首次解析公网、二次解析 `127.0.0.1`：第二次被拒绝
- DNS 同时返回公网 + 私网：整体拒绝
- 内网域名解析到 `10.1.2.3`：拒绝且不回显 IP
- 字面量 `127.0.0.1`、`10.0.0.1`、`192.168.1.5`、`169.254.169.254`、`::1`、`0.0.0.0`：拒绝
- `localhost:11434` + `ALPHA_ALLOW_PRIVATE_PROVIDERS=1`：放行并固定到 `127.0.0.1`
- `localhost` 同时返回 `::1`/`127.0.0.1`：优先 IPv4，避免 Ollama 只监听 IPv4 时失败
- 公网主机名固定 IP 后，URL 使用 IP，Host/SNI 保持域名
- 自定义 provider 路由在默认策略下拒绝私网 `base_url`
- URL 规则：非 http/https、内嵌账号密码、缺主机、坏 IPv6、解析失败

### 真实 HTTP 传输冒烟

结果：**PASS**（GitHub Actions，Python 3.11.17，httpx 0.28.1）。

`scripts/dev/verify_outbound_http.py` 需要 `httpx`，在 CI 里跑：

- 启动 `127.0.0.1` 本地 HTTP mock
- 用 `http://localhost:{port}/v1` 创建目标，实际连接到 `127.0.0.1`
- 校验 mock 收到的 `Host` 是 `localhost:{port}`
- 302 不被跟随
- 默认策略拒绝私网地址
- 二次解析到 `127.0.0.1` 被拒绝

运行方式：

```bash
python -m pip install "httpx>=0.27,<1"
python scripts/dev/verify_outbound_http.py
```

### 验收映射

| issue 验收 | 结果 |
|---|---|
| 首次解析公网、二次解析 127.0.0.1，请求被拒绝 | PASS：策略层拒绝，固定 IP 后请求不会重新解析 |
| 内网 IP 字面量、私网域名均被拒绝 | PASS |
| 本机 `http://localhost:11434/v1` 正常可用 | PASS：`ALPHA_ALLOW_PRIVATE_PROVIDERS=1` 下策略放行；真实 transport 用 localhost mock 验证 |
| 探活无法被当作端口扫描器 | 私网不可达、重定向不跟随已做；速率限制由 #67 承接 |
| 失败信息不泄露内网地址细节 | PASS：单测断言错误文本不含 `127.0.0.1` / `10.1.2.3` |

## 使用

默认（公网 provider）：

```bash
python -m shell.app
```

本机 Ollama / 局域网调试：

```bash
ALPHA_ALLOW_PRIVATE_PROVIDERS=1 python -m shell.app
```

`ALPHA_ALLOW_PRIVATE_PROVIDERS=1` 会放行所有私网地址，包括云元数据地址；
只应在开发机或隔离环境使用，不要在生产环境打开。

## 边界

- 这层解决“出站地址不可信”，不解决“服务端代理能读到明文 Key/内容”。
- 固定 IP 后，如果 provider 使用 CDN 且 IP 频繁变化，客户端缓存期间可能连到旧 IP；
  重新创建客户端即可刷新。
- 当前 transport 只启用 HTTP/1.1；HTTP/2 的 `:authority` 不在本次范围。
- 探活的端口扫描防护依赖 #67 的限流/配额；本 issue 只保证私网不可达、重定向不跟随。
- 不跟随任何 3xx，包括合法同域跳转；provider 应直接返回最终地址。
