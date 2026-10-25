"""P1-67 限流、配额与 token 审计的零依赖测试。

直接运行：
    python3 tests/test_limits.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.limits import (  # noqa: E402
    Bucket,
    LimitConfig,
    LimitConfigError,
    LimitConfigStore,
    LimitsMiddleware,
    LimitsService,
    RateLimitExceeded,
    RateLimitStore,
    UsageStore,
    client_ip,
    seconds_until_utc_midnight,
)
from shell.providers import Route  # noqa: E402


def _db() -> Path:
    return Path(tempfile.mkdtemp(prefix="alpha-limits-")) / "limits.db"


def _service(db: Path, **overrides) -> LimitsService:
    defaults = {
        "user_rpm": 0,
        "user_rpd": 0,
        "ip_rpm": 0,
        "ip_rpd": 0,
        "platform_daily_tokens": 0,
        "probe_per_hour": 10,
        "probe_concurrency": 2,
        "probe_backoff_base": 2,
        "probe_backoff_max": 300,
    }
    defaults.update(overrides)
    return LimitsService(db, defaults=LimitConfig(**defaults), clock=lambda: 1000.0)


def test_config_env_and_validation() -> None:
    keys = {
        "ALPHA_USER_RPM": os.environ.get("ALPHA_USER_RPM"),
        "ALPHA_USER_RPD": os.environ.get("ALPHA_USER_RPD"),
        "ALPHA_PLATFORM_DAILY_TOKENS": os.environ.get("ALPHA_PLATFORM_DAILY_TOKENS"),
    }
    try:
        os.environ["ALPHA_USER_RPM"] = "7"
        os.environ["ALPHA_USER_RPD"] = "70"
        os.environ["ALPHA_PLATFORM_DAILY_TOKENS"] = "7000"
        config = LimitConfig.from_env()
        assert config.user_rpm == 7
        assert config.user_rpd == 70
        assert config.platform_daily_tokens == 7000
    finally:
        for key, value in keys.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    for bad in (
        {"user_rpm": -1},
        {"probe_backoff_base": 10, "probe_backoff_max": 5},
    ):
        try:
            LimitConfig(**bad).validated()
        except LimitConfigError:
            pass
        else:
            raise AssertionError(f"应拒绝非法配置: {bad}")


def test_config_hot_reload_across_instances() -> None:
    db = _db()
    first = LimitConfigStore(db, defaults=LimitConfig(), ttl=0)
    second = LimitConfigStore(db, defaults=LimitConfig(), ttl=0)
    try:
        assert first.get().user_rpm == 60
        first.update({"user_rpm": 3, "platform_daily_tokens": 30})
        assert first.get().user_rpm == 3
        # 模拟另一个 worker 进程：不重启，下一轮 get 读到新值。
        assert second.get().user_rpm == 3
        assert second.get().platform_daily_tokens == 30
        for bad in ({"user_rpm": -1}, {"no_such_key": 1}):
            try:
                first.update(bad)
            except LimitConfigError:
                pass
            else:
                raise AssertionError(f"应拒绝配置: {bad}")
    finally:
        first.close()
        second.close()


def test_rate_limit_fixed_window() -> None:
    now = [1000.0]
    store = RateLimitStore(_db(), clock=lambda: now[0])
    try:
        bucket = Bucket("user_rpm", "u:alice:rpm", 2, 60)
        assert store.consume([bucket]).allowed
        assert store.consume([bucket]).allowed
        denied = store.consume([bucket])
        assert not denied.allowed
        assert denied.retry_after == 20
        assert denied.label == "user_rpm"

        # 同一窗口内被拒绝的请求不应额外扣减。
        now[0] = 1019.0
        assert not store.consume([bucket]).allowed
        now[0] = 1020.0
        assert store.consume([bucket]).allowed
    finally:
        store.close()


def test_zero_limit_disables_and_ip_dimension() -> None:
    limits = _service(_db(), user_rpm=10, ip_rpm=2)
    try:
        limits.check_request("alice", "1.1.1.1")
        limits.check_request("alice", "1.1.1.1")
        try:
            limits.check_request("bob", "1.1.1.1")
        except RateLimitExceeded as exc:
            assert exc.scope == "ip_rpm"
        else:
            raise AssertionError("IP 维度应在第三次请求时拒绝")
        # 换 IP 后，同一用户仍可继续，说明用户维度没有误伤。
        limits.check_request("alice", "2.2.2.2")
    finally:
        limits.close()

    unlimited = _service(_db(), user_rpm=0, ip_rpm=0)
    try:
        for _ in range(20):
            unlimited.check_request("alice", "1.1.1.1")
    finally:
        unlimited.close()


def test_anonymous_uses_ip_only() -> None:
    limits = _service(_db(), user_rpm=1, ip_rpm=1)
    try:
        limits.check_request(None, "3.3.3.3")
        try:
            limits.check_request(None, "3.3.3.3")
        except RateLimitExceeded as exc:
            assert exc.scope == "ip_rpm"
        else:
            raise AssertionError("匿名请求应按 IP 限流")
    finally:
        limits.close()


def test_platform_quota_and_byok_separation() -> None:
    limits = _service(_db(), platform_daily_tokens=10)
    platform = Route("openai", "gpt-test")
    byok = Route("custom-abc", "mock")
    try:
        limits.record_usage(
            "alice",
            platform,
            {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
        )
        limits.check_request("alice", "1.1.1.1", (platform,))
        limits.record_usage("alice", platform, {"total_tokens": 5})

        try:
            limits.check_request("alice", "1.1.1.1", (platform,))
        except RateLimitExceeded as exc:
            assert exc.code == "quota_exceeded"
            assert exc.retry_after > 0
        else:
            raise AssertionError("平台额度用尽后应拒绝")

        # BYOK 主路由不受平台额度影响，即使回退链里有平台 provider。
        limits.check_request("alice", "1.1.1.1", (byok, platform))
        limits.record_usage("alice", byok, {"total_tokens": 99})
        assert limits.usage.daily_tokens("alice", kind="platform") == 11
        assert limits.usage.daily_tokens("alice", kind="byok") == 99

        try:
            limits.check_platform_quota("alice", platform)
        except RateLimitExceeded as exc:
            assert exc.code == "quota_exceeded"
        else:
            raise AssertionError("直接检查平台配额时应拒绝")
        # BYOK 主路由不受平台配额影响。
        limits.check_platform_quota("alice", byok)

        totals = limits.usage.totals("alice")
        assert totals["kinds"]["platform"]["total_tokens"] == 11
        assert totals["kinds"]["byok"]["total_tokens"] == 99
        rows = limits.usage.rows(user_id="alice")
        assert rows
        forbidden = {"content", "message", "prompt", "reply", "original"}
        for row in rows:
            assert not (set(row) & forbidden)
    finally:
        limits.close()


def test_usage_estimated_flag_and_day() -> None:
    now = [1000.0]
    db = _db()
    usage = UsageStore(db, clock=lambda: now[0])
    try:
        usage.record(
            user_id="alice",
            provider="custom-x",
            model="m",
            kind="byok",
            usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            estimated=True,
        )
        totals = usage.totals("alice", "1970-01-01")
        assert totals["kinds"]["byok"]["estimated_requests"] == 1
        assert totals["total_tokens"] == 5
    finally:
        usage.close()


def test_probe_hourly_concurrency_and_backoff() -> None:
    now = [1000.0]
    limits = LimitsService(
        _db(),
        defaults=LimitConfig(
            user_rpm=0,
            user_rpd=0,
            ip_rpm=0,
            ip_rpd=0,
            platform_daily_tokens=0,
            probe_per_hour=2,
            probe_concurrency=1,
            probe_backoff_base=2,
            probe_backoff_max=4,
        ),
        clock=lambda: now[0],
    )
    try:
        url = "https://api.example.com/v1"
        limits.check_probe("alice", "1.1.1.1", url)
        limits.check_probe("alice", "1.1.1.1", url)
        try:
            limits.check_probe("alice", "1.1.1.1", url)
        except RateLimitExceeded as exc:
            assert exc.code == "probe_rate_limited"
            assert exc.retry_after > 0
        else:
            raise AssertionError("探活应按小时限流")

        bob_url = "https://bob.example.com/v1"
        limits.check_probe("bob", "2.2.2.2", bob_url)
        limits.record_probe_failure("bob", bob_url)
        try:
            limits.check_probe("bob", "2.2.2.2", bob_url)
        except RateLimitExceeded as exc:
            assert exc.code == "probe_backoff"
            assert exc.retry_after == 2
        else:
            raise AssertionError("失败后应进入退避")

        now[0] += 2
        limits.check_probe("bob", "2.2.2.2", bob_url)

        with limits.probe_slot("carol"):
            try:
                with limits.probe_slot("carol"):
                    pass
            except RateLimitExceeded as exc:
                assert exc.code == "probe_concurrency"
            else:
                raise AssertionError("并发探活应被拒绝")
    finally:
        limits.close()


def test_client_ip_trust_proxy() -> None:
    scope = {
        "headers": [(b"x-forwarded-for", b"5.6.7.8, 10.0.0.1")],
        "client": ("1.1.1.1", 1234),
    }
    assert client_ip(scope) == "1.1.1.1"
    assert client_ip(scope, trust_proxy=True) == "5.6.7.8"
    bad = {
        "headers": [(b"x-forwarded-for", b"not-an-ip")],
        "client": ("1.1.1.1", 1234),
    }
    assert client_ip(bad, trust_proxy=True) == "1.1.1.1"


def _scope(path: str, user: str | None = "alice", ip: str = "1.2.3.4") -> dict:
    state = {"user_id": user} if user else {}
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "client": (ip, 1234),
        "query_string": b"",
        "state": state,
    }


def test_http_middleware_429_and_retry_after() -> None:
    limits = _service(
        _db(),
        user_rpm=1,
        ip_rpm=0,
    )
    calls = []

    async def app(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    middleware = LimitsMiddleware(app, limits, route_lookup=lambda user: ())

    def run_once():
        sent = []

        async def send(message):
            sent.append(message)

        asyncio.run(middleware(_scope("/api/chat"), None, send))
        return sent

    try:
        first = run_once()
        assert calls == ["/api/chat"]
        assert first[0]["status"] == 200
        second = run_once()
        assert second[0]["status"] == 429
        headers = {key.lower(): value for key, value in second[0]["headers"]}
        assert headers[b"retry-after"]
        payload = json.loads(second[1]["body"])
        assert payload["code"] == "rate_limited"
    finally:
        limits.close()


def test_http_middleware_openai_error_shape() -> None:
    limits = _service(_db(), user_rpm=1, ip_rpm=0)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})

    middleware = LimitsMiddleware(app, limits, route_lookup=lambda user: ())

    def run_once():
        sent = []

        async def send(message):
            sent.append(message)

        asyncio.run(
            middleware(
                _scope("/v1/chat/completions"),
                None,
                send,
            )
        )
        return sent

    try:
        assert run_once()[0]["status"] == 200
        blocked = run_once()
        assert blocked[0]["status"] == 429
        headers = {key.lower(): value for key, value in blocked[0]["headers"]}
        assert headers[b"retry-after"]
        payload = json.loads(blocked[1]["body"])
        assert payload["error"]["type"] == "rate_limit_error"
        assert payload["error"]["code"] == "rate_limited"
    finally:
        limits.close()


def test_ws_middleware_limits_each_chat_message() -> None:
    limits = _service(_db(), user_rpm=1, ip_rpm=0)
    sent: list[dict] = []
    inbox: asyncio.Queue = asyncio.Queue()

    async def receive():
        return await inbox.get()

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        message = await receive()
        assert message["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                return
            await send({"type": "websocket.send", "text": "ok"})

    scope = {
        "type": "websocket",
        "path": "/ws/chat",
        "headers": [],
        "client": ("1.2.3.4", 1234),
        "state": {"user_id": "alice"},
        "query_string": b"",
    }

    async def run():
        await inbox.put({"type": "websocket.connect"})
        await inbox.put(
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "chat", "content": "a"}),
            }
        )
        await inbox.put(
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "chat", "content": "b"}),
            }
        )
        await inbox.put({"type": "websocket.disconnect", "code": 1000})
        middleware = LimitsMiddleware(app, limits, route_lookup=lambda user: ())
        await middleware(scope, receive, send)

    try:
        asyncio.run(run())
        texts = [
            message["text"]
            for message in sent
            if message.get("type") == "websocket.send"
            and isinstance(message.get("text"), str)
        ]
        assert "ok" in texts
        errors = [json.loads(text) for text in texts if text != "ok"]
        assert any(item.get("code") == "rate_limited" for item in errors)
    finally:
        limits.close()


def test_retry_after_until_utc_midnight() -> None:
    # 1970-01-01 23:59:30 UTC -> 30 秒。
    assert seconds_until_utc_midnight(86370.0) == 30


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"✓ {test.__name__}")
    print(f"limits ok ({len(tests)} tests)")
