"""Provider 出站策略：SSRF / DNS rebinding 防护。

设计要点
- 解析与校验放在纯 stdlib 层，测试不需要 httpx/openai。
- 请求前把主机名解析成 IP，并固定到该 IP；后续请求不再依赖 DNS。
- 默认只允许公网地址；`ALPHA_ALLOW_PRIVATE_PROVIDERS=1` 时放行本机/局域网调试。
- 探活与真实请求共用同一套 `plan_outbound()`。
- 不跟随重定向；HTTPS 使用原始主机名做 SNI，避免证书校验失败。

注意：这不是“平台看不到 Key/内容”的加密层，只解决出站地址不可信的问题。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from .providers import RouteError


class OutboundPolicyError(RouteError):
    """出站地址不合规（私网、环回、解析失败等）。"""


Resolver = Callable[[str, int], Iterable[str]]

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def allow_private_providers() -> bool:
    """是否允许本机/局域网 provider（调试用）。"""
    return os.getenv("ALPHA_ALLOW_PRIVATE_PROVIDERS", "").strip().lower() in _TRUTHY


def _default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _normalize_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    text = str(value).strip()
    # IPv6 link-local 可能带 zone id，例如 fe80::1%wlan0
    if "%" in text:
        text = text.split("%", 1)[0]
    return ipaddress.ip_address(text)


def is_blocked_ip(value: str) -> bool:
    """非公网地址一律视为阻断。"""
    ip = _normalize_ip(value)
    return not ip.is_global or any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_reserved,
            ip.is_multicast,
            ip.is_unspecified,
        )
    )


def resolve_ips(
    host: str,
    port: int,
    *,
    resolver: Resolver | None = None,
) -> tuple[str, ...]:
    """解析主机名，返回去重后的 IP 列表。

    字面量 IP 不查 DNS。解析失败统一报通用错误，避免把内部地址写进响应。
    """
    try:
        literal = _normalize_ip(host)
        return (str(literal),)
    except ValueError:
        pass

    try:
        raw = (resolver or _default_resolver)(host, port)
    except Exception as exc:  # noqa: BLE001 - 不把底层 DNS 细节抛给用户
        raise OutboundPolicyError("provider 主机无法解析") from exc

    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        try:
            ip = _normalize_ip(item)
        except ValueError:
            continue
        text = str(ip)
        if text not in seen:
            seen.add(text)
            result.append(text)

    if not result:
        raise OutboundPolicyError("provider 主机无法解析")
    # localhost 常同时返回 ::1 与 127.0.0.1；优先 IPv4，避免只监听
    # 127.0.0.1 的本机 Ollama 被 ::1 抢先连接而失败。
    result.sort(key=lambda value: (ipaddress.ip_address(value).version, value))
    return tuple(result)


def _parse_url(url: str) -> tuple[str, str, int, str, str, str]:
    raw = (url or "").strip().rstrip("/")
    if not raw:
        raise OutboundPolicyError("provider 地址为空")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise OutboundPolicyError("provider 地址不合法") from exc
    scheme = parsed.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise OutboundPolicyError("provider 只支持 http/https")
    if not parsed.hostname:
        raise OutboundPolicyError("provider 地址缺少主机名")
    if parsed.username or parsed.password:
        raise OutboundPolicyError("provider 地址不能内嵌账号密码")
    try:
        port = parsed.port or _DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise OutboundPolicyError("provider 端口不合法") from exc
    return raw, scheme, parsed.hostname, port, parsed.path, parsed.query


def _format_host(host: str) -> str:
    """URL/Host 头里的 IPv6 需要方括号。"""
    try:
        ip = _normalize_ip(host)
    except ValueError:
        return host
    return f"[{host}]" if ip.version == 6 else host


@dataclass(frozen=True)
class OutboundTarget:
    original_url: str
    scheme: str
    host: str
    port: int
    ip: str
    host_header: str
    sni_hostname: str | None
    pinned_url: str


def plan_outbound(
    url: str,
    *,
    allow_private: bool | None = None,
    resolver: Resolver | None = None,
) -> OutboundTarget:
    """解析并固定一个 provider 地址。

    - 默认拒绝任何非公网 IP；允许列表为空时直接拒绝。
    - 如果 DNS 同时返回公网和私网地址，整体拒绝，防止轮询式 rebinding。
    - `allow_private=True` 时跳过私网判断，但仍固定解析结果。
    """
    if allow_private is None:
        allow_private = allow_private_providers()

    raw, scheme, host, port, path, query = _parse_url(url)
    ips = resolve_ips(host, port, resolver=resolver)

    if not allow_private:
        for item in ips:
            if is_blocked_ip(item):
                raise OutboundPolicyError("provider 地址不允许：仅允许公网地址")

    ip = ips[0]
    default_port = _DEFAULT_PORTS[scheme]
    pinned_netloc = f"{_format_host(ip)}:{port}"
    pinned_url = urlunsplit((scheme, pinned_netloc, path or "/", query, ""))
    host_header = _format_host(host)
    if port != default_port:
        host_header = f"{host_header}:{port}"

    return OutboundTarget(
        original_url=raw,
        scheme=scheme,
        host=host,
        port=port,
        ip=ip,
        host_header=host_header,
        sni_hostname=host if scheme == "https" else None,
        pinned_url=pinned_url,
    )


@dataclass(frozen=True)
class PinnedRequest:
    """把请求 URL 重写到固定 IP，但保留原始 Host/SNI。"""

    url: str
    host_header: str
    sni_hostname: str | None


def pinned_request(target: OutboundTarget, original_url: str) -> PinnedRequest:
    parsed = urlsplit(original_url)
    netloc = f"{_format_host(target.ip)}:{target.port}"
    url = urlunsplit(
        (target.scheme, netloc, parsed.path or "/", parsed.query, "")
    )
    return PinnedRequest(
        url=url,
        host_header=target.host_header,
        sni_hostname=target.sni_hostname,
    )


def build_async_client(target: OutboundTarget, *, timeout: float = 60.0):
    """构建固定 IP 的 httpx.AsyncClient；httpx 只在运行时导入。

    这里用自定义 transport 重写请求 URL：
    - 连接目标变成 target.ip，DNS 不再参与；
    - Host 头保持原域名；
    - HTTPS 的 `sni_hostname` 保持原域名，证书校验仍按域名进行；
    - `follow_redirects=False`，避免 3xx 把请求带到内网。
    """
    import httpx

    class _PinnedTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self._transport = httpx.AsyncHTTPTransport(trust_env=False)

        async def handle_async_request(self, request):
            pinned = pinned_request(target, str(request.url))
            headers = request.headers.copy()
            headers["host"] = pinned.host_header
            extensions = dict(request.extensions)
            if pinned.sni_hostname:
                extensions["sni_hostname"] = pinned.sni_hostname
            body = await request.aread()
            rewritten = httpx.Request(
                method=request.method,
                url=pinned.url,
                headers=headers,
                content=body,
                extensions=extensions,
            )
            return await self._transport.handle_async_request(rewritten)

        async def aclose(self) -> None:
            await self._transport.aclose()

    return httpx.AsyncClient(
        transport=_PinnedTransport(),
        base_url=target.original_url,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


def build_pinned_openai_provider(
    *,
    model: str,
    api_key: str,
    base_url: str,
    target: OutboundTarget,
    temperature: float = 0.92,
    max_tokens: int = 1024,
):
    """自定义 provider 的 OpenAI-compatible 客户端。

    只覆盖初始化，chat/chat_stream 复用上游 OpenAICompatProvider。
    `max_retries=0`：重试/回退只由 shell.routing 决定，避免 SDK 内部重试叠加。
    """
    from openai import AsyncOpenAI
    from providers.llm.base import OpenAICompatProvider

    class PinnedOpenAIProvider(OpenAICompatProvider):
        PROVIDER_NAME = "openai_compat"

        def __init__(self) -> None:
            self.model = model
            self.temperature = temperature
            self.max_tokens = max_tokens
            self.provider_name = self.PROVIDER_NAME
            self.client = AsyncOpenAI(
                api_key=api_key or "no-key-required",
                base_url=base_url,
                http_client=build_async_client(target, timeout=60.0),
                max_retries=0,
            )

    return PinnedOpenAIProvider()
