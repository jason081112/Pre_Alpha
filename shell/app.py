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
from shell.auth.scope import (  # noqa: E402
    ScopedChatLog,
    current_auth_user,
    prepare_session_id,
)
from shell.auth.service import service_from_env  # noqa: E402
from shell.auth.store import AuthStore  # noqa: E402
from shell.crypto import SecretBox  # noqa: E402
from shell.custom import CustomProviderStore  # noqa: E402
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
routing_client = RoutingLLMClient(registry=registry, custom_store=custom_store)

app = upstream.app
app.include_router(build_router(registry, store, custom_store))
app.include_router(build_auth_router(auth_service))

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


@app.exception_handler(RouteError)
async def _route_error_handler(request, exc: RouteError) -> JSONResponse:
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
    return JSONResponse(
        status_code=409,
        content={
            "error": "route_not_configured",
            "action": "open_model_picker",
            "detail": str(exc),
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


def _user_of(scope: dict) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"x-alpha-user":
            user = value.decode("latin-1").strip()
            if user:
                return user
    return "local"


class AlphaMiddleware:
    """按请求解析路由，注入 contextvar；为 /api/chat 补充可观测字段。"""

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        user = _user_of(scope)
        record = store.get(user)
        route = record.route if record else DEV_ROUTE
        fallback = record.fallback if record else ()

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
# 后加入的中间件在最外层：先认证，再进入路由/会话层
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("ALPHA_HOST", "0.0.0.0"),
        port=int(os.getenv("ALPHA_PORT", "8000")),
    )
