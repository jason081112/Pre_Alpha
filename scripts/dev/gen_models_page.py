# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#100 从 provider 注册表生成「模型披露」页。

    python3 scripts/dev/gen_models_page.py          # 写入 site/legal/models.html
    python3 scripts/dev/gen_models_page.py --check  # 仅校验页面是否与注册表同步（CI 用）

页面内容必须与实际路由一致：用户能看到「平台默认用哪个模型」以及
「绑定自己的 Key 后可能把对话发给谁」。改了 providers.json 却忘了更新页面，
CI 会直接失败。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = ROOT / "shell" / "providers.json"
TARGET = ROOT / "site" / "legal" / "models.html"

DEFAULT_ROUTE = os.getenv("ALPHA_DEFAULT_ROUTE", "dashscope/qwen-plus-latest")

PROVIDER_NOTES = {
    "ollama-cloud": "Ollama Cloud 托管模型",
    "openai": "OpenAI 官方 API",
    "deepseek": "DeepSeek 官方 API",
    "moonshot": "月之暗面 Kimi 官方 API",
    "dashscope": "阿里云百炼（通义千问）",
    "gemini": "Google Gemini 官方 API",
    "claude": "Anthropic Claude 官方 API",
    "zhipu": "智谱 AI 开放平台",
}


def _host(base_url: str) -> str:
    value = str(base_url or "").strip()
    if not value:
        return "由 SDK 决定"
    value = value.split("://", 1)[-1]
    return value.split("/", 1)[0]


def _load() -> dict:
    return json.loads(PROVIDERS.read_text(encoding="utf-8"))


def _default_row() -> tuple[str, str]:
    provider, _, model = DEFAULT_ROUTE.partition("/")
    return provider or "dashscope", model or "qwen-plus-latest"


def render(data: dict) -> str:
    providers = data.get("providers") or {}
    default_provider, default_model = _default_row()

    rows = []
    for name in sorted(providers):
        item = providers[name] or {}
        models = item.get("models") or []
        note = PROVIDER_NOTES.get(name, "")
        rows.append(
            "        <tr>"
            f"<td><code>{name}</code>{(' · ' + note) if note else ''}</td>"
            f"<td>{_host(item.get('base_url', ''))}</td>"
            f"<td>{'、'.join(f'<code>{m}</code>' for m in models) or '—'}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#060711">
  <meta name="description" content="Pre_Alpha 模型披露：平台默认模型、可绑定的服务商与模型清单、内容审核说明。">
  <title>模型披露 · Pre_Alpha</title>
  <link rel="stylesheet" href="legal.css">
</head>
<body>
  <div class="aurora" aria-hidden="true"></div>

  <header class="legal-nav">
    <div class="legal-nav-inner">
      <a class="legal-brand" href="../glass/">
        <span class="legal-mark" aria-hidden="true"></span>
        <span>Pre_Alpha</span>
      </a>
      <nav aria-label="法律页面">
        <a href="terms.html">服务条款</a>
        <a href="privacy.html">隐私政策</a>
        <a href="aup.html">可接受使用</a>
        <a href="models.html" aria-current="page">模型披露</a>
      </nav>
    </div>
  </header>

  <main class="legal-wrap">
    <p class="eyebrow">LEGAL / MODEL DISCLOSURE</p>
    <h1>模型披露</h1>
    <p class="meta">
      <span>版本 1.0</span> · <span>运营主体：Wang Xinyu</span> · <span>随服务端配置自动生成</span>
    </p>

    <article class="legal-card">
      <p>Pre_Alpha 不自己训练基础模型。对话由第三方大模型服务生成，我们负责角色设定、上下文组装、额度与内容审核。本页列出当前实际可用的模型，方便你判断对话内容会发送给谁。</p>

      <section id="platform">
        <h2>1. 平台默认模型</h2>
        <p>未绑定自己的 API Key 时，对话走平台额度，使用：</p>
        <ul>
          <li>服务商：<code>{default_provider}</code></li>
          <li>模型：<code>{default_model}</code></li>
        </ul>
        <p>平台额度有每日上限，用完后需要等到次日，或绑定自己的 Key 继续。平台额度下的对话内容同样会发送给上述服务商，处理方式见《<a href="privacy.html">隐私政策</a>》。</p>
      </section>

      <section id="byok">
        <h2>2. 可绑定的服务商与模型</h2>
        <p>绑定自己的 Key 后，对话直接由你选择的服务商处理，计费也由你的账号承担。以下清单与服务端注册表一致：</p>
        <div class="legal-table-wrap">
        <table>
          <thead>
            <tr><th>服务商</th><th>接口域名</th><th>可用模型</th></tr>
          </thead>
          <tbody>
{chr(10).join(rows)}
          </tbody>
        </table>
        </div>
        <p>不同服务商对数据的保留、训练使用与地域处理各有政策，绑定前请阅读其条款。我们不代你同意任何服务商的训练条款。</p>
      </section>

      <section id="moderation">
        <h2>3. 内容审核</h2>
        <p>输入侧在生成前做一次安全检查；输出侧在返回前做规则审核。命中高风险内容时，回复会被替换为安全提示，或在不改变原意的前提下追加求助信息。处置与申诉规则见《<a href="aup.html">可接受使用政策</a>》。</p>
        <p>审核是确定性的本地规则加上游内容过滤，不保证覆盖所有语义变体。发现漏审内容可以通过应用内「举报」或邮件反馈。</p>
      </section>

      <section id="changes">
        <h2>4. 变更</h2>
        <p>新增或下线服务商、模型时，本页随服务端配置一起更新。若变更会影响数据处理方式，我们会在《<a href="privacy.html">隐私政策</a>》中同步说明并重新征得同意。</p>
      </section>

      <p class="legal-foot">本页由 <code>scripts/dev/gen_models_page.py</code> 根据 <code>shell/providers.json</code> 生成，请勿手工编辑。</p>
    </article>
  </main>
</body>
</html>
"""


def main(argv: list[str]) -> int:
    check = "--check" in argv
    try:
        data = _load()
    except (OSError, ValueError) as exc:
        print(f"✗ 读不到 provider 注册表：{exc}")
        return 1

    html = render(data)
    if check:
        try:
            current = TARGET.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"✗ {TARGET.relative_to(ROOT)} 不存在，请运行 gen_models_page.py")
            return 1
        if current != html:
            print(f"✗ {TARGET.relative_to(ROOT)} 与 providers.json 不同步")
            print("  运行：python3 scripts/dev/gen_models_page.py")
            return 1
        print("models.html 与 providers.json 同步")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(html, encoding="utf-8")
    print(f"写入 {TARGET.relative_to(ROOT)}（{len(data.get('providers') or {})} 个服务商）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
