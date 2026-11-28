# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#102 订阅档位与试用规则：价格的唯一来源（零依赖）。

只做单一 BYOK 档：

- 月付 $9，年付 $90（按 10 个月，省 2 个月）
- 新用户免费聊 30 轮（按 AI 回复条数计），第 31 轮起必须订阅

`site/pricing/index.html` 由 scripts/dev/gen_pricing_page.py 从这里生成，
页面、后端、Waffo 商品三处价格必须一致，由 CI 校验。
"""

from __future__ import annotations

__all__ = [
    "PLAN_ID",
    "PLAN_NAME",
    "CURRENCY",
    "TRIAL_TURNS",
    "PRICES",
    "INTERVAL_LABELS",
    "FEATURES",
    "price_for",
    "plan_payload",
    "trials_label",
]

PLAN_ID = "byok"
PLAN_NAME = "BYOK"
CURRENCY = "USD"
TRIAL_TURNS = 30

# 单一价格来源；改价要同时改 Waffo 商品（见 docs/billing.md）
PRICES = {"monthly": "9.00", "yearly": "90.00"}
INTERVAL_LABELS = {"monthly": "按月订阅", "yearly": "按年订阅"}
INTERVALS = ("monthly", "yearly")

FEATURES = (
    "自带 API Key（BYOK），聊天不消耗平台额度",
    "网页端与 OpenAI 兼容接口同一份订阅",
    "不限消息条数，只受你自己的 Key 额度限制",
    "可随时取消，到期前仍可使用",
    "年付按 10 个月计价（省 2 个月）",
)


def price_for(interval: str) -> str:
    """返回 USD 金额字符串；未知周期抛 ValueError。"""
    key = str(interval or "").strip().lower()
    if key not in PRICES:
        raise ValueError(f"未知订阅周期：{interval!r}")
    return PRICES[key]


def trials_label() -> str:
    return f"新用户免费 {TRIAL_TURNS} 轮"


def plan_payload() -> dict:
    """给前端/接口用的档位描述（不读环境变量，避免两处不一致）。"""
    return {
        "id": PLAN_ID,
        "name": PLAN_NAME,
        "currency": CURRENCY,
        "trial_turns": TRIAL_TURNS,
        "prices": dict(PRICES),
        "interval_labels": dict(INTERVAL_LABELS),
        "features": list(FEATURES),
    }
