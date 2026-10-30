# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""人格 CRUD 的 HTTP 层（#83）。

薄封装：HTTP 语义（If-Match / 状态码 / 身份头）在这里，业务规则在
`shell/persona_repo.py`（零依赖，可直接单测）。

身份：沿用 `shell/api.py` 的 `x-alpha-user` 头约定；生产环境由
`AuthMiddleware` 注入 `request.state.alpha_user`，二者取其一。
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Header, Request
from fastapi.responses import JSONResponse

from shell.persona_repo import PersonaError, PersonaRepository


def _user_of(request: Request, header_user: str | None) -> str:
    state = getattr(request, "state", None)
    for attr in ("alpha_user", "user_id"):
        value = getattr(state, attr, None) if state else None
        if value:
            return str(value)
    if header_user:
        return header_user
    return "default"


def _parse_if_match(raw: str | None) -> int:
    if not raw:
        raise PersonaError("缺少 If-Match 版本头（用于乐观锁）")
    token = raw.strip()
    if token.startswith("W/"):
        token = token[2:]
    token = token.strip('"')
    try:
        return int(token)
    except ValueError as exc:
        raise PersonaError(f"If-Match 不是合法版本号: {raw!r}") from exc


def build_personas_router(
    repo: PersonaRepository,
    *,
    audit: Callable[[str, dict[str, Any]], None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1/personas", tags=["personas"])

    def _audit(event: str, payload: dict[str, Any]) -> None:
        if audit is not None:
            audit(event, payload)

    def _fail(exc: PersonaError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.code, "message": exc.message},
        )

    @router.get("")
    def list_personas(
        request: Request,
        x_alpha_user: str | None = Header(default=None),
        include_deleted: bool = False,
    ):
        user = _user_of(request, x_alpha_user)
        return {"items": repo.list(user, include_deleted=include_deleted)}

    @router.post("", status_code=201)
    def create_persona(
        request: Request,
        payload: dict[str, Any] = Body(...),
        x_alpha_user: str | None = Header(default=None),
    ):
        user = _user_of(request, x_alpha_user)
        try:
            created = repo.create(user, payload, source="manual")
        except PersonaError as exc:
            return _fail(exc)
        _audit("persona.create", {"user": user, **created})
        return created

    @router.get("/{persona_id}")
    def get_persona(
        persona_id: str,
        request: Request,
        x_alpha_user: str | None = Header(default=None),
    ):
        user = _user_of(request, x_alpha_user)
        try:
            return repo.get(user, persona_id)
        except PersonaError as exc:
            return _fail(exc)

    @router.put("/{persona_id}")
    def replace_persona(
        persona_id: str,
        request: Request,
        payload: dict[str, Any] = Body(...),
        if_match: str | None = Header(default=None, alias="If-Match"),
        x_alpha_user: str | None = Header(default=None),
    ):
        user = _user_of(request, x_alpha_user)
        try:
            version = _parse_if_match(if_match)
            updated = repo.replace(user, persona_id, payload, version)
        except PersonaError as exc:
            return _fail(exc)
        _audit("persona.update", {"user": user, **updated})
        return updated

    @router.patch("/{persona_id}")
    def patch_persona(
        persona_id: str,
        request: Request,
        changes: dict[str, Any] = Body(...),
        if_match: str | None = Header(default=None, alias="If-Match"),
        x_alpha_user: str | None = Header(default=None),
    ):
        user = _user_of(request, x_alpha_user)
        try:
            version = _parse_if_match(if_match)
            updated = repo.patch(user, persona_id, changes, version)
        except PersonaError as exc:
            return _fail(exc)
        _audit("persona.update", {"user": user, **updated})
        return updated

    @router.delete("/{persona_id}")
    def delete_persona(
        persona_id: str,
        request: Request,
        x_alpha_user: str | None = Header(default=None),
    ):
        user = _user_of(request, x_alpha_user)
        try:
            result = repo.delete(user, persona_id)
        except PersonaError as exc:
            return _fail(exc)
        _audit("persona.delete", {"user": user, **result})
        return result

    @router.post("/{persona_id}/restore")
    def restore_persona(
        persona_id: str,
        request: Request,
        x_alpha_user: str | None = Header(default=None),
    ):
        user = _user_of(request, x_alpha_user)
        try:
            result = repo.restore(user, persona_id)
        except PersonaError as exc:
            return _fail(exc)
        _audit("persona.restore", {"user": user, **result})
        return result

    @router.post("/{persona_id}/fork", status_code=201)
    def fork_persona(
        persona_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        x_alpha_user: str | None = Header(default=None),
    ):
        user = _user_of(request, x_alpha_user)
        new_id = body.get("persona_id") if isinstance(body, dict) else None
        try:
            created = repo.fork(user, persona_id, new_id=new_id)
        except PersonaError as exc:
            return _fail(exc)
        _audit("persona.fork", {"user": user, "from": persona_id, **created})
        return created

    return router


__all__ = ["build_personas_router"]
