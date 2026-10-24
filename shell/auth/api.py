# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""认证 HTTP API。"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from .service import AuthError, AuthService


class RegisterIn(BaseModel):
    email: str
    password: str
    invite_code: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str | None = None


def _raise(exc: AuthError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail=str(exc),
        headers={"X-Alpha-Error": exc.code},
    )


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None


def build_auth_router(service: AuthService) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/config")
    def config() -> dict:
        return {
            "invite_required": service.require_invite,
            "methods": ["email_password"],
            "phone_login": False,
        }

    @router.post("/register", status_code=201)
    def register(payload: RegisterIn) -> dict:
        try:
            return service.register(
                payload.email,
                payload.password,
                payload.invite_code,
            ).as_dict()
        except AuthError as exc:
            _raise(exc)

    @router.post("/login")
    def login(payload: LoginIn) -> dict:
        try:
            return service.login(payload.email, payload.password).as_dict()
        except AuthError as exc:
            _raise(exc)

    @router.post("/refresh")
    def refresh(payload: RefreshIn) -> dict:
        try:
            return service.refresh(payload.refresh_token).as_dict()
        except AuthError as exc:
            _raise(exc)

    @router.post("/logout")
    def logout(
        payload: LogoutIn | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict:
        access = _bearer(authorization)
        refresh_token = payload.refresh_token if payload else None
        service.logout(access_token=access, refresh_token=refresh_token)
        return {"ok": True}

    @router.get("/me")
    def me(request: Request) -> dict:
        user_id = getattr(request.state, "user_id", None)
        user = service.get_user(user_id) if user_id else None
        if user is None:
            raise HTTPException(
                status_code=401,
                detail="未登录",
                headers={"X-Alpha-Error": "unauthorized"},
            )
        return {"user": user.as_public()}

    return router
