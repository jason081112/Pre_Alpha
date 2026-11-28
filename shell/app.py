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

import asyncio
import json
import os
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "OpenHer"

if not VENDOR.is_dir():
    raise RuntimeError(f"找不到上游代码: {VENDOR}")

# 上游用 load_dotenv() 相对 cwd 找 .env，因此先切到 vendor 目录
os.chdir(VENDOR)
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))
# chdir 之后仓库根可能不在 sys.path 里（取决于怎么启动），
# 而 shell.persona_repo 要 import bridge.*。缺了会报 ModuleNotFoundError: bridge。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── 启动解耦（#92 尾巴）──────────────────────────────────────────
# 上游 startup 会**无条件**创建一次默认 LLM 客户端（providers/registry.get_llm("openai")
# 读 OPENAI_API_KEY），缺失直接 raise 退出。但那个客户端随即被 RoutingLLMClient 替换，
# 这次创建只是流程占位 —— 不该因为没 key 就起不来。
# 没有 key 时注入占位符：服务能起、能看状态；真正缺 key 会在第一次调用模型时报错。
if not os.environ.get("OPENAI_API_KEY", "").strip():
    os.environ["OPENAI_API_KEY"] = "alpha-placeholder-startup-only"
    _KEY_PLACEHOLDER = True
else:
    _KEY_PLACEHOLDER = False

import main as upstream  # noqa: E402  （必须在 chdir 之后）

from fastapi import Response  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

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
from shell.content_safety import PromptSiftClient  # noqa: E402
from shell.consent import build_consent_router, has_consent  # noqa: E402
from shell.crypto import SecretBox  # noqa: E402
from shell.custom import CustomProviderStore  # noqa: E402
from shell.limits import (  # noqa: E402
    LimitsMiddleware,
    LimitsService,
    RateLimitExceeded,
)
from shell.billing import BillingStore, build_billing_router  # noqa: E402
from shell.limits_api import build_limits_admin_router  # noqa: E402
from shell.moderation import ModerationStore, build_moderation_router  # noqa: E402
from shell.account import (  # noqa: E402
    DeletionStore,
    build_account_router,
    purge_user,
    run_due,
)
from shell.persona_repo import PersonaRepository  # noqa: E402
from shell.personas_api import build_personas_router  # noqa: E402
from shell.openai_api import (  # noqa: E402
    build_openai_router,
    install_openai_error_handlers,
)
from shell.openai_format import error_body, is_openai_path  # noqa: E402
from shell.providers import Registry, Route, RouteError  # noqa: E402
from shell.seams import check_seams  # noqa: E402
from shell.routing import (  # noqa: E402
    AllProvidersFailed,
    RouteNotConfigured,
    RoutingLLMClient,
    attempts as routing_attempts,
    last_usage,
    route_from_env,
    route_used,
    routing_context,
)
from shell.store import RoutingStore  # noqa: E402
from shell.export_api import build_export_router  # noqa: E402
from shell.ui import build_ui_router  # noqa: E402
from shell.waffo import from_env as waffo_from_env  # noqa: E402
from shell.ws_stream import build_stream_ws_router  # noqa: E402

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
# #100 违规处置：举报 / 封禁 / 额度清零 / 审计
moderation = ModerationStore(
    Path(os.getenv("ALPHA_MODERATION_DB", str(DATA_DIR / "moderation.db")))
)
# #102 订阅与试用：SQLite 记试用轮数与订阅状态；Waffo 配置缺失时订阅接口 503
billing = BillingStore(
    Path(os.getenv("ALPHA_BILLING_DB", str(DATA_DIR / "billing.db")))
)
# #75 账号注销：冷静期登记 + 到期级联清除 + 回执
deletion = DeletionStore(
    Path(os.getenv("ALPHA_DELETION_DB", str(DATA_DIR / "account.db")))
)
DELETE_COOLING_DAYS = float(os.getenv("ALPHA_DELETE_COOLING_DAYS", "7"))
ENGINE_DATA = VENDOR / ".data"
try:
    waffo = waffo_from_env()
except Exception as exc:  # noqa: BLE001
    raise RuntimeError(f"Waffo 配置错误: {exc}") from exc
print("[billing] Waffo Pancake: " + ("enabled" if waffo is not None else "off"))
try:
    prompt_sift = PromptSiftClient.from_env()
except Exception as exc:  # noqa: BLE001 - 配置错误必须显式失败
    raise RuntimeError(f"Waffo Prompt Sift 配置错误: {exc}") from exc
print(
    "[safety] Waffo Prompt Sift: "
    + ("enabled" if prompt_sift is not None else "off")
)
routing_client = RoutingLLMClient(
    registry=registry,
    custom_store=custom_store,
    usage_hook=limits.record_usage,
)

app = upstream.app

# ── 上游危险端点闸门（#93 / ADR-0002）──────────────────────────────
# app = upstream.app 意味着上游全部路由对外可达。以下端点默认封禁：
#   /api/demo/inject —— 可向任意 client_id 的 WS 推 send_chat，
#                       等于绕过认证冒充用户发言。
# 直接从路由表摘除而不是用中间件拦截：摘掉即 404，没有绕过路径。
_ENABLE_DEMO_INJECT = os.getenv("ALPHA_ENABLE_DEMO_INJECT", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_DENY_PATHS = () if _ENABLE_DEMO_INJECT else ("/api/demo/inject",)

# 接缝自检：默认严格（缺即拒绝启动）；ALPHA_SEAM_STRICT=0 降级为告警。
SEAM_STRICT = os.getenv("ALPHA_SEAM_STRICT", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def _strip_dangerous_routes(application) -> list[str]:
    removed: list[str] = []
    for route in list(application.router.routes):
        if getattr(route, "path", None) in _DENY_PATHS:
            application.router.routes.remove(route)
            removed.append(route.path)
    return removed


_STRIPPED = _strip_dangerous_routes(app)


def _strip_upstream_pages(application, paths: tuple[str, ...]) -> list[str]:
    """首页交给 shell 的聊天页（#46）；上游自带前端不再对外。"""
    removed: list[str] = []
    for route in list(application.router.routes):
        if getattr(route, "path", None) in paths:
            application.router.routes.remove(route)
            removed.append(route.path)
    return removed


_STRIPPED_PAGES = _strip_upstream_pages(app, ("/",))


# ── #78 健康检查 ────────────────────────────────────────────────
_STARTED_AT = time.time()
_ready_cache: dict[str, Any] = {"at": 0.0, "ok": False, "detail": ""}


def _probe_db(path: Path) -> dict:
    if not path.exists():
        return {"ok": False, "error": "missing"}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def _probe_writable(path: Path) -> dict:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".readyz"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def _probe_llm() -> dict:
    """只做 TCP 可达性，不消耗额度；结果缓存 15 秒。"""
    if os.getenv("ALPHA_READY_LLM", "1") == "0":
        return {"ok": True, "skipped": True}
    now = time.time()
    if now - _ready_cache["at"] < 15 and _ready_cache["at"]:
        return {"ok": _ready_cache["ok"], "detail": _ready_cache["detail"], "cached": True}
    route = os.getenv("ALPHA_DEFAULT_ROUTE", "").strip()
    host = ""
    try:
        providers = json.loads((ROOT / "shell" / "providers.json").read_text("utf-8"))["providers"]
        name = route.split("/")[0] if route else ""
        base = (providers.get(name) or {}).get("base_url") or ""
        if base:
            host = urlparse(base).hostname or ""
    except Exception:  # noqa: BLE001
        host = ""
    if not host:
        # 没有可探测的 HTTP 端点（如 claude/gemini 走官方 SDK）：只要路由已配就算通过
        ok = bool(route)
        _ready_cache.update({"at": now, "ok": ok, "detail": route})
        return {"ok": ok, "detail": route or "no default route"}
    try:
        with socket.create_connection((host, 443), timeout=2.5):
            pass
        ok, detail = True, host
    except OSError as exc:
        ok, detail = False, f"{host}: {exc.__class__.__name__}"
    _ready_cache.update({"at": now, "ok": ok, "detail": detail})
    return {"ok": ok, "detail": detail}


@app.get("/healthz")
def healthz() -> dict:
    """进程活着就 200；不碰磁盘和网络，给容器 healthcheck 用。"""
    return {
        "status": "ok",
        "uptime_s": round(time.time() - _STARTED_AT, 1),
        "version": os.getenv("ALPHA_VERSION", "dev"),
    }


@app.get("/readyz")
def readyz(response: Response) -> dict:
    """依赖可用才 200：数据库可读、数据目录可写、默认路由可达。"""
    checks = {
        "auth_db": _probe_db(AUTH_DB_PATH),
        "limits_db": _probe_db(LIMITS_DB_PATH),
        "routing_db": _probe_db(DB_PATH),
        "billing_db": _probe_db(Path(os.getenv("ALPHA_BILLING_DB", str(DATA_DIR / "billing.db")))),
        "data_dir": _probe_writable(DATA_DIR),
        "engine_dir": _probe_writable(VENDOR / ".data"),
        "llm": _probe_llm(),
    }
    ok = all(v.get("ok") for v in checks.values())
    if not ok:
        response.status_code = 503
    return {
        "status": "ok" if ok else "degraded",
        "checks": checks,
        "version": os.getenv("ALPHA_VERSION", "dev"),
    }


app.include_router(build_router(registry, store, custom_store, limits=limits))

# PWA: serve manifest.json and sw.js
@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse(
        ROOT / "site" / "static" / "manifest.json",
        media_type="application/json",
    )

@app.get("/sw.js")
async def serve_sw():
    # SW 必须挂在根路径，否则作用域被限制在子目录
    return FileResponse(
        ROOT / "site" / "static" / "sw.js",
        media_type="text/javascript",
    )

@app.get("/static/{name}")
async def serve_pwa_static(name: str):
    if name not in {"icon-192.png", "icon-512.png"}:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return FileResponse(ROOT / "site" / "static" / name, media_type="image/png")
app.include_router(build_auth_router(auth_service))
app.include_router(build_consent_router(auth_store))
app.include_router(build_page_router())
app.include_router(build_limits_admin_router(limits))
# #100 举报通知：写本地日志 + 可选 webhook（省得只能靠轮询管理端）
def _notify_report(report: dict) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(report.get("created_at") or time.time()))
    line = (
        f"{stamp} 新举报 #{report.get('id')} "
        f"[{report.get('category')}] 用户 {report.get('user_id')} "
        f"对象 {report.get('target') or '-'}：{report.get('detail') or ''}"
    )
    print(line, flush=True)
    log_path = Path(os.getenv("ALPHA_REPORT_LOG", str(DATA_DIR / "reports.log")))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**report, "at": stamp}, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[report] 写日志失败: {exc}", flush=True)
    hook = os.getenv("ALPHA_REPORT_WEBHOOK", "").strip()
    if not hook:
        return

    def post() -> None:
        import urllib.request

        body = json.dumps(
            {
                "text": line,
                "content": line,
                "report": report,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            hook,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=6).read()
        except Exception as exc:  # noqa: BLE001
            print(f"[report] webhook 失败: {exc}", flush=True)

    threading.Thread(target=post, daemon=True).start()


app.include_router(
    build_moderation_router(moderation, limits=limits, notify=_notify_report)
)
app.include_router(
    build_billing_router(
        billing,
        waffo=waffo,
        success_url=(
            os.getenv("ALPHA_PUBLIC_URL", "https://47.239.51.212.sslip.io").rstrip("/")
            + "/?billing=success"
        ),
    )
)

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


def _purge_account(user_id: str) -> dict:
    """#75：把一个用户在 8 个库 + 引擎数据 + 人格目录里的痕迹全清掉。"""
    return purge_user(
        user_id,
        auth_store=auth_store,
        limits=limits,
        moderation=moderation,
        billing=billing,
        custom_store=custom_store,
        agent_extra=agent_extra,
        routes=store,
        chat_db=ENGINE_DATA / "chat.db",
        engine_db=ENGINE_DATA / "openher.db",
        memory_db=ENGINE_DATA / "memory.db",
        persona_root=PERSONA_ROOT,
        backup_dir=os.getenv("ALPHA_BACKUP_DIR") or None,
        backup_retention_days=int(os.getenv("ALPHA_BACKUP_RETENTION_DAYS", "30")),
    )


app.include_router(
    build_account_router(
        deletion,
        _purge_account,
        cooling_days=DELETE_COOLING_DAYS,
        admin_token=os.getenv("ALPHA_ADMIN_TOKEN", ""),
    )
)

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
            "action": "open_byok_setup",
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


# 开发兜底：任何用户都能用（仅本地验证，生产不要设）
DEV_ROUTE = route_from_env("ALPHA_DEV_ROUTE")
# 平台默认路由：用户没配路由时走这条（平台 Key + 每日免费额度）
DEFAULT_ROUTE = route_from_env("ALPHA_DEFAULT_ROUTE")

app.include_router(
    build_openai_router(
        upstream,
        store=store,
        registry=registry,
        custom_store=custom_store,
        dev_route=DEV_ROUTE,
        limits=limits,
        safety=prompt_sift,
        banned_check=moderation.is_banned,
        access_check=billing.can_chat,
        turn_hook=billing.record_turn,
        guard_log=lambda user, action, cats: moderation.record_action(
            user, f"guard_{action}", reason=",".join(cats)
        ),
    )
)
install_openai_error_handlers(app)

# 真流式 WS（#94）：上游 /ws/chat 拿不到中间内容，这条走 shell 自己的解析器
app.include_router(build_stream_ws_router(
    upstream,
    safety=prompt_sift,
    consent_check=lambda user: has_consent(auth_store, user),
    banned_check=moderation.is_banned,
    access_check=billing.can_chat,
    turn_hook=billing.record_turn,
    guard_log=lambda user, action, cats: moderation.record_action(
        user, f"guard_{action}", reason=",".join(cats)
    ),
))

# 聊天页（#46）：单文件 HTML，接 /ws/chat/stream
app.include_router(build_ui_router())

# 静态站点（#76）：隐私政策/AUP/模型披露、定价页、视觉样张。
# 之前只把文件放进了 site/，没有挂载路由，线上 /legal/* 一直是 404。
for _sub in ("legal", "pricing", "glass"):
    _dir = ROOT / "site" / _sub
    if _dir.is_dir():
        app.mount(f"/{_sub}", StaticFiles(directory=str(_dir), html=True), name=f"site-{_sub}")

# 对话导出（#50）：Markdown / JSON
app.include_router(build_export_router(upstream))


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
    fallback = DEV_ROUTE or DEFAULT_ROUTE
    return (fallback,) if fallback is not None else ()


# 需要注入路由上下文的 WS 端点（上游 /ws/chat 与 #94 的流式端点）
_WS_ROUTES = ("/ws/chat", "/ws/chat/stream")


class AlphaMiddleware:
    """按请求解析路由，注入 contextvar；为 /api/chat 补充可观测字段。"""

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        path = scope.get("path", "")
        user = _user_of(scope)
        record = store.get(user)
        route = record.route if record else (DEV_ROUTE or DEFAULT_ROUTE)
        fallback = record.fallback if record else ()

        if kind == "websocket":
            if path in _WS_ROUTES:
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
                "action": "open_byok_setup",
                "detail": f"用户 {user!r} 尚未选择模型，且服务端未配置默认模型",
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

    # ── 接缝自检（#93 / ADR-0002）────────────────────────────
    # 5 个注入点任一失效都会「能起能聊但是不对」，必须启动时就报。
    scoped_log = (
        upstream.chat_log_store
        if isinstance(upstream.chat_log_store, ScopedChatLog)
        else None
    )
    report = check_seams(
        upstream,
        get_or_create_session=_alpha_get_or_create_session,
        persist_agent=_alpha_persist_agent,
        llm_client=routing_client,
        chat_log_store=scoped_log,
    )
    print("✓ 接缝自检:")
    for line in report.lines():
        print(line)
    if not report.ok:
        if SEAM_STRICT:
            raise RuntimeError(report.fail_message())
        print("⚠️  接缝不完整：ALPHA_SEAM_STRICT=0，继续启动（功能可能静默降级）")

    if _STRIPPED:
        print(f"✓ 已封禁上游端点: {', '.join(_STRIPPED)}")
    if _STRIPPED_PAGES:
        print(f"✓ 首页已接管: {', '.join(_STRIPPED_PAGES)} → shell/ui.py")
    if _KEY_PLACEHOLDER:
        print(
            "⚠️  未检测到 OPENAI_API_KEY：已注入启动占位符，服务可起但**调用模型会失败**。\n"
            "   配置方式：export OPENAI_API_KEY=<key>（ollama cloud 也填这个名字），\n"
            "   或运行 scripts/dev/restore_env.sh 从 .secrets/alpha.env 恢复。"
        )
    print(
        f"✓ Alpha 路由已接管: {len(list(registry.specs()))} 个 provider, "
        f"dev_route={DEV_ROUTE or '未设置'}"
    )
    print(
        f"✓ Alpha 认证: {'required' if AUTH_REQUIRED else 'disabled (dev)'}, "
        f"invite_required={auth_service.require_invite}"
    )


@app.on_event("startup")
async def _start_deletion_worker() -> None:
    """#75：每小时检查一次到期注销；启动时先跑一遍。"""
    async def loop() -> None:
        while True:
            try:
                done = await asyncio.to_thread(run_due, deletion, _purge_account)
                for item in done:
                    uid = item.get("user_id", "")
                    if item.get("error"):
                        print(f"[deletion] {uid} 失败: {item['error']}")
                    else:
                        print(f"[deletion] {uid} 已清除")
            except Exception as exc:  # noqa: BLE001
                print(f"[deletion] 任务异常: {exc}")
            await asyncio.sleep(3600)

    asyncio.create_task(loop())


@app.on_event("shutdown")
async def _close_store() -> None:
    store.close()
    custom_store.close()
    auth_store.close()
    limits.close()
    billing.close()
    deletion.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("ALPHA_HOST", "0.0.0.0"),
        port=int(os.getenv("ALPHA_PORT", "8000")),
    )
