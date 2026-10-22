"""路由配置 API：目录、当前路由读写。"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

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


def build_router(registry: Registry, store: RoutingStore) -> APIRouter:
    router = APIRouter()

    def user_of(value: str | None) -> str:
        user = (value or "local").strip()
        return user or "local"

    @router.get("/api/routes/catalog")
    def catalog() -> dict:
        return {"providers": registry.catalog()}

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
        try:
            primary = registry.resolve(payload.provider, payload.model)
        except RouteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if len(payload.fallback) > MAX_FALLBACK:
            raise HTTPException(
                status_code=422,
                detail=f"回退链最多 {MAX_FALLBACK} 跳，收到 {len(payload.fallback)}",
            )

        fallback: list[Route] = []
        for item in payload.fallback:
            try:
                route = registry.resolve(item.provider, item.model)
            except RouteError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        removed = store.delete(user)
        return {"user_id": user, "removed": removed}

    return router
