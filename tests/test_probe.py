"""BYOK 探活（shell/probe.py）的零依赖测试。

    python3 tests/test_probe.py

覆盖：出站策略拒绝、200/4xx/3xx 分支、连接异常、client_factory 注入约定。
不发真实网络请求：公网地址用字面量 IP，HTTP 层用假客户端。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.probe import probe_provider  # noqa: E402

PUBLIC = "93.184.216.34"
PUBLIC_URL = f"https://{PUBLIC}/v1"


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeClient:
    """记录调用参数，返回预设状态码或抛出预设异常。"""

    def __init__(self, status: int | None = None, exc: Exception | None = None):
        self.status = status
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url: str, headers: dict | None = None):
        self.calls.append((url, headers or {}))
        if self.exc is not None:
            raise self.exc
        return FakeResponse(self.status)


def _factory(client: FakeClient):
    def make(target, timeout):
        client.target = target
        client.timeout = timeout
        return client

    return make


def _probe(url: str, key: str = "sk-test", client: FakeClient | None = None):
    kwargs = {}
    if client is not None:
        kwargs["client_factory"] = _factory(client)
    return asyncio.run(probe_provider(url, key, **kwargs))


def test_private_literal_rejected_by_policy() -> None:
    old = os.environ.pop("ALPHA_ALLOW_PRIVATE_PROVIDERS", None)
    try:
        for url in (
            "http://127.0.0.1:11434/v1",
            "https://10.0.0.1/v1",
            "http://169.254.169.254/latest",
        ):
            ok, message = _probe(url)
            assert not ok, url
            assert "公网" in message, message
    finally:
        if old is not None:
            os.environ["ALPHA_ALLOW_PRIVATE_PROVIDERS"] = old


def test_ok_status_returns_true() -> None:
    for status in (200, 404):
        ok, message = _probe(PUBLIC_URL, client=FakeClient(status=status))
        assert ok, status
        assert message == f"HTTP {status}"


def test_auth_failure_reports_key_problem() -> None:
    for status in (401, 403):
        ok, message = _probe(PUBLIC_URL, client=FakeClient(status=status))
        assert not ok, status
        assert "鉴权失败" in message, message
        assert str(status) in message


def test_redirect_is_rejected() -> None:
    for status in (301, 302, 307, 308):
        ok, message = _probe(PUBLIC_URL, client=FakeClient(status=status))
        assert not ok, status
        assert "重定向" in message, message


def test_connection_error_is_captured() -> None:
    ok, message = _probe(PUBLIC_URL, client=FakeClient(exc=ConnectionError("boom")))
    assert not ok
    assert message == "连接失败: ConnectionError", message
    assert "boom" not in message


def test_request_shape() -> None:
    client = FakeClient(status=200)
    _probe(PUBLIC_URL, key="sk-secret", client=client)
    url, headers = client.calls[0]
    assert url == PUBLIC_URL + "/models"
    assert headers["Authorization"] == "Bearer sk-secret"
    assert client.timeout == 5.0
    # 注入的是解析固定后的 target（字面量 IP 场景与输入一致）
    assert client.target.ip == PUBLIC


def test_trailing_slash_normalized() -> None:
    client = FakeClient(status=200)
    _probe(PUBLIC_URL + "/", client=client)
    assert client.calls[0][0] == PUBLIC_URL + "/models"


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"✓ {test.__name__}")
    print(f"probe ok ({len(tests)} tests)")
