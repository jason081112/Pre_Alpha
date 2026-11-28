# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""从 shell/plans.py 生成 site/pricing/index.html（#102）。

价格只有一个来源，页面由脚本生成；CI 用 `--check` 确认没有手改漂移。

    python3 scripts/dev/gen_pricing_page.py
    python3 scripts/dev/gen_pricing_page.py --check
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shell.plans import (  # noqa: E402
    CURRENCY,
    FEATURES,
    PLAN_NAME,
    PRICES,
    TRIAL_TURNS,
)

OUT = ROOT / "site" / "pricing" / "index.html"

TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#060711">
  <meta name="description" content="Pre_Alpha 价格：新用户免费 {trial} 轮，之后 {name} 订阅 ${monthly} / 月或 ${yearly} / 年，自带 API Key。">
  <title>价格 · Pre_Alpha</title>
  <link rel="stylesheet" href="../legal/legal.css">
  <link rel="stylesheet" href="pricing.css">
</head>
<body>
  <div class="aurora" aria-hidden="true"></div>

  <header class="legal-nav">
    <div class="legal-nav-inner">
      <a class="legal-brand" href="../glass/">
        <span class="legal-mark" aria-hidden="true"></span>
        <span>Pre_Alpha</span>
      </a>
      <nav aria-label="价格与法律">
        <a href="../glass/">项目主页</a>
        <a href="../legal/terms.html">服务条款</a>
        <a href="../legal/privacy.html">隐私政策</a>
        <a href="../legal/aup.html">使用政策</a>
      </nav>
    </div>
  </header>

  <main class="legal-wrap pricing-wrap">
    <p class="eyebrow">PRICING / 价格</p>
    <h1>先聊 {trial} 轮，再决定。</h1>
    <p class="meta">
      <span>版本 2.0</span> · <span>单一订阅档</span> · <span>币种 {currency}</span>
    </p>
    <p class="pricing-lead">
      新用户注册后免费聊 {trial} 轮，不绑卡、不自动扣款。
      用完后订阅 {name} 即可继续：{price_monthly} 每月，或 {price_yearly} 每年（按 10 个月计，省 ${save}）。
      订阅是自带 API Key 的档位 —— 聊天走你自己的 Key，不消耗平台额度。
    </p>

    <div class="price-grid price-grid-beta">
      <article class="price-card price-current price-beta">
        <div class="price-top">
          <span class="price-tag">TRIAL / 试用</span>
          <span class="price-status">注册即得</span>
        </div>
        <h2>免费 {trial} 轮</h2>
        <p class="price-line"><span class="price-amount">$0</span><span class="price-unit">不需要信用卡</span></p>
        <p class="price-desc">按 AI 回复条数计。第 {trial_plus} 条起需要订阅。</p>
        <ul class="price-list">
          <li>完整聊天体验：流式回复与多段消息</li>
          <li>平台默认模型（免费额度内）</li>
          <li>聊天历史按账号隔离保存，可导出</li>
        </ul>
        <a class="price-cta" href="../">开始使用 →</a>
        <p class="price-foot">用完不会自动转付费</p>
      </article>

      <article class="price-card price-pro">
        <div class="price-top">
          <span class="price-tag">BYOK / 自带 Key</span>
          <span class="price-status">订阅</span>
        </div>
        <h2>{name}</h2>
        <p class="price-line"><span class="price-amount">${monthly}</span><span class="price-unit">每月 · {currency}</span></p>
        <p class="price-desc">或 ${yearly} 每年，相当于 ${yearly_monthly} / 月，省 ${save}。</p>
        <ul class="price-list">
{features}
        </ul>
        <a class="price-cta price-cta-primary" href="../?subscribe=1">去订阅 →</a>
        <p class="price-foot">有促销码可在订阅页填写 · 可随时取消</p>
      </article>
    </div>

    <h2 class="pricing-h2">常见问题</h2>
    <dl class="pricing-faq">
      <dt>“轮”怎么算？</dt>
      <dd>按 AI 的回复条数算：你发一条、它回一条 = 1 轮。连续快速发多条消息会被合并成一轮，和聊天页的行为一致。</dd>
      <dt>BYOK 是什么意思？</dt>
      <dd>Bring Your Own Key：在「模型设置」里填自己的服务商 API Key，聊天直接走那家服务商。订阅费是软件使用费，模型调用费由服务商按你的用量收取。</dd>
      <dt>会自动续费吗？</dt>
      <dd>订阅是自动续费的，可随时在订阅页取消；取消后到期前仍可使用，不会立即失效。</dd>
      <dt>怎么取消？</dt>
      <dd>发邮件到 prealpha@163.com，注明注册邮箱，我们会在 1 个工作日内取消；到期前仍可正常使用。自助取消入口在开发中。</dd>
      <dt>价格以后会变吗？</dt>
      <dd>已生效的订阅在当期不受影响。调价会提前在站内公示，不会静默改价。</dd>
    </dl>

    <p class="pricing-note">
      支付由 Waffo Pancake 处理，本站不接触卡号。订阅前请阅读
      <a href="../legal/terms.html">服务条款</a>、<a href="../legal/privacy.html">隐私政策</a>
      与 <a href="../legal/aup.html">使用政策</a>。
    </p>
  </main>

  <footer class="legal-footer">
    <div class="legal-footer-inner">
      <span>Pre_Alpha · 运营主体 Wang Xinyu</span>
      <span><a href="../legal/">法律与合规</a> · <a href="mailto:prealpha@163.com">prealpha@163.com</a></span>
    </div>
  </footer>
</body>
</html>
"""


def render() -> str:
    monthly = PRICES["monthly"]
    yearly = PRICES["yearly"]
    save = f"{float(monthly) * 12 - float(yearly):.0f}"
    features = "\n".join(f"          <li>{item}</li>" for item in FEATURES)
    return TEMPLATE.format(
        trial=TRIAL_TURNS,
        trial_plus=TRIAL_TURNS + 1,
        name=PLAN_NAME,
        currency=CURRENCY,
        monthly=monthly,
        yearly=yearly,
        price_monthly=f"${monthly}",
        price_yearly=f"${yearly}",
        yearly_monthly=f"{float(yearly) / 12:.2f}",
        save=save,
        features=features,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验不写文件")
    args = parser.parse_args()

    html = render()
    if args.check:
        if not OUT.exists():
            print(f"✗ 缺少 {OUT.relative_to(ROOT)}")
            return 1
        if OUT.read_text(encoding="utf-8") != html:
            print(f"✗ {OUT.relative_to(ROOT)} 与 shell/plans.py 不一致，请重新生成")
            return 1
        print("pricing page in sync")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
