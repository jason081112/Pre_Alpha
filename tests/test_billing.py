# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#102 试用与订阅状态机的零依赖测试。

    python3 tests/test_billing.py

不依赖 fastapi / cryptography：只测 BillingStore 与 apply_event 的语义。
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shell.billing import BillingStore, apply_event, extract_user  # noqa: E402
from shell.plans import PRICES, TRIAL_TURNS, plan_payload, price_for  # noqa: E402

USER = "11111111-2222-3333-4444-555555555555"


def event(etype: str, eid: str, **data) -> dict:
    return {
        "eventType": etype,
        "eventId": eid,
        "storeId": "STO_test",
        "mode": "test",
        "data": {"orderId": "ORD_1", "merchantProvidedBuyerIdentity": USER, **data},
    }


class BillingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "billing.db")
        self.store = BillingStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    # ── 试用 ───────────────────────────────────────────────
    def test_new_user_starts_on_trial(self) -> None:
        allowed, reason = self.store.can_chat(USER)
        self.assertTrue(allowed)
        self.assertEqual(reason, "trial")
        self.assertEqual(self.store.trial_used(USER), 0)

    def test_record_turn_increments(self) -> None:
        self.assertEqual(self.store.record_turn(USER), 1)
        self.assertEqual(self.store.record_turn(USER), 2)
        self.assertEqual(self.store.trial_used(USER), 2)

    def test_thirty_turns_exhaust_trial(self) -> None:
        for _ in range(TRIAL_TURNS):
            self.store.record_turn(USER)
        allowed, reason = self.store.can_chat(USER)
        self.assertFalse(allowed)
        self.assertEqual(reason, "trial_exhausted")

    def test_local_user_is_unlimited_and_not_counted(self) -> None:
        self.assertTrue(self.store.can_chat("local")[0])
        self.assertEqual(self.store.record_turn("local"), 0)
        self.assertEqual(self.store.trial_used("local"), 0)

    def test_status_shape(self) -> None:
        st = self.store.status(USER)
        self.assertEqual(st["trial"]["limit"], TRIAL_TURNS)
        self.assertEqual(st["trial"]["remaining"], TRIAL_TURNS)
        self.assertFalse(st["subscribed"])
        self.assertTrue(st["can_chat"])
        self.assertEqual(st["plan"]["prices"], PRICES)

    # ── 订阅 ───────────────────────────────────────────────
    def test_subscription_unlocks_and_stops_trial_counting(self) -> None:
        self.store.set_subscription(
            USER,
            status="active",
            order_id="ORD_1",
            interval="monthly",
            current_period_end="2999-01-01",
        )
        self.store.record_turn(USER)
        self.assertEqual(self.store.trial_used(USER), 0)  # 订阅期间不消耗试用
        allowed, reason = self.store.can_chat(USER)
        self.assertTrue(allowed)
        self.assertEqual(reason, "subscribed")

    def test_canceling_still_allows(self) -> None:
        self.store.set_subscription(USER, status="canceling", current_period_end="2999-01-01")
        self.assertTrue(self.store.is_subscribed(USER))

    def test_canceled_revokes(self) -> None:
        self.store.set_subscription(USER, status="canceled", current_period_end="2999-01-01")
        self.assertFalse(self.store.is_subscribed(USER))

    def test_period_end_past_grace_revokes(self) -> None:
        old = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 40 * 86400))
        self.store.set_subscription(USER, status="active", current_period_end=old)
        self.assertFalse(self.store.is_subscribed(USER))

    def test_period_end_within_grace_allows(self) -> None:
        recent = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 2 * 86400))
        self.store.set_subscription(USER, status="active", current_period_end=recent)
        self.assertTrue(self.store.is_subscribed(USER))

    def test_upsert_keeps_missing_fields(self) -> None:
        self.store.set_subscription(
            USER,
            status="active",
            order_id="ORD_1",
            interval="monthly",
            current_period_end="2999-01-01",
            email="a@example.com",
        )
        # renewed 事件可能只带 period end
        self.store.set_subscription(USER, status="active", current_period_end="2999-02-01")
        sub = self.store.subscription(USER)
        self.assertEqual(sub["order_id"], "ORD_1")
        self.assertEqual(sub["interval"], "monthly")
        self.assertEqual(sub["email"], "a@example.com")
        self.assertEqual(sub["current_period_end"], "2999-02-01")

    # ── 回调 ───────────────────────────────────────────────
    def test_activated_event_grants_access(self) -> None:
        result = apply_event(
            self.store,
            event(
                "subscription.activated",
                "ORD_1",
                billingPeriod="yearly",
                currentPeriodEnd="2999-01-01",
                buyerEmail="a@example.com",
            ),
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["duplicate"])
        sub = self.store.subscription(USER)
        self.assertEqual(sub["status"], "active")
        self.assertEqual(sub["interval"], "yearly")
        self.assertTrue(self.store.is_subscribed(USER))

    def test_duplicate_event_is_idempotent(self) -> None:
        ev = event("subscription.activated", "ORD_1", currentPeriodEnd="2999-01-01")
        apply_event(self.store, ev)
        again = apply_event(self.store, ev)
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self.store.events()), 1)

    def test_canceled_event_revokes(self) -> None:
        apply_event(self.store, event("subscription.activated", "ORD_1", currentPeriodEnd="2999-01-01"))
        apply_event(self.store, event("subscription.canceled", "ORD_2", currentPeriodEnd="2999-01-01"))
        self.assertFalse(self.store.is_subscribed(USER))

    def test_refund_revokes(self) -> None:
        apply_event(self.store, event("subscription.activated", "ORD_1", currentPeriodEnd="2999-01-01"))
        apply_event(self.store, event("refund.succeeded", "REF_1"))
        self.assertFalse(self.store.is_subscribed(USER))

    def test_past_due_keeps_access(self) -> None:
        apply_event(self.store, event("subscription.activated", "ORD_1", currentPeriodEnd="2999-01-01"))
        apply_event(self.store, event("subscription.past_due", "ORD_1-2026", currentPeriodEnd="2999-01-01"))
        self.assertTrue(self.store.is_subscribed(USER))

    def test_missing_fields_rejected(self) -> None:
        self.assertFalse(apply_event(self.store, {"eventType": "", "eventId": ""})["ok"])

    def test_extract_user_fallbacks(self) -> None:
        self.assertEqual(
            extract_user({"data": {"merchantProvidedBuyerIdentity": " u1 "}}), "u1"
        )
        self.assertEqual(
            extract_user({"data": {"orderMerchantExternalId": "u2"}}), "u2"
        )
        self.assertEqual(extract_user({"data": {}}), "")

    def test_event_without_user_is_recorded_but_no_subscription(self) -> None:
        ev = {"eventType": "order.completed", "eventId": "PAY_1", "data": {}}
        apply_event(self.store, ev)
        self.assertIsNone(self.store.subscription(USER))
        self.assertEqual(len(self.store.events()), 1)

    # ── 结账记录 ───────────────────────────────────────────
    def test_checkout_records(self) -> None:
        self.store.save_checkout(USER, "CS_1", "monthly", "DEV50")
        rows = self.store.checkouts(USER)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["promo_code"], "DEV50")

    def test_mark_event_returns_false_on_duplicate(self) -> None:
        self.assertTrue(self.store.mark_event("x", "1"))
        self.assertFalse(self.store.mark_event("x", "1"))
        self.assertTrue(self.store.mark_event("x", "2"))


class PlansTests(unittest.TestCase):
    def test_single_byok_tier(self) -> None:
        payload = plan_payload()
        self.assertEqual(payload["id"], "byok")
        self.assertEqual(payload["prices"], {"monthly": "9.00", "yearly": "90.00"})

    def test_price_for(self) -> None:
        self.assertEqual(price_for("monthly"), "9.00")
        self.assertEqual(price_for("YEARLY"), "90.00")
        with self.assertRaises(ValueError):
            price_for("weekly")

    def test_trial_is_thirty(self) -> None:
        self.assertEqual(TRIAL_TURNS, 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
