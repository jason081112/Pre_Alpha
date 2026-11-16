# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#100 Waffo Prompt Sift 最小接入的零依赖测试。

直接运行：
    python3 tests/test_content_safety.py
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.content_safety import (  # noqa: E402
    BLOCK_MESSAGE,
    DEFAULT_ENDPOINT,
    MAX_PROMPT_CHARS,
    REVIEW_MESSAGE,
    PromptSiftClient,
    PromptSiftError,
    ScanResult,
    refusal_for,
)


class FakeTransport:
    def __init__(self, status: int, payload: dict | bytes, headers: dict | None = None):
        self.status = status
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.headers = headers or {}
        self.calls: list[dict] = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "body": body,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return self.status, self.headers, self.payload


def make_client(transport: FakeTransport) -> PromptSiftClient:
    return PromptSiftClient(
        merchant_id="merchant_test",
        endpoint=DEFAULT_ENDPOINT,
        signer=lambda canonical: "SIG-" + str(len(canonical)),
        transport=transport,
    )


def test_allow_scan_sends_only_current_prompt() -> None:
    transport = FakeTransport(
        200,
        {
            "data": {
                "action": "allow",
                "reasonCode": "safe",
                "requestId": "req_1",
                "matchedCategories": ["none"],
                "semanticStatus": "enforce",
            }
        },
    )
    client = make_client(transport)
    result = asyncio.run(client.scan("今天有点累"))

    assert result.allowed
    assert result.action == "allow"
    assert result.request_id == "req_1"

    call = transport.calls[0]
    assert call["url"] == DEFAULT_ENDPOINT
    assert call["timeout"] == 5.0
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload == {
        "prompt": "今天有点累",
        "locale": "zh",
        "semantic": "enforce",
    }
    assert call["headers"]["X-Merchant-Id"] == "merchant_test"
    assert call["headers"]["X-Signature"].startswith("SIG-")
    assert call["headers"]["X-Timestamp"].isdigit()

    expected_hash = base64.b64encode(
        hashlib.sha256(call["body"]).digest()
    ).decode("ascii")
    canonical = (
        "POST\n/v1/actions/verification/scan-prompt\n"
        + call["headers"]["X-Timestamp"]
        + "\n"
        + expected_hash
    )
    assert call["headers"]["X-Signature"] == "SIG-" + str(len(canonical))


def test_block_and_review_map_to_refusals() -> None:
    block = ScanResult(action="block", reason_code="sexual", request_id="req_b")
    review = ScanResult(action="review", reason_code="uncertain", request_id="req_r")
    allow = ScanResult(action="allow")

    assert refusal_for(allow) is None
    assert refusal_for(block) == (400, BLOCK_MESSAGE, "content_blocked")
    assert refusal_for(review) == (503, REVIEW_MESSAGE, "content_review")
    # 调用方（OpenAI / WS）读取 .message 构造错误响应。
    assert PromptSiftError(BLOCK_MESSAGE).message == BLOCK_MESSAGE


def test_rate_limit_preserves_retry_after() -> None:
    transport = FakeTransport(429, b"{}", headers={"Retry-After": "12"})
    client = make_client(transport)

    try:
        asyncio.run(client.scan("test"))
    except PromptSiftError as exc:
        assert exc.status_code == 429
        assert exc.code == "content_safety_rate_limited"
        assert exc.retry_after == 12
    else:  # pragma: no cover
        raise AssertionError("429 应抛出 PromptSiftError")


def test_server_error_and_malformed_response_fail_closed() -> None:
    for transport in (
        FakeTransport(500, b"upstream down"),
        FakeTransport(200, b"not-json"),
        FakeTransport(200, {"data": {"action": "unknown"}}),
    ):
        try:
            asyncio.run(make_client(transport).scan("test"))
        except PromptSiftError as exc:
            assert exc.status_code == 503
            assert exc.code == "content_safety_unavailable"
        else:  # pragma: no cover
            raise AssertionError("异常响应应 fail-closed")


def test_empty_and_oversized_prompt_are_rejected_before_network() -> None:
    transport = FakeTransport(200, {"data": {"action": "allow"}})
    client = make_client(transport)

    for text, code in (
        ("   ", "content_safety_empty"),
        ("x" * (MAX_PROMPT_CHARS + 1), "content_safety_too_long"),
    ):
        try:
            asyncio.run(client.scan(text))
        except PromptSiftError as exc:
            assert exc.status_code == 400
            assert exc.code == code
        else:  # pragma: no cover
            raise AssertionError("非法输入应在本地拒绝")

    assert transport.calls == []


def test_from_env_modes() -> None:
    with patch.dict(os.environ, {"ALPHA_PROMPT_SIFT": "off"}, clear=True):
        assert PromptSiftClient.from_env() is None

    with patch.dict(os.environ, {"ALPHA_PROMPT_SIFT": "auto"}, clear=True):
        assert PromptSiftClient.from_env() is None

    with patch.dict(os.environ, {"ALPHA_PROMPT_SIFT": "enforce"}, clear=True):
        try:
            PromptSiftClient.from_env()
        except RuntimeError as exc:
            assert "缺少 WAFFO_MERCHANT_ID" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("enforce 缺凭据应拒绝启动")

    with patch.dict(os.environ, {"ALPHA_PROMPT_SIFT": "unexpected"}, clear=True):
        try:
            PromptSiftClient.from_env()
        except RuntimeError as exc:
            assert "auto/off/enforce" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("非法模式应报错")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"\n{len(tests)} tests passed")
