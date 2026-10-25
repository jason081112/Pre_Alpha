"""#81 出站策略的零依赖测试。

    python3 tests/test_outbound.py

覆盖：DNS rebinding、私网字面量/域名、混合解析、localhost 放行、IP 固定。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.custom import assert_public_host  # noqa: E402
from shell.outbound import (  # noqa: E402
    OutboundPolicyError,
    _format_host,
    pinned_request,
    plan_outbound,
    resolve_ips,
)

PUBLIC = "93.184.216.34"


class SequenceResolver:
    def __init__(self, *answers: list[str]):
        self.answers = list(answers)

    def __call__(self, host: str, port: int) -> list[str]:
        return self.answers.pop(0)


def _reject(fn, needle: str | None = None) -> str:
    try:
        fn()
    except OutboundPolicyError as exc:
        text = str(exc)
        assert needle is None or needle in text, text
        return text
    raise AssertionError("应被出站策略拒绝")


def test_dns_rebinding_second_resolution_is_rejected() -> None:
    resolver = SequenceResolver([PUBLIC], ["127.0.0.1"])
    first = plan_outbound(
        "https://rebind.example/v1",
        allow_private=False,
        resolver=resolver,
    )
    assert first.ip == PUBLIC

    text = _reject(
        lambda: plan_outbound(
            "https://rebind.example/v1",
            allow_private=False,
            resolver=resolver,
        )
    )
    assert "127.0.0.1" not in text


def test_mixed_public_and_private_answers_are_rejected() -> None:
    _reject(
        lambda: plan_outbound(
            "https://mixed.example/v1",
            allow_private=False,
            resolver=SequenceResolver([PUBLIC, "10.0.0.7"]),
        )
    )


def test_private_domain_is_rejected_without_leaking_ip() -> None:
    text = _reject(
        lambda: plan_outbound(
            "https://internal.example/v1",
            allow_private=False,
            resolver=SequenceResolver(["10.1.2.3"]),
        )
    )
    assert "10.1.2.3" not in text


def test_private_literals_are_rejected() -> None:
    for url in (
        "http://127.0.0.1:11434/v1",
        "https://10.0.0.1/v1",
        "https://192.168.1.5/v1",
        "http://169.254.169.254/latest",
        "http://[::1]:11434/v1",
        "http://0.0.0.0/v1",
    ):
        _reject(lambda url=url: plan_outbound(url, allow_private=False))


def test_localhost_allowed_when_private_enabled() -> None:
    target = plan_outbound(
        "http://localhost:11434/v1",
        allow_private=True,
        resolver=SequenceResolver(["::1", "127.0.0.1"]),
    )
    assert target.ip == "127.0.0.1"
    assert target.pinned_url == "http://127.0.0.1:11434/v1"
    assert target.host_header == "localhost:11434"
    assert target.sni_hostname is None


def test_create_policy_uses_same_rebinding_check() -> None:
    resolver = SequenceResolver([PUBLIC], ["127.0.0.1"])
    assert_public_host(
        "https://rebind.example/v1",
        allow_private=False,
        resolver=resolver,
    )
    _reject(
        lambda: assert_public_host(
            "https://rebind.example/v1",
            allow_private=False,
            resolver=resolver,
        )
    )


def test_create_policy_allows_localhost_with_private_flag() -> None:
    target = assert_public_host(
        "http://localhost:11434/v1",
        allow_private=True,
        resolver=SequenceResolver(["127.0.0.1"]),
    )
    assert target.ip == "127.0.0.1"


def test_private_env_enables_localhost() -> None:
    old = os.environ.get("ALPHA_ALLOW_PRIVATE_PROVIDERS")
    os.environ["ALPHA_ALLOW_PRIVATE_PROVIDERS"] = "1"
    try:
        target = plan_outbound(
            "http://127.0.0.1:11434/v1",
            resolver=SequenceResolver(["127.0.0.1"]),
        )
    finally:
        if old is None:
            os.environ.pop("ALPHA_ALLOW_PRIVATE_PROVIDERS", None)
        else:
            os.environ["ALPHA_ALLOW_PRIVATE_PROVIDERS"] = old
    assert target.ip == "127.0.0.1"


def test_public_literal_and_pinned_request() -> None:
    target = plan_outbound(
        f"https://{PUBLIC}/v1",
        allow_private=False,
    )
    pinned = pinned_request(target, f"https://{PUBLIC}/v1/models?x=1")
    assert pinned.url == f"https://{PUBLIC}:443/v1/models?x=1"
    assert pinned.host_header == PUBLIC
    assert pinned.sni_hostname == PUBLIC


def test_hostname_pins_ip_but_keeps_host_and_sni() -> None:
    target = plan_outbound(
        "https://api.example.com/v1",
        allow_private=False,
        resolver=SequenceResolver([PUBLIC]),
    )
    pinned = pinned_request(target, "https://api.example.com/v1/models")
    assert pinned.url == f"https://{PUBLIC}:443/v1/models"
    assert pinned.host_header == "api.example.com"
    assert pinned.sni_hostname == "api.example.com"


def test_ipv6_is_bracketed() -> None:
    target = plan_outbound(
        "https://[2606:2800:220:1:248:1893:25c8:1946]/v1",
        allow_private=False,
    )
    assert target.pinned_url.startswith("https://[2606:2800:220:1:248:1893:25c8:1946]:443/")
    assert _format_host("2606:2800:220:1:248:1893:25c8:1946").startswith("[")


def test_url_rules() -> None:
    for url in (
        "",
        "ftp://example.com/v1",
        "https://user:pass@example.com/v1",
        "https:///v1",
        "https://[::1/v1",
    ):
        _reject(lambda url=url: plan_outbound(url, allow_private=True))


def test_resolve_failure_is_generic() -> None:
    def broken(host: str, port: int) -> list[str]:
        raise OSError("dns down")

    text = _reject(
        lambda: resolve_ips("nope.example", 443, resolver=broken)
    )
    assert "dns down" not in text


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"✓ {test.__name__}")
    print(f"outbound ok ({len(tests)} tests)")
