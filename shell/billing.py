# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#102 订阅与试用：本地状态 + Waffo 结账/回调（零依赖存储层）。

职责边界：

- **试用**：每个用户免费 30 轮 AI 回复，`record_turn` 只在未订阅时累加。
- **订阅**：`status` 只认 active / canceling / past_due 三种为有效；
  canceled 立即失效。`current_period_end` 之后留 7 天宽限，防时钟漂移。
- **幂等**：回调事件按 `(event_type, event_id)` 唯一约束落库，
  重复投递只返回 2xx，不重复改状态。
- **结账**：只记录 session 与用户/周期的对应关系，便于对账。

Waffo 的签名、HTTP 调用放在 shell/waffo.py；本模块不依赖第三方库，
方便本地零依赖测试。

注意：这里不能用 `from __future__ import annotations`（与 consent.py /
moderation.py 同一个坑：fastapi 延迟导入后注解字符串化，请求体被当成 query）。
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .plans import CURRENCY, PLAN_ID, PRICES, TRIAL_TURNS, price_for

__all__ = [
    "ACTIVE_STATUSES",
    "BillingStore",
    "build_billing_router",
    "apply_event",
    "extract_user",
]

# 有效订阅状态；canceled 立即失去权益
ACTIVE_STATUSES = ("active", "canceling", "past_due")
GRACE_DAYS = 7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_trial (
    user_id     TEXT PRIMARY KEY,
    turns       INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_subscription (
    user_id            TEXT PRIMARY KEY,
    order_id           TEXT NOT NULL DEFAULT '',
    product_id         TEXT NOT NULL DEFAULT '',
    interval           TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'active',
    current_period_end TEXT NOT NULL DEFAULT '',
    mode               TEXT NOT NULL DEFAULT '',
    email              TEXT NOT NULL DEFAULT '',
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sub_status ON billing_subscription(status);
CREATE TABLE IF NOT EXISTS billing_event (
    event_type  TEXT NOT NULL,
    event_id    TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '',
    received_at REAL NOT NULL,
    PRIMARY KEY (event_type, event_id)
);
CREATE TABLE IF NOT EXISTS billing_checkout (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    interval    TEXT NOT NULL DEFAULT '',
    promo_code  TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkout_user ON billing_checkout(user_id, created_at);
"""


class BillingStore:
    """SQLite 存储；所有方法可在多线程下调用。"""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        # RLock：apply_event 持锁时还会调 set_subscription / subscription
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ── 试用 ────────────────────────────────────────────────
    def trial_used(self, user: str) -> int:
        if not user or user == "local":
            return 0
        with self._lock:
            row = self._conn.execute(
                "SELECT turns FROM billing_trial WHERE user_id = ?", (user,)
            ).fetchone()
        return int(row["turns"]) if row else 0

    def record_turn(self, user: str) -> int:
        """成功回复一条后调用；已订阅用户不消耗试用轮数。"""
        if not user or user == "local":
            return 0
        if self.is_subscribed(user):
            return self.trial_used(user)
        now = self._clock()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO billing_trial (user_id, turns, created_at, updated_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    turns = turns + 1,
                    updated_at = excluded.updated_at
                """,
                (user, now, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT turns FROM billing_trial WHERE user_id = ?", (user,)
            ).fetchone()
        return int(row["turns"]) if row else 0

    def set_trial_used(self, user: str, turns: int) -> None:
        """测试/客服调整用；不做业务校验。"""
        now = self._clock()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO billing_trial (user_id, turns, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    turns = excluded.turns, updated_at = excluded.updated_at
                """,
                (user, max(0, int(turns)), now, now),
            )
            self._conn.commit()

    # ── 订阅 ────────────────────────────────────────────────
    def subscription(self, user: str) -> dict | None:
        if not user or user == "local":
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM billing_subscription WHERE user_id = ?", (user,)
            ).fetchone()
        return dict(row) if row else None

    def is_subscribed(self, user: str) -> bool:
        sub = self.subscription(user)
        if not sub or sub.get("status") not in ACTIVE_STATUSES:
            return False
        end = str(sub.get("current_period_end") or "").strip()
        if not end:
            return True
        return _days_left(end, self._clock()) >= -GRACE_DAYS

    def set_subscription(
        self,
        user: str,
        *,
        status: str,
        order_id: str = "",
        product_id: str = "",
        interval: str = "",
        current_period_end: str = "",
        mode: str = "",
        email: str = "",
    ) -> dict:
        """UPSERT；空字符串不覆盖已有值（renewed 事件常常不带全部字段）。"""
        now = self._clock()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO billing_subscription
                    (user_id, order_id, product_id, interval, status,
                     current_period_end, mode, email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    order_id = CASE WHEN excluded.order_id != '' THEN excluded.order_id ELSE order_id END,
                    product_id = CASE WHEN excluded.product_id != '' THEN excluded.product_id ELSE product_id END,
                    interval = CASE WHEN excluded.interval != '' THEN excluded.interval ELSE interval END,
                    status = excluded.status,
                    current_period_end = CASE WHEN excluded.current_period_end != ''
                        THEN excluded.current_period_end ELSE current_period_end END,
                    mode = CASE WHEN excluded.mode != '' THEN excluded.mode ELSE mode END,
                    email = CASE WHEN excluded.email != '' THEN excluded.email ELSE email END,
                    updated_at = excluded.updated_at
                """,
                (
                    user,
                    order_id,
                    product_id,
                    interval,
                    status,
                    current_period_end,
                    mode,
                    email,
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return self.subscription(user) or {}

    def subscriptions(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM billing_subscription ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── 权益 ────────────────────────────────────────────────
    def can_chat(self, user: str) -> tuple[bool, str]:
        """(允许?, 原因)；原因用于错误码：subscribed / trial / trial_exhausted。"""
        if not user or user == "local":
            return True, "local"  # 开发模式放行
        if self.is_subscribed(user):
            return True, "subscribed"
        if self.trial_used(user) < TRIAL_TURNS:
            return True, "trial"
        return False, "trial_exhausted"

    def status(self, user: str) -> dict:
        used = self.trial_used(user)
        sub = self.subscription(user)
        subscribed = self.is_subscribed(user)
        return {
            "plan": {
                "id": PLAN_ID,
                "name": "BYOK",
                "currency": CURRENCY,
                "prices": dict(PRICES),
            },
            "trial": {
                "used": used,
                "limit": TRIAL_TURNS,
                "remaining": max(0, TRIAL_TURNS - used),
            },
            "subscribed": subscribed,
            "subscription": sub,
            "can_chat": subscribed or used < TRIAL_TURNS,
        }

    # ── 事件幂等 ────────────────────────────────────────────
    def mark_event(self, event_type: str, event_id: str, payload: Any = None) -> bool:
        """首次见到返回 True；重复事件返回 False。"""
        raw = ""
        if payload is not None:
            try:
                raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:20000]
            except Exception:  # noqa: BLE001
                raw = ""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO billing_event (event_type, event_id, payload, received_at)"
                    " VALUES (?, ?, ?, ?)",
                    (event_type, event_id, raw, self._clock()),
                )
            except sqlite3.IntegrityError:
                return False
            self._conn.commit()
        return True

    def events(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_type, event_id, received_at FROM billing_event"
                " ORDER BY received_at DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── 结账记录 ────────────────────────────────────────────
    def save_checkout(self, user: str, session_id: str, interval: str, promo: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO billing_checkout"
                " (session_id, user_id, interval, promo_code, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, user, interval, promo or "", self._clock()),
            )
            self._conn.commit()

    def checkouts(self, user: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM billing_checkout WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_user(self, user: str) -> dict:
        """#75 注销：清掉试用计数/订阅行/结账记录与该用户相关的回调事件。"""
        user = str(user or "").strip()
        counts: dict[str, int] = {}
        with self._lock:
            for table in ("billing_trial", "billing_subscription", "billing_checkout"):
                cur = self._conn.execute(
                    f"DELETE FROM {table} WHERE user_id = ?", (user,)
                )
                counts[table] = int(cur.rowcount or 0)
            cur = self._conn.execute(
                "DELETE FROM billing_event WHERE payload LIKE ?",
                (f"%{user}%",),
            )
            counts["billing_event"] = int(cur.rowcount or 0)
            self._conn.commit()
        return counts

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _days_left(period_end: str, now: float) -> int:
    """period_end(YYYY-MM-DD) 距今天数；解析失败当作 0（不额外惩罚）。"""
    try:
        import datetime as _dt

        end = _dt.date.fromisoformat(str(period_end)[:10])
        today = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).date()
        return (end - today).days
    except Exception:  # noqa: BLE001
        return 0


def extract_user(event: dict) -> str:
    """从回调事件里取我们的用户 id：优先 buyerIdentity，其次外部订单号。"""
    data = event.get("data") or {}
    for key in ("merchantProvidedBuyerIdentity", "orderMerchantExternalId"):
        val = str(data.get(key) or "").strip()
        if val:
            return val
    return ""


def apply_event(store: BillingStore, event: dict) -> dict:
    """把 Waffo 回调映射成订阅状态；重复事件不重复生效。"""
    etype = str(event.get("eventType") or "").strip()
    eid = str(event.get("eventId") or "").strip()
    if not etype or not eid:
        return {"ok": False, "reason": "missing_event"}

    data = event.get("data") or {}
    user = extract_user(event)
    with store._lock:  # 事件记录与业务变更同一事务
        try:
            store._conn.execute(
                "INSERT INTO billing_event (event_type, event_id, payload, received_at)"
                " VALUES (?, ?, ?, ?)",
                (etype, eid, json.dumps(event, ensure_ascii=False)[:20000], store._clock()),
            )
        except sqlite3.IntegrityError:
            return {"ok": True, "duplicate": True, "event": etype}
        if user:
            _apply_status(store, user, etype, data, event)
        store._conn.commit()
    return {"ok": True, "duplicate": False, "event": etype, "user": user}


def _apply_status(store: BillingStore, user: str, etype: str, data: dict, event: dict) -> None:
    order_id = str(data.get("orderId") or "")
    period_end = str(data.get("currentPeriodEnd") or "")
    mode = str(event.get("mode") or "")
    email = str(data.get("buyerEmail") or "")
    interval = str(data.get("billingPeriod") or "")
    product_name = str(data.get("productName") or "")
    product_id = str(data.get("productId") or "")

    common = {
        "order_id": order_id,
        "product_id": product_id or product_name,
        "interval": interval,
        "current_period_end": period_end,
        "mode": mode,
        "email": email,
    }

    if etype in ("subscription.activated", "subscription.renewed",
                 "subscription.recovered", "subscription.uncanceled"):
        store.set_subscription(user, status="active", **common)
    elif etype == "subscription.canceling":
        store.set_subscription(user, status="canceling", **common)
    elif etype in ("subscription.canceled", "refund.succeeded"):
        store.set_subscription(user, status="canceled", **common)
    elif etype == "subscription.past_due":
        store.set_subscription(user, status="past_due", **common)
    # order.completed / payment_succeeded 不改变权益，只作为审计事件落库


def build_billing_router(store: BillingStore, *, waffo: Any = None, success_url: str = ""):
    """用户侧订阅接口 + Waffo 回调；延迟导入 fastapi 便于零依赖测试。"""
    from fastapi import APIRouter, HTTPException, Request, Response
    from pydantic import BaseModel, Field

    from .auth.scope import current_auth_user

    class CheckoutIn(BaseModel):
        interval: str = Field(default="monthly")
        promo_code: str = Field(default="", max_length=64)

    router = APIRouter(tags=["billing"])

    def _user() -> str:
        user = current_auth_user.get()
        if not user or user == "local":
            raise HTTPException(401, "请先登录")
        return user

    @router.get("/api/billing/status")
    def billing_status() -> dict:
        return store.status(_user())

    @router.post("/api/billing/sync")
    def billing_sync() -> dict:
        """支付回跳后的对账：本地没生效就去支付服务查一次。

        回调可能丢（签名失败/超时），但钱已经付了；这里按
        orderMerchantExternalId 反查订阅，查到就补上本地状态。
        """
        user = _user()
        current = store.status(user)
        if current["subscribed"]:
            return current
        if waffo is None:
            raise HTTPException(503, "支付尚未配置")
        try:
            sub = waffo.find_subscription(user)
        except Exception:  # noqa: BLE001 - 对外不暴露上游细节
            raise HTTPException(502, "暂时无法向支付服务确认，请稍后重试") from None
        if not sub:
            return current
        status = str(sub.get("status") or "")
        if status in ("active", "canceling", "past_due"):
            store.set_subscription(
                user,
                status=status,
                order_id=str(sub.get("id") or ""),
                interval=str(sub.get("billingPeriod") or ""),
                current_period_end=str(sub.get("currentPeriodEnd") or ""),
                mode="sync",
                email=str(sub.get("buyerEmail") or ""),
            )
        return store.status(user)

    @router.post("/api/billing/checkout")
    def checkout(payload: CheckoutIn) -> dict:
        user = _user()
        interval = payload.interval.strip().lower()
        if interval not in PRICES:
            raise HTTPException(422, "interval 必须是 monthly 或 yearly")
        if store.is_subscribed(user):
            raise HTTPException(409, "当前已是有效订阅")
        if waffo is None:
            raise HTTPException(503, "支付尚未配置")
        promo = (payload.promo_code or "").strip()
        if promo and not (3 <= len(promo) <= 64):
            raise HTTPException(422, "促销码格式不正确")
        try:
            result = waffo.create_checkout(
                user_id=user,
                interval=interval,
                promo_code=promo,
                success_url=success_url or waffo.default_success_url,
            )
        except Exception as exc:  # noqa: BLE001 - 上游错误不能变成 500
            status = getattr(exc, "status", 0) or 502
            detail = str(getattr(exc, "body", "") or exc)
            if status == 409:
                raise HTTPException(409, "促销码无效或该订单已存在，请检查后重试") from None
            raise HTTPException(502, f"支付服务暂时不可用（{status}）：{detail[:120]}") from None
        session_id = str(result.get("sessionId") or "")
        if session_id:
            store.save_checkout(user, session_id, interval, promo)
        return {"url": result.get("checkoutUrl"), "session_id": session_id}

    @router.post("/api/billing/webhook")
    async def webhook(request: Request) -> Response:
        if waffo is None:
            raise HTTPException(503, "支付尚未配置")
        raw = await request.body()
        sig = request.headers.get("x-waffo-signature") or ""
        if not waffo.verify_webhook(raw, sig):
            raise HTTPException(401, "签名校验失败")
        try:
            event = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "请求体不是 JSON")
        apply_event(store, event)
        return Response(status_code=200, content='{"ok":true}', media_type="application/json")

    return router
