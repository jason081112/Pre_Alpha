# P1-92 · provider 配置外置

> 状态：#92 已关闭。本文记录最终形态与遗留边界。

## 问题

`vendor/OpenHer/providers/api_config.py:39` 把配置路径写死：

```python
_CONFIG_PATH = Path(__file__).parent.parent / "providers" / "api.yaml"
```

且 `base_url` **只能从 yaml 的 preset 读，没有任何环境变量覆盖**：

```python
base_url = preset.get("base_url", "")     # 只能从 yaml 来
```

于是：

1. 默认 provider 是 `gemini`，没 key 时**启动直接失败**
2. 换 provider 必须手改 `vendor/OpenHer/providers/api.yaml`
3. `vendor/` 已 gitignore → 换机器或重新 fetch 必然重踩
4. 改错没有报错，只会静默用错地址

## 方案

```text
config/provider.yaml          ← 入库，不含密钥，人改这个
        │  apply_provider_config.py
        ▼
vendor/OpenHer/providers/api.yaml   ← 生成物，gitignore，不手改
```

`config/provider.yaml` 现状：

```yaml
provider: openai
base_url: https://ollama.com/v1
api_key_env: OPENAI_API_KEY
default_model: gemma4:31b
```

### 关键：`provider: openai` 不等于「用 OpenAI 官方」

`provider` 是上游的**实现名**（`providers/registry.get_llm("openai")`），不是服务商。
`base_url` 才决定实际地址。ollama cloud 兼容 OpenAI 协议，所以：

- `provider: openai` + `base_url: https://ollama.com/v1` → 走 ollama
- `api_key_env: OPENAI_API_KEY` → 密钥变量名叫这个，**里面填 ollama 的 key**

实测：`POST https://ollama.com/v1/chat/completions` → 200，完整链路 13 秒出回复。

## 三个使用点

| 场景 | 命令 |
|---|---|
| 拉取 / 更新 vendor | `scripts/dev/fetch_openher.sh` —— **自动应用**（第 212/222 行） |
| 手动写入 | `python3 scripts/dev/apply_provider_config.py` |
| 检查是否漂移 | `python3 scripts/dev/apply_provider_config.py --check`（不一致退出码 1） |

当前状态：`--check` → `[config] 一致（provider=openai）`。

## 启动解耦（2026-11-06 补）

上游 startup 会**无条件**创建一次默认 LLM 客户端，缺 key 直接 raise 退出——尽管它随后会被
`RoutingLLMClient` 替换。

`shell/app.py` 在没有 `OPENAI_API_KEY` 时注入占位符：

```python
if not os.environ.get("OPENAI_API_KEY", "").strip():
    os.environ["OPENAI_API_KEY"] = "alpha-placeholder-startup-only"
```

行为：

| | 结果 |
|---|---|
| 启动 | 正常，`/api/auth/config` 200，控制台打印配置提示 |
| 调用模型 | `500 provider_unavailable`，进程不崩 |

密钥恢复：`scripts/dev/restore_env.sh` 会从 `/sandbox/workspace/.secrets/alpha.env` 读取
（只报条数，不打印值）。该文件在仓库外，不入库。

## 两套 provider 配置的分工

| 文件 | 谁读 | 管什么 |
|---|---|---|
| `config/provider.yaml` → `vendor/…/api.yaml` | 上游引擎内部 | startup 默认客户端，以及未走 `llm_client` 的内部调用（embedding / TTS 等） |
| `shell/providers.json` | 外壳 | 用户可选的 7 个 provider、模型列表、限流与路由回退 |

两者**不互通**：改 `providers.json` 不会同步 `api.yaml`。

实际影响有限——用户请求走的是 `RoutingLLMClient`（读 `providers.json`），`api.yaml` 只兜底
引擎内部调用。但如果你换了主 provider 且发现某些内部能力（如 embedding）仍走旧地址，
改的是 `config/provider.yaml`。

> 上游哪些内部调用绕过了 `llm_client`，尚未逐一核实。列为已知不确定项。

## 遗留

- **CI 没有 `--check`**：CI 里 vendor 不存在（gitignore），`--check` 会因缺目标文件失败。
  改用「fetch 时自动应用 + 本地可 --check」的机制。
- **`api_key_env` 只支持一个变量名**：想同时配 ollama 和 deepseek，只能共用一个变量名，
  或改 `providers.json` 走外壳路由（推荐后者）。
