#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""P1-67 真实 HTTP transport 冒烟：限流、热更新、平台配额、BYOK 与审计。

依赖（只在 CI/开发机安装）：
    pip install fastapi uvicorn httpx pydantic

运行：
    python scripts/dev/verify_limits_http.py
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import httpx
    import uvicorn
    from fastapi import FastAPI, Request
except ModuleNotFoundError as exc:  # pragma: no cover - 本地默认不装依赖
    print(f"SKIP: 缺少运行时依赖 ({exc.name})，请在 CI/开发机安装 fastapi uvicorn httpx")
    raise SystemExit(2)

from shell.limits import (  # noqa: E402
    LimitConfig,
    LimitsMiddleware,
    LimitsService,
)
from shell.limits_api import build_limits_admin_router  # noqa: E402
from shell.providers import Route  # noqa: E402

ADMIN_TOKEN = "limits-smoke-admin"


class DevAuthMiddleware:
    """仅用于冒烟：从 x-alpha-user 注入 scope.state。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        user = None
        for key, value in scope.get("headers") or []:
            if key.lower() == b"x-alpha-user":
                user = value.decode("latin-1").strip() or None
                break
        state = dict(scope.get("state") or {})
        if user:
            state["user_id"] = user
        else:
            state.pop("user_id", None)
        await self.app({**scope, "state": state}, receive, send)


def build_app(limits: LimitsService) -> FastAPI:
    app = FastAPI()

    def chain_for(user: str) -> tuple[Route, ...]:
        if user == "bob":
            return (Route("custom-bob", "mock"),)
        return (Route("openai", "gpt-smoke"),)

    @app.post("/api/chat")
    async def chat(request: Request) -> dict:
        user = request.headers.get("x-alpha-user", "local")
        limits.record_usage(
            user,
            Route("openai", "gpt-smoke"),
            {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        )
        return {"ok": True}

    @app.post("/api/auth/login")
    async def login() -> dict:
        return {"ok": True}

    app.include_router(build_limits_admin_router(limits))
    app.add_middleware(
        LimitsMiddleware,
        limits=limits,
        route_lookup=chain_for,
    )
    app.add_middleware(DevAuthMiddleware)
    return app


def wait_started(server: uvicorn.Server, timeout: float = 10.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.started:
            sockets = getattr(server.servers[0], "sockets", None) or []
            if sockets:
                return int(sockets[0].getsockname()[1])
        time.sleep(0.05)
    raise RuntimeError("uvicorn 未能启动")


def expect(response: httpx.Response, status: int, label: str) -> httpx.Response:
    if response.status_code != status:
        raise AssertionError(
            f"{label}: 期望 {status}，实际 {response.status_code}: {response.text}"
        )
    return response


def main() -> int:
    os.environ["ALPHA_ADMIN_TOKEN"] = ADMIN_TOKEN
    data_dir = Path(tempfile.mkdtemp(prefix="alpha-limits-smoke-"))
    limits = LimitsService(
        data_dir / "limits.db",
        defaults=LimitConfig(
            user_rpm=0,
            user_rpd=0,
            ip_rpm=0,
            ip_rpd=0,
            platform_daily_tokens=0,
            probe_per_hour=10,
            probe_concurrency=2,
        ),
    )
    app = build_app(limits)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    port = wait_started(server)
    base_url = f"http://127.0.0.1:{port}"
    admin = {"X-Alpha-Admin-Token": ADMIN_TOKEN}
    passed: list[str] = []

    try:
        with httpx.Client(base_url=base_url, timeout=5.0) as client:
            # 1) 配置读取
            response = expect(client.get("/api/admin/limits", headers=admin), 200, "读取配置")
            assert response.json()["config"]["user_rpm"] == 0
            passed.append("admin config readable")

            # 2) IP 维度 + 429 + Retry-After
            expect(
                client.put(
                    "/api/admin/limits",
                    headers=admin,
                    json={
                        "ip_rpm": 2,
                        "ip_rpd": 1000,
                        "user_rpm": 0,
                        "user_rpd": 0,
                        "platform_daily_tokens": 0,
                    },
                ),
                200,
                "热更新 IP 配置",
            )
            expect(client.post("/api/auth/login", json={}), 200, "login #1")
            expect(client.post("/api/auth/login", json={}), 200, "login #2")
            blocked = expect(client.post("/api/auth/login", json={}), 429, "login #3")
            assert "retry-after" in blocked.headers
            assert blocked.json()["code"] == "rate_limited"
            passed.append("IP RPM -> 429 + Retry-After")

            # 3) 用户维度 + 热更新；每个成功 chat 写入 5 platform tokens
            expect(
                client.put(
                    "/api/admin/limits",
                    headers=admin,
                    json={
                        "ip_rpm": 1000,
                        "ip_rpd": 1000,
                        "user_rpm": 2,
                        "user_rpd": 1000,
                        "platform_daily_tokens": 0,
                    },
                ),
                200,
                "热更新用户配置",
            )
            alice = {"x-alpha-user": "alice"}
            expect(client.post("/api/chat", headers=alice), 200, "alice chat #1")
            expect(client.post("/api/chat", headers=alice), 200, "alice chat #2")
            blocked = expect(client.post("/api/chat", headers=alice), 429, "alice chat #3")
            assert blocked.json()["code"] == "rate_limited"
            assert "retry-after" in blocked.headers
            passed.append("user RPM -> 429")

            # 4) 并发压测：同一用户 6 个并发请求，只允许 2 个
            def burst(_index: int) -> int:
                return httpx.post(
                    f"{base_url}/api/chat",
                    headers={"x-alpha-user": "carol"},
                    timeout=5.0,
                ).status_code

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                statuses = list(pool.map(burst, range(6)))
            assert statuses.count(200) == 2, statuses
            assert statuses.count(429) == 4, statuses
            passed.append("concurrent burst: 2 pass / 4 blocked")

            # 5) 审计能看到每日 token 数，且没有原文
            usage = expect(
                client.get(
                    "/api/admin/usage",
                    headers=admin,
                    params={"user_id": "alice"},
                ),
                200,
                "审计查询",
            ).json()
            assert usage["totals"]["kinds"]["platform"]["total_tokens"] == 10
            assert usage["totals"]["total_tokens"] == 10
            assert usage["rows"]
            for row in usage["rows"]:
                assert "content" not in row and "message" not in row
            passed.append("daily token audit")

            # 6) 平台配额热更新后立即生效；BYOK 不扣平台额度
            expect(
                client.put(
                    "/api/admin/limits",
                    headers=admin,
                    json={"user_rpm": 100, "platform_daily_tokens": 5},
                ),
                200,
                "热更新平台配额",
            )
            quota = expect(client.post("/api/chat", headers=alice), 429, "平台配额")
            assert quota.json()["code"] == "quota_exceeded"
            assert "retry-after" in quota.headers
            expect(
                client.post("/api/chat", headers={"x-alpha-user": "bob"}),
                200,
                "bob BYOK",
            )
            passed.append("platform quota blocks, BYOK passes")

            # 7) 配置热更新无需重启：再次读取就是新值
            current = expect(
                client.get("/api/admin/limits", headers=admin), 200, "再次读取配置"
            ).json()["config"]
            assert current["platform_daily_tokens"] == 5
            assert current["user_rpm"] == 100
            passed.append("hot reload without restart")

        print(
            json.dumps(
                {"ok": True, "checks": passed, "port": port},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        limits.close()


if __name__ == "__main__":
    raise SystemExit(main())
