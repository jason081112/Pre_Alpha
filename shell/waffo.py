# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Waffo Pancake 客户端（#102）：手写 RSA 签名 + 回调验签。

官方 SDK 只有 TypeScript；官方文档给了等价的 Python 签名示例，本文件照做：

    canonical = METHOD + "\\n" + PATH + "\\n" + TIMESTAMP + "\\n" + base64(sha256(body))
    signature = base64(RSA-SHA256-PKCS1v15(canonical))

回调验签：`X-Waffo-Signature: t=<ms>,v1=<b64>`，
签名内容是 `f"{t}.{raw_body}"`，时间差容忍 45 分钟（重试会重放原签名）。

`cryptography` 只在真正调用时导入，模块本身可被零依赖测试 import。
"""

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

__all__ = ["WaffoClient", "WaffoError", "from_env"]

BASE_URL = "https://api.waffo.ai"

# 订阅回调里需要处理的全部事件
WEBHOOK_EVENTS = (
    "order.completed",
    "subscription.activated",
    "subscription.payment_succeeded",
    "subscription.renewed",
    "subscription.recovered",
    "subscription.plan_changed",
    "subscription.canceling",
    "subscription.uncanceled",
    "subscription.canceled",
    "subscription.past_due",
    "refund.succeeded",
    "refund.failed",
)

SIG_TOLERANCE_MS = 45 * 60 * 1000


class WaffoError(RuntimeError):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _b64decode_key(value: str):
    """接受 base64(PKCS#8 DER) 或 PEM；返回 cryptography 私钥对象。"""
    from cryptography.hazmat.primitives import serialization

    raw = (value or "").strip()
    if not raw:
        raise WaffoError("缺少私钥")
    if raw.startswith("-----BEGIN"):
        return serialization.load_pem_private_key(raw.encode(), password=None)
    der = base64.b64decode(raw)
    return serialization.load_der_private_key(der, password=None)


def _public_key(value: str):
    from cryptography.hazmat.primitives import serialization

    raw = (value or "").strip()
    if not raw:
        raise WaffoError("缺少 Webhook 公钥")
    if raw.startswith("-----BEGIN"):
        return serialization.load_pem_public_key(raw.encode())
    return serialization.load_pem_public_key(base64.b64decode(raw))


class WaffoClient:
    def __init__(
        self,
        merchant_id: str,
        private_key_b64: str,
        store_id: str,
        *,
        webhook_public_key_b64: str = "",
        base_url: str = BASE_URL,
        timeout: float = 20.0,
    ):
        self.merchant_id = merchant_id
        self.store_id = store_id
        self._private_key_b64 = private_key_b64
        self._webhook_public_key_b64 = webhook_public_key_b64
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._key = None

    # ── 签名与请求 ──────────────────────────────────────────
    @property
    def private_key(self):
        if self._key is None:
            self._key = _b64decode_key(self._private_key_b64)
        return self._key

    def _sign(self, method: str, path: str, body: bytes, timestamp: str) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        body_hash = base64.b64encode(hashlib.sha256(body).digest()).decode()
        canonical = f"{method}\n{path}\n{timestamp}\n{body_hash}"
        sig = self.private_key.sign(
            canonical.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        return base64.b64encode(sig).decode()

    def post(self, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(
            payload or {}, ensure_ascii=False, separators=(",", ":")
        ).encode()
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-Merchant-Id": self.merchant_id,
            "X-Timestamp": timestamp,
            "X-Signature": self._sign("POST", path, body, timestamp),
        }
        req = urllib.request.Request(
            self.base_url + path, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise WaffoError(f"Waffo {path} 返回 {exc.code}", exc.code, detail) from None
        except urllib.error.URLError as exc:
            raise WaffoError(f"Waffo 连接失败：{exc.reason}") from None
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise WaffoError(f"Waffo 返回非 JSON：{raw[:200]}", 0, raw) from None

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        return self.post("/v1/graphql", {"query": query, "variables": variables or {}})

    # ── 商品 ────────────────────────────────────────────────
    def list_products(self) -> list[dict]:
        data = self.graphql(
            "query($storeId: String!) { subscriptionProducts(storeId: $storeId) {"
            " id name status billingPeriod prices { currency priceInfo { amount taxCategory } } } }",
            {"storeId": self.store_id},
        )
        return list(((data.get("data") or {}).get("subscriptionProducts") or []))

    def create_product(self, name: str, interval: str, amount: str) -> dict:
        return self.post(
            "/v1/actions/subscription-product/create-product",
            {
                "storeId": self.store_id,
                "name": name,
                "billingPeriod": interval,
                "prices": {
                    "USD": {
                        "amount": amount,
                        "taxIncluded": False,
                        "taxCategory": "saas",
                    }
                },
            },
        )

    def publish_product(self, product_id: str) -> dict:
        return self.post(
            "/v1/actions/subscription-product/publish-product",
            {"storeId": self.store_id, "productId": product_id},
        )

    # ── 订阅查询（回调丢失时对账） ──────────────────────────
    def find_subscription(self, user_id: str) -> dict | None:
        """按 orderMerchantExternalId 查这个用户的订阅；没有返回 None。

        回调可能因签名/网络失败而丢失，前端确认页用这个兜底：
        支付服务那边已激活，就把本地状态补上。
        """
        user_id = (user_id or "").strip()
        if not user_id:
            return None
        data = self.graphql(
            "query($storeId: String!, $ref: String!) {"
            " subscriptionOrders(storeId: $storeId,"
            " filter: { orderMerchantExternalId: { eq: $ref } }) {"
            " id buyerEmail status billingPeriod orderMerchantExternalId"
            " currentPeriodEnd createdAt } }",
            {"storeId": self.store_id, "ref": user_id},
        )
        rows = list((data.get("data") or {}).get("subscriptionOrders") or [])
        live = [
            r for r in rows
            if str(r.get("status") or "") in ("active", "canceling", "past_due")
        ]
        pick = live or rows
        if not pick:
            return None
        pick.sort(key=lambda r: str(r.get("createdAt") or ""), reverse=True)
        return pick[0]

    # ── 结账 ────────────────────────────────────────────────
    @property
    def default_success_url(self) -> str:
        return default_success_url()

    def create_checkout(
        self,
        *,
        user_id: str,
        interval: str,
        product_id: str = "",
        email: str = "",
        promo_code: str = "",
        success_url: str = "",
    ) -> dict:
        product_id = (product_id or product_id_for(interval)).strip()
        if not product_id:
            raise WaffoError(f"缺少 {interval} 的 Waffo 商品 ID（WAFFO_PRODUCT_BYOK_{interval.upper()}）")
        success_url = success_url or self.default_success_url
        payload = {
            "productId": product_id,
            "currency": "USD",
            "buyerIdentity": user_id,
            "successUrl": success_url,
            "metadata": {"user_id": user_id, "interval": interval},
            "orderMerchantExternalId": user_id,
            "expiresInSeconds": 3600,
            "allowPromotionCodes": True,
        }
        if email:
            payload["buyerEmail"] = email
        if promo_code:
            # 与 allowPromotionCodes 互斥：显式传码时关掉自动码框
            payload["promotionCode"] = promo_code
            payload.pop("allowPromotionCodes", None)
        resp = self.post("/v1/actions/checkout/create-session", payload)
        # Waffo 统一把结果放在 data 下
        return resp.get("data") or resp

    # ── Webhook ─────────────────────────────────────────────
    def verify_webhook(self, raw_body: bytes, signature_header: str, *, now_ms: int = 0) -> bool:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        header = (signature_header or "").strip()
        if not header:
            return False
        parts = {}
        for piece in header.split(","):
            if "=" in piece:
                k, v = piece.split("=", 1)
                parts[k.strip()] = v.strip()
        try:
            t = int(parts.get("t", ""))
        except ValueError:
            return False
        sig_b64 = parts.get("v1") or ""
        if not sig_b64:
            return False
        now = now_ms or int(time.time() * 1000)
        if abs(now - t) > SIG_TOLERANCE_MS:
            return False
        signed = f"{t}.".encode() + raw_body
        try:
            _public_key(self._webhook_public_key_b64).verify(
                base64.b64decode(sig_b64),
                signed,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception:  # noqa: BLE001
            return False
        return True

    def list_webhooks(self) -> list[dict]:
        data = self.graphql(
            "query($storeId: String!) { store(id: $storeId) { storeWebhooks {"
            " id channel url events testMode } } }",
            {"storeId": self.store_id},
        )
        store = (data.get("data") or {}).get("store") or {}
        return list(store.get("storeWebhooks") or [])

    def add_webhook(self, url: str, *, test_mode: bool, events=WEBHOOK_EVENTS) -> dict:
        return self.post(
            "/v1/actions/store/add-webhook",
            {
                "storeId": self.store_id,
                "channel": "http",
                "url": url,
                "events": list(events),
                "testMode": bool(test_mode),
            },
        )


def from_env(env: dict | None = None) -> WaffoClient | None:
    """环境变量不完整时返回 None（服务照常启动，订阅接口回 503）。"""
    env = env or os.environ
    merchant = (env.get("WAFFO_MERCHANT_ID") or "").strip()
    store = (env.get("WAFFO_STORE_ID") or "").strip()
    key = (env.get("WAFFO_PRIVATE_KEY_B64") or "").strip()
    if not (merchant and store and key):
        return None
    return WaffoClient(
        merchant,
        key,
        store,
        webhook_public_key_b64=(env.get("WAFFO_WEBHOOK_PUBLIC_KEY_B64") or "").strip(),
        base_url=(env.get("WAFFO_BASE_URL") or BASE_URL).strip(),
    )


def product_id_for(interval: str, env: dict | None = None) -> str:
    env = env or os.environ
    key = "WAFFO_PRODUCT_BYOK_" + str(interval or "").upper()
    return (env.get(key) or "").strip()


def default_success_url(env: dict | None = None) -> str:
    env = env or os.environ
    return (env.get("ALPHA_PUBLIC_URL") or "https://47.239.51.212.sslip.io").rstrip("/") + "/?billing=success"
