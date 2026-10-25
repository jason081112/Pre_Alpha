# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""路由与 provider 配置 API。"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .custom import CustomProviderStore, assert_public_host
from .outbound import (
    OutboundPolicyError,
    allow_private_providers,
    build_async_client,
    plan_outbound,
)
from .providers import Registry, Route, RouteError
from .store import RoutingStore

MAX_FALLBACK = 4


class RouteIn(BaseModel):
    provider: str
    model: str


class RoutingUpdate(BaseModel):
    provider: str
    model: str
    fallback: list[RouteIn] = Field(default_factory=list)


class ProviderCreate(BaseModel):
    label: str
    base_url: str
    api_key: str
    models: list[str] = Field(default_factory=list)
    caps: list[str] = Field(default_factory=lambda: ["zh"])
    probe: bool = True


class ProviderUpdate(BaseModel):
    label: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    models: list[str] | None = None
    caps: list[str] | None = None
    probe: bool = True


async def probe_provider(base_url: str, api_key: str, timeout: float = 5.0) -> tuple[bool, str]:
    """最小连通性检查；/models 不通不阻塞保存，鉴权失败才拒绝。

    探活与真实请求共用 `plan_outbound()`：先解析并固定 IP，再发请求；
    不跟随重定向，避免 3xx 把探活带到内网。
    """
    try:
        target = plan_outbound(base_url, allow_private=allow_private_providers())
        async with build_async_client(target, timeout=timeout) as client:
            response = await client.get(
                base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except OutboundPolicyError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败: {type(exc).__name__}"

    if response.status_code in (301, 302, 303, 307, 308):
        return False, "provider 返回重定向，已拒绝"
    if response.status_code in (401, 403):
        return False, f"鉴权失败 (HTTP {response.status_code})"
    return True, f"HTTP {response.status_code}"


def build_router(
    registry: Registry,
    store: RoutingStore,
    custom_store: CustomProviderStore | None = None,
    probe=probe_provider,
) -> APIRouter:
    router = APIRouter()

    def user_of(value: str | None) -> str:
        user = (value or "local").strip()
        return user or "local"

    def merged_registry(user: str) -> Registry:
        specs = {spec.id: spec for spec in registry.specs()}
        if custom_store is not None:
            for provider in custom_store.list(user):
                specs[provider.id] = provider.to_spec()
        return Registry(specs)

    def custom_map(user: str) -> dict:
        if custom_store is None:
            return {}
        return {provider.id: provider for provider in custom_store.list(user)}

    # ── 目录 ──────────────────────────────────────────────
    @router.get("/api/routes/catalog")
    def catalog(x_alpha_user: str | None = Header(default=None)) -> dict:
        user = user_of(x_alpha_user)
        extras = custom_map(user)
        providers = []
        for item in merged_registry(user).catalog():
            custom = extras.get(item["id"])
            if custom is not None:
                item = {**item, **custom.to_public()}
            providers.append(item)
        return {"providers": providers}

    # ── 当前模型 ──────────────────────────────────────────
    @router.get("/api/me/routing")
    def get_routing(x_alpha_user: str | None = Header(default=None)) -> dict:
        user = user_of(x_alpha_user)
        record = store.get(user)
        if record is None:
            return {
                "user_id": user,
                "configured": False,
                "route": None,
                "fallback": [],
            }
        return {
            "user_id": user,
            "configured": True,
            "route": record.route.as_dict(),
            "route_key": record.route.as_str(),
            "fallback": [r.as_dict() for r in record.fallback],
            "updated_at": record.updated_at,
        }

    @router.put("/api/me/routing")
    def put_routing(
        payload: RoutingUpdate,
        x_alpha_user: str | None = Header(default=None),
    ) -> dict:
        user = user_of(x_alpha_user)
        available = merged_registry(user)
        custom = custom_map(user)
        try:
            primary = available.resolve(payload.provider, payload.model)
        except RouteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        primary_custom = custom.get(payload.provider)
        if primary_custom is not None and not primary_custom.has_key:
            raise HTTPException(
                status_code=409,
                detail=f"provider {payload.provider} 的 Key 不可用，请先重新配置",
            )

        if len(payload.fallback) > MAX_FALLBACK:
            raise HTTPException(
                status_code=422,
                detail=f"回退链最多 {MAX_FALLBACK} 跳，收到 {len(payload.fallback)}",
            )

        fallback: list[Route] = []
        for item in payload.fallback:
            try:
                route = available.resolve(item.provider, item.model)
            except RouteError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            fallback_custom = custom.get(item.provider)
            if fallback_custom is not None and not fallback_custom.has_key:
                raise HTTPException(
                    status_code=409,
                    detail=f"provider {item.provider} 的 Key 不可用，请先重新配置",
                )
            if route == primary:
                raise HTTPException(status_code=422, detail=f"回退链不能包含当前模型: {route.as_str()}")
            if route in fallback:
                raise HTTPException(status_code=422, detail=f"回退链重复: {route.as_str()}")
            fallback.append(route)

        record = store.put(user, primary, fallback)
        return {
            "user_id": user,
            "configured": True,
            "route": record.route.as_dict(),
            "route_key": record.route.as_str(),
            "fallback": [r.as_dict() for r in record.fallback],
            "updated_at": record.updated_at,
        }

    @router.delete("/api/me/routing")
    def delete_routing(x_alpha_user: str | None = Header(default=None)) -> dict:
        user = user_of(x_alpha_user)
        return {"user_id": user, "removed": store.delete(user)}

    # ── 自定义 provider ───────────────────────────────────
    @router.get("/api/me/providers")
    def list_providers(x_alpha_user: str | None = Header(default=None)) -> dict:
        if custom_store is None:
            return {"providers": []}
        user = user_of(x_alpha_user)
        return {"providers": [p.to_public() for p in custom_store.list(user)]}

    @router.post("/api/me/providers")
    async def create_provider(
        payload: ProviderCreate,
        x_alpha_user: str | None = Header(default=None),
    ) -> dict:
        if custom_store is None:
            raise HTTPException(status_code=501, detail="自定义 provider 未启用")
        user = user_of(x_alpha_user)
        allow_private = _allow_private()
        try:
            assert_public_host(payload.base_url, allow_private=allow_private)
            if payload.probe:
                ok, message = await probe(payload.base_url, payload.api_key)
                if not ok:
                    raise HTTPException(status_code=422, detail=message)
            provider = custom_store.add(
                user_id=user,
                label=payload.label,
                base_url=payload.base_url,
                api_key=payload.api_key,
                models=payload.models,
                caps=payload.caps,
                allow_private=allow_private,
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return provider.to_public()

    @router.patch("/api/me/providers/{provider_id}")
    async def update_provider(
        provider_id: str,
        payload: ProviderUpdate,
        x_alpha_user: str | None = Header(default=None),
    ) -> dict:
        if custom_store is None:
            raise HTTPException(status_code=501, detail="自定义 provider 未启用")
        user = user_of(x_alpha_user)
        existing = custom_store.get(provider_id, user, decrypt=True)
        if existing is None:
            raise HTTPException(status_code=404, detail="provider 不存在")

        provided = {
            "label": payload.label,
            "base_url": payload.base_url,
            "api_key": payload.api_key,
            "models": payload.models,
            "caps": payload.caps,
        }
        changes = {key: value for key, value in provided.items() if value is not None}
        if not changes:
            raise HTTPException(status_code=422, detail="没有可更新的字段")
        if "api_key" in changes and not str(changes["api_key"]).strip():
            raise HTTPException(
                status_code=422, detail="api_key 不能为空；撤销请调用 revoke"
            )

        allow_private = _allow_private()
        target_url = str(changes.get("base_url") or existing.base_url)
        if "base_url" in changes:
            try:
                assert_public_host(target_url, allow_private=allow_private)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        # 不让正在使用的模型被悄悄从清单里删掉
        if "models" in changes and changes["models"]:
            record = store.get(user)
            active = [record.route, *record.fallback] if record else []
            blocked = [
                route
                for route in active
                if route.provider == provider_id
                and route.model not in changes["models"]
            ]
            if blocked:
                raise HTTPException(
                    status_code=409,
                    detail=f"模型仍在使用中，请先切换: {blocked[0].as_str()}",
                )

        if payload.probe and ("api_key" in changes or "base_url" in changes):
            probe_key = str(changes.get("api_key") or existing.api_key)
            if not probe_key:
                raise HTTPException(
                    status_code=422,
                    detail="Key 已撤销，请提供新 Key 或设置 probe=false",
                )
            ok, message = await probe(target_url, probe_key)
            if not ok:
                raise HTTPException(status_code=422, detail=message)

        try:
            provider = custom_store.update(
                provider_id,
                user,
                label=changes.get("label"),
                base_url=changes.get("base_url"),
                api_key=changes.get("api_key"),
                models=changes.get("models"),
                caps=changes.get("caps"),
                allow_private=allow_private,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if provider is None:
            raise HTTPException(status_code=404, detail="provider 不存在")
        return provider.to_public()

    @router.post("/api/me/providers/{provider_id}/revoke")
    def revoke_provider(
        provider_id: str,
        x_alpha_user: str | None = Header(default=None),
    ) -> dict:
        if custom_store is None:
            raise HTTPException(status_code=501, detail="自定义 provider 未启用")
        provider = custom_store.revoke(provider_id, user_of(x_alpha_user))
        if provider is None:
            raise HTTPException(status_code=404, detail="provider 不存在")
        return provider.to_public()

    @router.get("/api/me/providers/{provider_id}/audit")
    def provider_audit(
        provider_id: str,
        x_alpha_user: str | None = Header(default=None),
    ) -> dict:
        if custom_store is None:
            raise HTTPException(status_code=501, detail="自定义 provider 未启用")
        user = user_of(x_alpha_user)
        if custom_store.get(provider_id, user) is None:
            raise HTTPException(status_code=404, detail="provider 不存在")
        return {
            "provider_id": provider_id,
            "events": custom_store.list_audit(provider_id, user),
        }

    @router.delete("/api/me/providers/{provider_id}")
    def delete_provider(
        provider_id: str,
        x_alpha_user: str | None = Header(default=None),
    ) -> dict:
        if custom_store is None:
            raise HTTPException(status_code=501, detail="自定义 provider 未启用")
        user = user_of(x_alpha_user)
        record = store.get(user)
        if record is not None and (
            record.route.provider == provider_id
            or any(r.provider == provider_id for r in record.fallback)
        ):
            raise HTTPException(
                status_code=409,
                detail="该 provider 仍在使用中，请先切换当前模型或从回退链移除",
            )
        return {"removed": custom_store.delete(provider_id, user)}

    return router


def _allow_private() -> bool:
    return allow_private_providers()
