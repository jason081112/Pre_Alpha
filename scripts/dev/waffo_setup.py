# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""在 Waffo Pancake 上创建 BYOK 订阅商品并注册回调（#102）。

    set -a; . /etc/prealpha/env; set +a
    /opt/prealpha/vendor/OpenHer/.venv/bin/python scripts/dev/waffo_setup.py

幂等：同名商品已存在就复用；同 URL 的 webhook 已存在就跳过。
商品在测试环境创建后 publish 到生产；API Key 决定当前环境。

只打印商品 ID 与建议写入 env 的行，不打印任何密钥。
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shell import plans  # noqa: E402
from shell.waffo import WEBHOOK_EVENTS, WaffoError, from_env  # noqa: E402

NAMES = {
    "monthly": f"{plans.PLAN_NAME} Monthly",
    "yearly": f"{plans.PLAN_NAME} Yearly",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="", help="webhook 地址，默认 ALPHA_PUBLIC_URL + /api/billing/webhook")
    parser.add_argument("--skip-webhook", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = from_env()
    if client is None:
        print("✗ 缺少 WAFFO_MERCHANT_ID / WAFFO_STORE_ID / WAFFO_PRIVATE_KEY_B64")
        return 1

    public = (os.getenv("ALPHA_PUBLIC_URL") or "https://47.239.51.212.sslip.io").rstrip("/")
    hook_url = args.url or public + "/api/billing/webhook"
    test_mode = (os.getenv("ALPHA_BILLING_MODE") or "test").strip().lower() != "prod"

    print(f"store: {client.store_id}  webhook mode: {'test' if test_mode else 'prod'}")
    products = client.list_products()
    print(f"已有商品: {', '.join(str(p.get('name')) for p in products) if products else '（无）'}")

    def _matches(product: dict, interval: str, amount: str) -> bool:
        """按周期 + 美元价格认领已有商品，名字可以不同（控制台手建过）。"""
        if str(product.get("billingPeriod") or "").lower() != interval:
            return False
        for price in product.get("prices") or []:
            info = price.get("priceInfo") or {}
            if str(price.get("currency") or "").upper() == "USD":
                try:
                    return abs(float(info.get("amount") or 0) - float(amount)) < 0.01
                except (TypeError, ValueError):
                    return False
        return False

    ids: dict[str, str] = {}
    for interval, name in NAMES.items():
        amount = plans.price_for(interval)
        hit = next(
            (p for p in products
             if _matches(p, interval, amount) and str(p.get("status") or "") == "active"),
            None,
        )
        if hit:
            pid = hit.get("id") or ""
            ids[interval] = pid
            print(f"= {name}: {pid}（已存在，状态 {hit.get('status')}，未改价）")
            continue
        if args.dry_run:
            print(f"+ 将创建 {name} ${amount}/{interval}")
            continue
        created = client.create_product(name, interval, amount)
        product = (created.get("data") or {}).get("product") or created.get("product") or created
        pid = str(product.get("id") or "")
        status = str(product.get("status") or "")
        if pid and status.lower() in ("draft", "test", ""):
            try:
                client.publish_product(pid)
                print(f"+ {name}: {pid}（已发布）")
            except WaffoError as exc:
                print(f"+ {name}: {pid}（发布失败：{exc}）")
        else:
            print(f"+ {name}: {pid}（{status}）")
        ids[interval] = pid

    if not args.skip_webhook and not args.dry_run:
        hooks = client.list_webhooks()
        same = [h for h in hooks if h.get("url") == hook_url and bool(h.get("testMode")) == test_mode]
        if same:
            print(f"= webhook 已存在: {hook_url}（{len(same)} 条）")
        else:
            client.add_webhook(hook_url, test_mode=test_mode, events=WEBHOOK_EVENTS)
            print(f"+ webhook: {hook_url}（{len(WEBHOOK_EVENTS)} 个事件，test={test_mode}）")

    print("\n建议写入 /etc/prealpha/env：")
    for interval, pid in ids.items():
        if pid:
            print(f"WAFFO_PRODUCT_BYOK_{interval.upper()}={pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
