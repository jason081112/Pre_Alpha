# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""P1-67 管理接口：限流配置热更新与 token 审计查询。"""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .limits import LimitConfigError, LimitsService, utc_day


class LimitConfigUpdate(BaseModel):
    user_rpm: int | None = Field(default=None, ge=0)
    user_rpd: int | None = Field(default=None, ge=0)
    ip_rpm: int | None = Field(default=None, ge=0)
    ip_rpd: int | None = Field(default=None, ge=0)
    platform_daily_tokens: int | None = Field(default=None, ge=0)
    probe_per_hour: int | None = Field(default=None, ge=0)
    probe_concurrency: int | None = Field(default=None, ge=0)
    probe_backoff_base: int | None = Field(default=None, ge=0)
    probe_backoff_max: int | None = Field(default=None, ge=0)


def _require_admin(token: str | None) -> None:
    expected = os.getenv("ALPHA_ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="管理接口未启用：请设置 ALPHA_ADMIN_TOKEN",
        )
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="admin token 无效")


def build_limits_admin_router(limits: LimitsService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/admin/limits")
    def get_limits(
        x_alpha_admin_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(x_alpha_admin_token)
        return {"config": limits.config().as_dict()}

    @router.put("/api/admin/limits")
    def put_limits(
        payload: LimitConfigUpdate,
        x_alpha_admin_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(x_alpha_admin_token)
        changes = payload.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(status_code=422, detail="没有可更新的配置项")
        try:
            config = limits.update_config(changes)
        except LimitConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"config": config.as_dict(), "hot_reloaded": True}

    @router.get("/api/admin/usage")
    def get_usage(
        user_id: str | None = Query(default=None),
        day: str | None = Query(default=None),
        limit: int = Query(default=500, ge=1, le=5000),
        x_alpha_admin_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(x_alpha_admin_token)
        target_day = day or utc_day()
        rows = limits.usage.rows(user_id=user_id, day=target_day, limit=limit)
        totals = limits.usage.totals(user_id, target_day) if user_id else None
        return {
            "day": target_day,
            "user_id": user_id,
            "rows": rows,
            "totals": totals,
        }

    return router
