"""运行期路由：按请求选择 provider/model，并执行用户自定义回退链。

设计要点
- 不改上游：把 `main.llm_client` 换成 RoutingLLMClient 即可
- 并发安全：当前路由、回退链、尝试记录全部放在 contextvar
- 回退规则：超时 / 429 / 5xx / 网络错误才换；400/401/403 直接报错
- 流式：首 token 吐出后不再回退，避免"半句话换人"
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Iterator, Sequence

from .providers import ProviderSpec, Registry, Route, RouteError

# ── 请求级状态 ────────────────────────────────────────────────
current_user: ContextVar[str] = ContextVar("alpha_current_user", default="local")
current_route: ContextVar[Route | None] = ContextVar("alpha_current_route", default=None)
last_usage: ContextVar[dict | None] = ContextVar("alpha_last_usage", default=None)
fallback_routes: ContextVar[tuple[Route, ...]] = ContextVar("alpha_fallback_routes", default=())
attempts: ContextVar[tuple[dict, ...]] = ContextVar("alpha_attempts", default=())
route_used: ContextVar[Route | None] = ContextVar("alpha_route_used", default=None)


class RouteNotConfigured(RuntimeError):
    """用户还没有选择当前模型（产品语义：没有默认模型）。"""


class AllProvidersFailed(RuntimeError):
    """当前模型与整条回退链全部失败。"""

    def __init__(self, attempt_list: Sequence[dict]):
        self.attempt_list = list(attempt_list)
        detail = "; ".join(
            f"{a['provider']}/{a['model']}: {a['error']}" for a in self.attempt_list
        ) or "no attempt"
        super().__init__(f"所有模型均失败: {detail}")


@contextmanager
def routing_context(
    route: Route | None,
    fallback: Sequence[Route] = (),
    user: str = "local",
) -> Iterator[None]:
    """为一个请求设置路由上下文，退出时恢复。"""
    token_user = current_user.set(user)
    token_route = current_route.set(route)
    token_fallback = fallback_routes.set(tuple(fallback))
    token_attempts = attempts.set(())
    token_used = route_used.set(None)
    token_usage = last_usage.set(None)
    try:
        yield
    finally:
        current_user.reset(token_user)
        current_route.reset(token_route)
        fallback_routes.reset(token_fallback)
        attempts.reset(token_attempts)
        route_used.reset(token_used)
        last_usage.reset(token_usage)


def clear_attempts() -> None:
    attempts.set(())


# ── 错误分类 ──────────────────────────────────────────────────
_NO_RETRY_STATUS = {400, 401, 403, 404, 405, 422}
_RETRY_STATUS = {408, 409, 425, 429}


def _status_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def should_fallback(exc: BaseException) -> bool:
    """True 表示换下一个 provider 有意义；False 表示应该直接把错误抛给用户。"""
    if isinstance(exc, (RouteError, RouteNotConfigured)):
        return False
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True

    status = _status_of(exc)
    if status in _NO_RETRY_STATUS:
        return False
    if status in _RETRY_STATUS or (status is not None and status >= 500):
        return True

    name = type(exc).__name__.lower()
    if "authentication" in name or "permission" in name or "badrequest" in name:
        return False
    if "valueerror" in name:
        # 多为"缺 Key / 参数配置错误"，换 provider 也没用
        return False
    if any(k in name for k in ("timeout", "connection", "network", "ratelimit")):
        return True

    # 未知错误：允许尝试下一跳，但错误会被记录
    return True


def _describe(exc: BaseException) -> str:
    text = str(exc).strip().replace("\n", " ")
    if len(text) > 200:
        text = text[:200] + "…"
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


# ── 默认客户端工厂（运行时才 import 上游） ────────────────────
def default_client_factory(spec: ProviderSpec, route: Route, temperature: float, max_tokens: int) -> Any:
    from providers.llm.client import LLMClient  # 上游模块，仅运行时可用

    return LLMClient(
        provider=spec.upstream,
        model=route.model,
        api_key=spec.api_key or None,
        base_url=spec.base_url or None,
        temperature=temperature,
        max_tokens=max_tokens,
    )


@dataclass
class _Attempt:
    route: Route
    ok: bool
    latency_ms: int
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "provider": self.route.provider,
            "model": self.route.model,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


class RoutingLLMClient:
    """LLMClient 的门面：按 contextvar 里的路由选择真实客户端。

    只需要实现 chat / chat_stream / model / provider 四个成员，
    因为上游 ChatAgent 只依赖这些。
    """

    def __init__(
        self,
        registry: Registry,
        client_factory: Callable[[ProviderSpec, Route, float, int], Any] | None = None,
        timeout: float = 60.0,
        first_token_timeout: float = 20.0,
        temperature: float = 0.92,
        max_tokens: int = 1024,
        max_cached_clients: int = 32,
        custom_store: Any = None,
    ):
        self.registry = registry
        self.custom_store = custom_store
        self._factory = client_factory or default_client_factory
        self.timeout = timeout
        self.first_token_timeout = first_token_timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._max_cached = max_cached_clients
        self._clients: dict[tuple[str, str, str], Any] = {}

    # ── 兼容 LLMClient 的属性 ─────────────────────────────
    @property
    def provider(self) -> str:
        route = current_route.get() or route_used.get()
        return route.provider if route else "unconfigured"

    @property
    def model(self) -> str:
        route = current_route.get() or route_used.get()
        return route.model if route else "unconfigured"

    # ── 内部 ──────────────────────────────────────────────
    def _route_chain(self) -> tuple[Route, ...]:
        primary = current_route.get()
        if primary is None:
            raise RouteNotConfigured(
                "当前用户尚未选择模型（route_not_configured）"
            )
        chain = [primary]
        for route in fallback_routes.get():
            if route != primary and route not in chain:
                chain.append(route)
        return tuple(chain)

    def _spec(self, route: Route) -> ProviderSpec:
        """平台 provider 查注册表；custom-* 查当前用户的自定义 provider。"""
        if self.custom_store is not None and route.provider.startswith("custom-"):
            provider = self.custom_store.get(
                route.provider, current_user.get(), decrypt=True
            )
            if provider is None:
                raise RouteError(f"自定义 provider 不存在: {route.provider}")
            return provider.to_spec()
        return self.registry.get(route.provider)

    def _client(self, route: Route) -> Any:
        spec = self._spec(route)
        import hashlib

        key = (
            current_user.get(),
            route.provider,
            route.model,
            spec.base_url,
            hashlib.sha256(spec.api_key.encode()).hexdigest()[:12],
        )
        client = self._clients.get(key)
        if client is None:
            client = self._factory(spec, route, self.temperature, self.max_tokens)
            if len(self._clients) >= self._max_cached:
                self._clients.pop(next(iter(self._clients)))
            self._clients[key] = client
        return client

    @staticmethod
    def _record(route: Route, started: float, ok: bool, exc: BaseException | None = None) -> _Attempt:
        return _Attempt(
            route=route,
            ok=ok,
            latency_ms=int((time.monotonic() - started) * 1000),
            error="" if exc is None else _describe(exc),
        )

    @staticmethod
    def _store(records: list[_Attempt]) -> None:
        # 对外只暴露失败跳转；成功的这一跳由 route_used 表示
        attempts.set(tuple(r.as_dict() for r in records if not r.ok))

    # ── 非流式 ────────────────────────────────────────────
    async def chat(
        self,
        messages: list[Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> Any:
        chain = self._route_chain()
        records: list[_Attempt] = []

        for index, route in enumerate(chain):
            started = time.monotonic()
            try:
                client = self._client(route)
                result = await asyncio.wait_for(
                    client.chat(
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=tools,
                        tool_choice=tool_choice,
                    ),
                    timeout=self.timeout,
                )
            except Exception as exc:  # noqa: BLE001 - 需要按类型分类
                record = self._record(route, started, False, exc)
                records.append(record)
                self._store(records)
                is_last = index == len(chain) - 1
                if is_last or not should_fallback(exc):
                    if len(records) == 1:
                        raise
                    raise AllProvidersFailed([r.as_dict() for r in records]) from exc
                continue

            records.append(self._record(route, started, True))
            self._store(records)
            route_used.set(route)
            usage = getattr(result, "usage", None)
            if isinstance(usage, dict):
                last_usage.set(usage)
            return result

        raise AllProvidersFailed([r.as_dict() for r in records])  # pragma: no cover

    # ── 流式 ──────────────────────────────────────────────
    async def chat_stream(
        self,
        messages: list[Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        chain = self._route_chain()
        records: list[_Attempt] = []

        for index, route in enumerate(chain):
            started = time.monotonic()
            emitted = False
            stream = None
            try:
                client = self._client(route)
                stream = client.chat_stream(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ).__aiter__()

                try:
                    first = await asyncio.wait_for(
                        stream.__anext__(), timeout=self.first_token_timeout
                    )
                except StopAsyncIteration:
                    first = None

                if first is not None:
                    emitted = True
                    yield first
                    async for chunk in stream:
                        yield chunk

                records.append(self._record(route, started, True))
                self._store(records)
                route_used.set(route)
                return

            except Exception as exc:  # noqa: BLE001
                if stream is not None:
                    try:
                        await stream.aclose()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        pass
                records.append(self._record(route, started, False, exc))
                self._store(records)
                is_last = index == len(chain) - 1
                if emitted or is_last or not should_fallback(exc):
                    if len(records) == 1:
                        raise
                    raise AllProvidersFailed([r.as_dict() for r in records]) from exc
                continue

        raise AllProvidersFailed([r.as_dict() for r in records])  # pragma: no cover
