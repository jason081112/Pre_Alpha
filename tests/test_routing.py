"""#70 路由内核的零依赖测试。

直接运行：
    python3 tests/test_routing.py

不导入 FastAPI / 上游代码，因此在 Termux 上也能跑。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.providers import ProviderSpec, Registry, Route, RouteError  # noqa: E402
from shell.outbound import OutboundPolicyError  # noqa: E402
from shell.routing import (  # noqa: E402
    AllProvidersFailed,
    RouteNotConfigured,
    RoutingLLMClient,
    attempts,
    default_client_factory,
    last_usage,
    route_used,
    routing_context,
    should_fallback,
)
from shell.store import RoutingStore  # noqa: E402


# ── 假异常与假客户端 ─────────────────────────────────────────
class FakeAuthError(Exception):
    status_code = 401


class FakeServerError(Exception):
    status_code = 500


class FakeClient:
    def __init__(self, name: str, behavior: str, calls: list[str] | None = None):
        self.name = name
        self.behavior = behavior
        self.calls = calls if calls is not None else []

    async def chat(self, *args, **kwargs):
        self.calls.append(f"chat:{self.name}")
        if self.behavior == "timeout":
            raise asyncio.TimeoutError()
        if self.behavior == "auth":
            raise FakeAuthError("bad key")
        if self.behavior == "server":
            raise FakeServerError("boom")
        return f"ok:{self.name}"

    async def chat_stream(self, *args, **kwargs):
        self.calls.append(f"stream:{self.name}")
        if self.behavior == "timeout":
            raise asyncio.TimeoutError()
        if self.behavior == "emit_then_fail":
            yield "first"
            raise FakeServerError("mid-stream")
        yield f"ok:{self.name}"


def _registry() -> Registry:
    specs = {
        "a": ProviderSpec("a", "openai", "", "", ("m",), ("zh",)),
        "b": ProviderSpec("b", "openai", "", "", ("m",), ("zh",)),
    }
    return Registry(specs)


def _client(behaviors: dict[str, str], calls: list[str]) -> RoutingLLMClient:
    registry = _registry()

    def factory(spec, route, temperature, max_tokens):
        return FakeClient(route.provider, behaviors.get(route.provider, "ok"), calls)

    return RoutingLLMClient(registry=registry, client_factory=factory, timeout=2, first_token_timeout=1)


# ── 基础 ─────────────────────────────────────────────────────
def test_route_parse() -> None:
    assert Route.parse("ollama-cloud/gemma4:31b") == Route("ollama-cloud", "gemma4:31b")
    assert Route.parse("p/vendor/model").model == "vendor/model"
    try:
        Route.parse("bad")
    except RouteError:
        pass
    else:
        raise AssertionError("缺少 / 应报错")


def test_registry_validation() -> None:
    registry = Registry.load()
    route = registry.resolve("ollama-cloud", "gemma4:31b")
    assert route.as_str() == "ollama-cloud/gemma4:31b"

    for provider, model in (("nope", "x"), ("ollama-cloud", "no-such-model")):
        try:
            registry.resolve(provider, model)
        except RouteError:
            continue
        raise AssertionError(f"应拒绝非法路由: {provider}/{model}")


def test_should_fallback() -> None:
    assert should_fallback(asyncio.TimeoutError()) is True
    assert should_fallback(FakeServerError()) is True
    assert should_fallback(FakeAuthError()) is False
    assert should_fallback(RouteError("bad")) is False
    assert should_fallback(OutboundPolicyError("blocked")) is False
    assert should_fallback(ValueError("missing key")) is False


def test_custom_route_rejects_private_base_url() -> None:
    old = os.environ.get("ALPHA_ALLOW_PRIVATE_PROVIDERS")
    os.environ.pop("ALPHA_ALLOW_PRIVATE_PROVIDERS", None)
    spec = ProviderSpec(
        id="custom-deadbeef",
        upstream="openai",
        base_url="http://127.0.0.1:9999/v1",
        api_key_env="",
        models=("mock-ok",),
        caps=("zh",),
        api_key="test-key",
    )
    try:
        default_client_factory(spec, Route("custom-deadbeef", "mock-ok"), 0.9, 128)
    except OutboundPolicyError:
        pass
    else:
        raise AssertionError("自定义 provider 必须走固定 IP 出站策略")
    finally:
        if old is None:
            os.environ.pop("ALPHA_ALLOW_PRIVATE_PROVIDERS", None)
        else:
            os.environ["ALPHA_ALLOW_PRIVATE_PROVIDERS"] = old


def test_store_roundtrip() -> None:
    tmp = tempfile.mkdtemp()
    db = Path(tmp) / "routing.db"
    store = RoutingStore(db)
    try:
        assert store.get("u1") is None
        store.put("u1", Route("a", "m"), [Route("b", "m")])
        record = store.get("u1")
        assert record is not None
        assert record.route == Route("a", "m")
        assert record.fallback == (Route("b", "m"),)

        store.put("u1", Route("b", "m"), [])
        assert store.get("u1").route == Route("b", "m")
        assert store.get("u1").fallback == ()

        assert store.delete("u1") is True
        assert store.get("u1") is None
    finally:
        store.close()


# ── 路由行为 ─────────────────────────────────────────────────
def test_chat_falls_back_on_timeout() -> None:
    calls: list[str] = []
    client = _client({"a": "timeout", "b": "ok"}, calls)

    async def run():
        with routing_context(Route("a", "m"), [Route("b", "m")]):
            result = await client.chat([])
            return result, list(attempts.get()), route_used.get()

    result, records, used = asyncio.run(run())
    assert result == "ok:b"
    assert used == Route("b", "m")
    assert len(records) == 1
    assert records[0]["ok"] is False and records[0]["provider"] == "a"
    assert calls == ["chat:a", "chat:b"]


def test_chat_does_not_fall_back_on_auth() -> None:
    calls: list[str] = []
    client = _client({"a": "auth", "b": "ok"}, calls)

    async def run():
        with routing_context(Route("a", "m"), [Route("b", "m")]):
            try:
                await client.chat([])
            except FakeAuthError:
                return list(attempts.get())

    records = asyncio.run(run())
    assert calls == ["chat:a"]
    assert len(records) == 1 and records[0]["ok"] is False


def test_chat_raises_all_failed() -> None:
    calls: list[str] = []
    client = _client({"a": "server", "b": "server"}, calls)

    async def run():
        with routing_context(Route("a", "m"), [Route("b", "m")]):
            try:
                await client.chat([])
            except AllProvidersFailed as exc:
                return exc.attempt_list

    attempt_list = asyncio.run(run())
    assert len(attempt_list) == 2
    assert calls == ["chat:a", "chat:b"]


def test_stream_falls_back_before_first_token() -> None:
    calls: list[str] = []
    client = _client({"a": "timeout", "b": "ok"}, calls)

    async def run():
        with routing_context(Route("a", "m"), [Route("b", "m")]):
            chunks = [chunk async for chunk in client.chat_stream([])]
            return chunks, route_used.get()

    chunks, used = asyncio.run(run())
    assert chunks == ["ok:b"]
    assert used == Route("b", "m")


def test_stream_no_retry_after_first_token() -> None:
    calls: list[str] = []
    client = _client({"a": "emit_then_fail", "b": "ok"}, calls)

    async def run():
        with routing_context(Route("a", "m"), [Route("b", "m")]):
            stream = client.chat_stream([])
            first = await stream.__anext__()
            assert first == "first"
            try:
                await stream.__anext__()
            except FakeServerError:
                return True
            return False

    assert asyncio.run(run()) is True
    assert calls == ["stream:a"]


def test_route_not_configured() -> None:
    client = _client({}, [])

    async def run():
        with routing_context(None):
            try:
                await client.chat([])
            except RouteNotConfigured:
                return True
            return False

    assert asyncio.run(run()) is True


def test_concurrent_routes_do_not_leak() -> None:
    calls: list[str] = []
    client = _client({"a": "ok", "b": "ok"}, calls)
    registry = _registry()

    async def worker(route: Route):
        with routing_context(route):
            await asyncio.sleep(0.01)
            local = RoutingLLMClient(
                registry=registry,
                client_factory=lambda spec, r, t, m: FakeClient(r.provider, "ok", calls),
            )
            return await local.chat([])

    async def run():
        return await asyncio.gather(worker(Route("a", "m")), worker(Route("b", "m")))

    assert asyncio.run(run()) == ["ok:a", "ok:b"]


class UsageClient:
    async def chat(self, *args, **kwargs):
        return type(
            "Result",
            (),
            {"usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}},
        )()


class StreamUsageClient:
    async def chat_stream(self, *args, **kwargs):
        yield "hello"
        yield " world"


def test_usage_is_captured_per_request() -> None:
    registry = Registry({"a": ProviderSpec("a", "openai", "", "", ("m",), ("zh",))})
    client = RoutingLLMClient(
        registry=registry, client_factory=lambda s, r, t, m: UsageClient()
    )

    async def run():
        with routing_context(Route("a", "m")):
            await client.chat([])
            return last_usage.get()

    usage = asyncio.run(run())
    assert usage is not None and usage["total_tokens"] == 18


def test_usage_hook_receives_provider_and_user() -> None:
    registry = Registry({"a": ProviderSpec("a", "openai", "", "", ("m",), ("zh",))})
    seen: list[tuple] = []
    client = RoutingLLMClient(
        registry=registry,
        client_factory=lambda s, r, t, m: UsageClient(),
        usage_hook=lambda user, route, usage, estimated: seen.append(
            (user, route, usage, estimated)
        ),
    )

    async def run():
        with routing_context(Route("a", "m"), user="alice"):
            await client.chat(
                [{"role": "user", "content": "你好"}],
            )

    asyncio.run(run())
    assert len(seen) == 1
    user, route, usage, estimated = seen[0]
    assert user == "alice"
    assert route == Route("a", "m")
    assert usage["total_tokens"] == 18
    assert estimated is False


def test_stream_usage_is_estimated() -> None:
    registry = Registry({"a": ProviderSpec("a", "openai", "", "", ("m",), ("zh",))})
    seen: list[tuple] = []
    client = RoutingLLMClient(
        registry=registry,
        client_factory=lambda s, r, t, m: StreamUsageClient(),
        usage_hook=lambda user, route, usage, estimated: seen.append(
            (user, route, usage, estimated)
        ),
    )

    async def run():
        with routing_context(Route("a", "m"), user="alice"):
            return [
                chunk
                async for chunk in client.chat_stream(
                    [{"role": "user", "content": "你好世界"}],
                )
            ]

    assert asyncio.run(run()) == ["hello", " world"]
    assert len(seen) == 1
    user, route, usage, estimated = seen[0]
    assert user == "alice"
    assert route == Route("a", "m")
    assert estimated is True
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"✓ {test.__name__}")
    print(f"routing ok ({len(tests)} tests)")
