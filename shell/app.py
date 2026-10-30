# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Alpha 外壳入口：import 上游 app，挂路由中间件与配置 API。

启动方式（在仓库根目录）：
    python -m shell.app
或：
    uvicorn shell.app:app --host 0.0.0.0 --port 8000

前提：vendor/OpenHer 已安装依赖，且 .env 至少有一个可用 provider 的 Key
（上游 startup 会先创建一次默认客户端，随后被 RoutingLLMClient 替换）。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "OpenHer"

if not VENDOR.is_dir():
    raise RuntimeError(f"找不到上游代码: {VENDOR}")

# 上游用 load_dotenv() 相对 cwd 找 .env，因此先切到 vendor 目录
os.chdir(VENDOR)
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import main as upstream  # noqa: E402  （必须在 chdir 之后）

from fastapi.responses import JSONResponse  # noqa: E402

from shell.agent_state import AgentExtraStore  # noqa: E402
from shell.api import build_router  # noqa: E402
from shell.auth.api import build_auth_router  # noqa: E402
from shell.auth.middleware import AuthMiddleware  # noqa: E402
from shell.auth.page import build_page_router  # noqa: E402
from shell.auth.scope import (  # noqa: E402
    ScopedChatLog,
    current_auth_user,
    prepare_session_id,
)
from shell.auth.service import service_from_env  # noqa: E402
from shell.auth.store import AuthStore  # noqa: E402
from shell.crypto import SecretBox  # noqa: E402
from shell.custom import CustomProviderStore  # noqa: E402
from shell.limits import (  # noqa: E402
    LimitsMiddleware,
    LimitsService,
    RateLimitExceeded,
)
from shell.limits_api import build_limits_admin_router  # noqa: E402
from shell.persona_repo import PersonaRepository  # noqa: E402
from shell.personas_api import build_personas_router  # noqa: E402
from shell.openai_api import (  # noqa: E402
    build_openai_router,
    install_openai_error_handlers,
)
from shell.openai_format import error_body, is_openai_path  # noqa: E402
from shell.providers import Registry, Route, RouteError  # noqa: E402
from shell.routing import (  # noqa: E402
    AllProvidersFailed,
    RouteNotConfigured,
    RoutingLLMClient,
    attempts as routing_attempts,
    last_usage,
    route_used,
    routing_context,
)
from shell.store import RoutingStore  # noqa: E402

DATA_DIR = Path(os.getenv("ALPHA_DATA_DIR", str(ROOT / ".data")))
DB_PATH = Path(os.getenv("ALPHA_ROUTING_DB", str(DATA_DIR / "routing.db")))
AUTH_DB_PATH = Path(os.getenv("ALPHA_AUTH_DB", str(DATA_DIR / "auth.db")))
LIMITS_DB_PATH = Path(os.getenv("ALPHA_LIMITS_DB", str(DATA_DIR / "limits.db")))
AUTH_REQUIRED = os.getenv("ALPHA_AUTH_DISABLED", "").strip().lower() not in {
    "1",
    "true",
    "yes",
    "on",
}

registry = Registry.load()
store = RoutingStore(DB_PATH)
custom_store = CustomProviderStore(DB_PATH, SecretBox.from_env(DATA_DIR))
agent_extra = AgentExtraStore(
    Path(os.getenv("ALPHA_AGENT_EXTRA_DB", str(DATA_DIR / "agent_extra.db")))
)
auth_store = AuthStore(AUTH_DB_PATH)
auth_service = service_from_env(auth_store)
limits = LimitsService(LIMITS_DB_PATH)
routing_client = RoutingLLMClient(
    registry=registry,
    custom_store=custom_store,
    usage_hook=limits.record_usage,
)

app = upstream.app
app.include_router(build_router(registry, store, custom_store, limits=limits))
app.include_router(build_auth_router(auth_service))
app.include_router(build_page_router())
app.include_router(build_limits_admin_router(limits))

# ── 人格 CRUD（#83）──────────────────────────────────────────────
# profile.json 落在 ALPHA_PERSONA_ROOT/<user>/personas/<id>/；
# 同时把渲染出的 SOUL.md 镜像到引擎读取目录，写完即可被加载。
# 按用户隔离与配额的完整实现由 #84 接管。
PERSONA_ROOT = Path(os.getenv("ALPHA_PERSONA_ROOT", str(DATA_DIR / "users")))
PERSONA_MIRROR = Path(
    os.getenv("ALPHA_PERSONA_MIRROR", str(VENDOR / "persona" / "personas"))
)


def _reload_persona(user: str, persona_id: str) -> None:
    """人格变更后重载引擎缓存；失败不阻断写操作。"""
    loader = getattr(upstream, "persona_loader", None)
    if loader is None:
        return
    try:
        loader.reload(persona_id)
    except Exception:  # noqa: BLE001
        pass


persona_repo = PersonaRepository(
    PERSONA_ROOT, mirror_dir=PERSONA_MIRROR, on_change=_reload_persona
)
app.include_router(build_personas_router(persona_repo))

# ── ChatAgent 补充状态：上游没有持久化关系 EMA / turn_count ──
# 不改 vendor，只在运行时包装两个入口：
# - get_or_create_session：新建 Agent 时回填补充状态
# - _persist_agent：每轮落盘后追加保存
_original_get_or_create_session = upstream.get_or_create_session
_original_persist_agent = upstream._persist_agent


def _alpha_get_or_create_session(
    session_id, persona_id, user_name=None, client_id=None
):
    """用户隔离后的 session 入口。

    - 忽略客户端传入的 user_name，统一绑定当前认证用户；
    - session_id 加用户前缀，跨用户复用直接拒绝；
    - 不让上游按 persona 盲目复用 session（client_id 传 None），
      再由外壳按用户命名空间恢复聊天历史。
    """
    user = current_auth_user.get() or "local"
    internal_sid = prepare_session_id(session_id, user)
    sid, agent = _original_get_or_create_session(
        internal_sid,
        persona_id,
        user_name=user,
        client_id=None,
    )

    if client_id and not agent.history and upstream.chat_log_store is not None:
        try:
            rows = upstream.chat_log_store.load_messages(
                client_id=client_id,
                persona_id=persona_id,
                limit=agent.max_history,
            )
            if rows:
                agent.history = [
                    upstream.ChatMessage(role=row["role"], content=row["content"])
                    for row in rows
                ]
                print(f"  ↳ 恢复聊天历史: {len(agent.history)} 条消息")
        except Exception as exc:  # noqa: BLE001
            print(f"  [auth] 历史恢复失败: {type(exc).__name__}")

    try:
        extra = agent_extra.load(agent.user_id, agent.persona.persona_id)
        if extra:
            agent_extra.apply(agent, extra)
    except Exception as exc:  # noqa: BLE001
        print(f"  [agent-extra] 回填失败: {type(exc).__name__}")
    return sid, agent


def _alpha_persist_agent(agent) -> None:
    _original_persist_agent(agent)
    try:
        agent_extra.save(agent)
    except Exception as exc:  # noqa: BLE001
        print(f"  [agent-extra] 落盘失败: {type(exc).__name__}")


upstream.get_or_create_session = _alpha_get_or_create_session
upstream._persist_agent = _alpha_persist_agent


def _openai_error(
    status_code: int,
    message: str,
    *,
    code: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(
            message,
            error_type=(
                "server_error"
                if status_code >= 500
                else (
                    "rate_limit_error"
                    if status_code == 429
                    else "invalid_request_error"
                )
            ),
            code=code,
        ),
        headers={"X-Alpha-Error": code, **(headers or {})},
    )


@app.exception_handler(RouteError)
async def _route_error_handler(request, exc: RouteError) -> JSONResponse:
    if is_openai_path(request.url.path):
        return _openai_error(400, str(exc), code="provider_unavailable")
    return JSONResponse(
        status_code=409,
        content={
            "error": "provider_unavailable",
            "action": "reconfigure_provider",
            "detail": str(exc),
        },
    )


@app.exception_handler(AllProvidersFailed)
async def _all_failed_handler(request, exc: AllProvidersFailed) -> JSONResponse:
    if is_openai_path(request.url.path):
        return _openai_error(
            500,
            "上游模型调用失败，请稍后重试",
            code="provider_unavailable",
        )
    return JSONResponse(
        status_code=502,
        content={
            "error": "all_providers_failed",
            "action": "open_model_picker",
            "detail": str(exc),
            "attempts": exc.attempt_list,
        },
    )


@app.exception_handler(RouteNotConfigured)
async def _not_configured_handler(request, exc: RouteNotConfigured) -> JSONResponse:
    if is_openai_path(request.url.path):
        return _openai_error(400, str(exc), code="route_not_configured")
    return JSONResponse(
        status_code=409,
        content={
            "error": "route_not_configured",
            "action": "open_model_picker",
            "detail": str(exc),
        },
    )


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request, exc: RateLimitExceeded) -> JSONResponse:
    if is_openai_path(request.url.path):
        return _openai_error(
            429,
            exc.detail,
            code=exc.code,
            headers={"Retry-After": str(exc.retry_after)},
        )
    return JSONResponse(
        status_code=429,
        content=exc.as_dict(),
        headers={
            "Retry-After": str(exc.retry_after),
            "X-Alpha-Error": exc.code,
        },
    )


def _dev_route() -> Route | None:
    raw = os.getenv("ALPHA_DEV_ROUTE", "").strip()
    if not raw:
        return None
    try:
        return Route.parse(raw)
    except RouteError as exc:
        print(f"⚠️  ALPHA_DEV_ROUTE 无效: {exc}")
        return None


DEV_ROUTE = _dev_route()

app.include_router(
    build_openai_router(
        upstream,
        store=store,
        registry=registry,
        custom_store=custom_store,
        dev_route=DEV_ROUTE,
        limits=limits,
    )
)
install_openai_error_handlers(app)


def _user_of(scope: dict) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"x-alpha-user":
            user = value.decode("latin-1").strip()
            if user:
                return user
    return "local"


def _route_chain(user: str) -> tuple[Route, ...]:
    record = store.get(user)
    if record is not None:
        return (record.route, *record.fallback)
    return (DEV_ROUTE,) if DEV_ROUTE is not None else ()


class AlphaMiddleware:
    """按请求解析路由，注入 contextvar；为 /api/chat 补充可观测字段。"""

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        path = scope.get("path", "")
        user = _user_of(scope)
        record = store.get(user)
        route = record.route if record else DEV_ROUTE
        fallback = record.fallback if record else ()

        if kind == "websocket":
            if path == "/ws/chat":
                with routing_context(route, fallback, user=user):
                    await self.app(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return
        if kind != "http":
            await self.app(scope, receive, send)
            return

        if path == "/api/chat" and route is None:
            await self._route_not_configured(scope, receive, send, user)
            return

        with routing_context(route, fallback, user=user):
            if path == "/api/chat":
                await self._with_observability(scope, receive, send)
            else:
                await self.app(scope, receive, send)

    @staticmethod
    async def _route_not_configured(scope, receive, send, user: str) -> None:
        from starlette.responses import JSONResponse

        response = JSONResponse(
            status_code=409,
            content={
                "error": "route_not_configured",
                "action": "open_model_picker",
                "detail": f"用户 {user!r} 尚未选择模型",
            },
        )
        await response(scope, receive, send)

    async def _with_observability(self, scope, receive, send) -> None:
        """缓冲 /api/chat 的 JSON 响应，追加 route_used / fallback_attempts / latency_ms。"""
        started = time.monotonic()
        start_message: dict | None = None
        chunks: list[bytes] = []

        async def send_wrapper(message: dict) -> None:
            nonlocal start_message

            if message["type"] == "http.response.start":
                start_message = message
                return

            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return

                assert start_message is not None
                body = b"".join(chunks)
                headers = [
                    (k, v)
                    for k, v in start_message.get("headers", [])
                    if k.lower() != b"content-length"
                ]

                if start_message["status"] == 200:
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeDecodeError):
                        payload = None
                    if isinstance(payload, dict):
                        used = route_used.get()
                        if used is not None:
                            payload["route_used"] = used.as_str()
                            headers.append((b"x-alpha-route", used.as_str().encode()))
                        payload["fallback_attempts"] = list(routing_attempts.get())
                        payload["latency_ms"] = int((time.monotonic() - started) * 1000)
                        usage = last_usage.get()
                        if usage:
                            payload["usage"] = usage
                        body = json.dumps(payload, ensure_ascii=False).encode()

                headers.append((b"content-length", str(len(body)).encode()))
                await send({**start_message, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(AlphaMiddleware)
# 后加入的中间件在最外层：认证 -> 限流 -> 路由/会话层
app.add_middleware(
    LimitsMiddleware,
    limits=limits,
    route_lookup=_route_chain,
)
app.add_middleware(
    AuthMiddleware,
    service=auth_service,
    required=AUTH_REQUIRED,
)


@app.on_event("startup")
async def _install_routing_client() -> None:
    """上游 startup 创建完全局 llm_client 之后执行，替换为路由门面。"""
    upstream.llm_client = routing_client
    if upstream.chat_log_store is not None and not isinstance(
        upstream.chat_log_store, ScopedChatLog
    ):
        upstream.chat_log_store = ScopedChatLog(upstream.chat_log_store)
    print(
        f"✓ Alpha 路由已接管: {len(list(registry.specs()))} 个 provider, "
        f"dev_route={DEV_ROUTE or '未设置'}"
    )
    print(
        f"✓ Alpha 认证: {'required' if AUTH_REQUIRED else 'disabled (dev)'}, "
        f"invite_required={auth_service.require_invite}"
    )


@app.on_event("shutdown")
async def _close_store() -> None:
    store.close()
    custom_store.close()
    auth_store.close()
    limits.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("ALPHA_HOST", "0.0.0.0"),
        port=int(os.getenv("ALPHA_PORT", "8000")),
    )
