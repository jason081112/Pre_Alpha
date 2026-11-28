# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#76 隐私政策同意记录与门禁。

政策文本在 `site/legal/privacy.html`；这里只负责：
- 当前要求的政策版本号（发版时与页面同步改）
- 读取/写入用户的同意记录（`auth_consent`，按版本留痕）
- 给聊天入口提供 `has_consent()` 门禁

未同意前对话会被拒绝（WS 返回 `consent_required`），
同意记录可审计、可追溯到具体版本与时间。
"""

from __future__ import annotations

from .auth.scope import current_auth_user
from .auth.store import AuthStore

# 与 site/legal/privacy.html 的「版本 x.y」保持一致
PRIVACY_VERSION = "1.2"
CONSENT_KIND = "privacy"
POLICY_URL = "/legal/privacy.html"


def has_consent(store: AuthStore, user: str) -> bool:
    """当前用户是否已同意「当前版本」的隐私政策。"""
    if not user or user == "local":
        # 认证关闭的开发模式：没有真实用户，不拦
        return True
    record = store.get_consent(user, CONSENT_KIND)
    return bool(record and record["version"] == PRIVACY_VERSION)


def build_consent_router(store: AuthStore):
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel

    class ConsentIn(BaseModel):
        version: str
        accepted: bool = True

    router = APIRouter(tags=["consent"])

    @router.get("/api/me/consent")
    def get_consent() -> dict:
        user = current_auth_user.get()
        record = store.get_consent(user, CONSENT_KIND)
        return {
            "required_version": PRIVACY_VERSION,
            "accepted": bool(record and record["version"] == PRIVACY_VERSION),
            "accepted_version": record["version"] if record else None,
            "accepted_at": record["agreed_at"] if record else None,
            "policy_url": POLICY_URL,
            "history": store.consent_history(user, CONSENT_KIND),
        }

    @router.post("/api/me/consent")
    def post_consent(payload: ConsentIn) -> dict:
        if not payload.accepted:
            raise HTTPException(status_code=400, detail="需要同意当前版本的政策")
        if payload.version != PRIVACY_VERSION:
            raise HTTPException(
                status_code=409,
                detail=f"政策版本已更新（当前 {PRIVACY_VERSION}），请重新阅读",
            )
        user = current_auth_user.get()
        store.record_consent(user, CONSENT_KIND, PRIVACY_VERSION)
        record = store.get_consent(user, CONSENT_KIND)
        return {
            "accepted": True,
            "accepted_version": record["version"] if record else PRIVACY_VERSION,
            "accepted_at": record["agreed_at"] if record else None,
        }

    return router
