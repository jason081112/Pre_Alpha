# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP / WebSocket 认证中间件。

- 未认证访问受保护路径统一 401；
- WS 在握手阶段校验，失败直接 1008 关闭；
- 认证成功后把可信 user_id 写入 scope.state 和 x-alpha-user，
  并设置 current_auth_user 供会话/聊天历史命名空间使用。
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from ..openai_format import error_body, is_openai_path
from .scope import (
    current_auth_scope_enabled,
    current_auth_user,
    validate_session_id,
)
from .service import AuthService

_PROTECTED_EXACT = {
    "/api/chat",
    "/api/chat/history",
    "/api/me",
    "/api/routes/catalog",
    "/api/auth/me",
    "/api/demo/inject",
    "/api/tts",
    "/api/image",
    "/ws/chat",
    "/v1/chat/completions",
}
_PROTECTED_PREFIXES = (
    "/api/me/",
    "/api/chat/history/",
    "/api/session/",
    "/api/routes/",
    "/v1/",
)


def is_protected(path: str) -> bool:
    return path in _PROTECTED_EXACT or path.startswith(_PROTECTED_PREFIXES)


def _header(scope: dict, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1").strip()
    return None


def _bearer_token(scope: dict) -> str | None:
    raw = _header(scope, b"authorization")
    if raw:
        parts = raw.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip() or None
    if scope.get("type") == "websocket":
        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        for key in ("access_token", "token"):
            values = query.get(key)
            if values and values[0].strip():
                return values[0].strip()
    return None


def _dev_user(scope: dict) -> str:
    return _header(scope, b"x-alpha-user") or "local"


def _with_identity(scope: dict, user_id: str) -> dict:
    headers = [
        (key, value)
        for key, value in scope.get("headers") or []
        if key.lower() != b"x-alpha-user"
    ]
    headers.append((b"x-alpha-user", user_id.encode("utf-8")))
    state = dict(scope.get("state") or {})
    state["user_id"] = user_id
    return {**scope, "headers": headers, "state": state}


def _strip_identity(scope: dict) -> dict:
    headers = [
        (key, value)
        for key, value in scope.get("headers") or []
        if key.lower() != b"x-alpha-user"
    ]
    state = dict(scope.get("state") or {})
    state.pop("user_id", None)
    return {**scope, "headers": headers, "state": state}


class AuthMiddleware:
    def __init__(self, asgi_app, service: AuthService, *, required: bool = True):
        self.app = asgi_app
        self.service = service
        self.required = required

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = (scope.get("method") or "GET").upper()
        if scope["type"] == "http" and method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if not self.required:
            user = _dev_user(scope)
            token = current_auth_user.set(user)
            scope_token = current_auth_scope_enabled.set(False)
            try:
                await self.app(_with_identity(scope, user), receive, send)
            finally:
                current_auth_scope_enabled.reset(scope_token)
                current_auth_user.reset(token)
            return

        token = _bearer_token(scope)
        user = self.service.authenticate(token) if token else None

        if is_protected(path):
            if user is None:
                await self._reject(scope, send)
                return
        elif user is None:
            # 公共路径也去掉客户端伪造的身份头
            scope = _strip_identity(scope)

        if user is not None:
            scope = _with_identity(scope, user.id)
            context_value = user.id
            if path.startswith("/api/session/"):
                session_id = path[len("/api/session/") :].split("/", 1)[0]
                try:
                    validate_session_id(session_id, user.id)
                except ValueError:
                    await self._not_found(scope, send)
                    return
        else:
            context_value = "local"

        context_token = current_auth_user.set(context_value)
        scope_token = current_auth_scope_enabled.set(True)
        try:
            await self.app(scope, receive, send)
        finally:
            current_auth_scope_enabled.reset(scope_token)
            current_auth_user.reset(context_token)

    @staticmethod
    async def _not_found(scope: dict, send) -> None:
        body = json.dumps({"detail": "Session not found"}, ensure_ascii=False).encode(
            "utf-8"
        )
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject(scope: dict, send) -> None:
        if scope["type"] == "websocket":
            await send(
                {
                    "type": "websocket.close",
                    "code": 1008,
                    "reason": "unauthorized",
                }
            )
            return
        openai_path = is_openai_path(scope.get("path", ""))
        if openai_path:
            payload = error_body(
                "Invalid API key",
                error_type="invalid_request_error",
                code="invalid_api_key",
            )
        else:
            payload = {"error": "unauthorized", "detail": "未登录或 token 无效"}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"www-authenticate", b"Bearer"),
        ]
        if openai_path:
            headers.append((b"x-alpha-error", b"invalid_api_key"))
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})
