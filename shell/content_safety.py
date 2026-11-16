# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Waffo Prompt Sift：生成前的用户输入安全扫描（#100 最小接入）。

设计边界：
- 只扫描**当前这一轮**用户输入，不发送历史、人格或聊天记录。
- 默认 ``auto``：有商户凭据才启用；本地开发没有凭据时静默跳过。
- ``enforce``：生产环境使用，缺少凭据直接拒绝启动，避免“以为开了、其实没开”。
- 网络错误、超时、5xx、格式异常一律 fail-closed；只有 ``allow`` 才继续生成。
- 不记录 prompt 原文，日志只记录 action / reasonCode / requestId。

签名与请求格式见：
https://docs.waffo.ai/zh/api-reference/endpoints/content-safety/scan-prompt
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

__all__ = [
    "DEFAULT_ENDPOINT",
    "MAX_PROMPT_CHARS",
    "PromptSiftClient",
    "PromptSiftError",
    "ScanResult",
    "refusal_for",
]

DEFAULT_ENDPOINT = "https://api.waffo.ai/v1/actions/verification/scan-prompt"
MAX_PROMPT_CHARS = 10_000

BLOCK_MESSAGE = "这条内容不符合使用规范，换一种说法再试。"
REVIEW_MESSAGE = "内容正在复核，请稍后再试。"
UNAVAILABLE_MESSAGE = "内容安全检查暂时不可用，请稍后再试。"

# transport(url, body, headers, timeout) -> (status, headers, body)
Transport = Callable[
    [str, bytes, Mapping[str, str], float],
    tuple[int, Mapping[str, str], bytes],
]
# signer(canonical_request) -> base64 signature
Signer = Callable[[str], str]


class PromptSiftError(RuntimeError):
    """Prompt Sift 不可用、被限流或返回异常。调用方应拒绝本轮生成。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 503,
        code: str = "content_safety_unavailable",
        retry_after: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after
        self.request_id = request_id


@dataclass(frozen=True)
class ScanResult:
    action: str
    reason_code: str = ""
    request_id: str = ""
    matched_categories: tuple[str, ...] = field(default_factory=tuple)
    semantic_status: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


def refusal_for(result: ScanResult) -> tuple[int, str, str] | None:
    """把非 allow 结果映射成 (HTTP 状态, 用户消息, 错误码)。"""
    if result.allowed:
        return None
    if result.action == "review":
        return 503, REVIEW_MESSAGE, "content_review"
    return 400, BLOCK_MESSAGE, "content_blocked"


def _load_signer(private_key: str) -> Signer:
    """延迟导入 cryptography；只有真正启用扫描时才需要它。"""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:  # pragma: no cover - 生产依赖缺失
        raise RuntimeError(
            "Prompt Sift 已启用，但缺少 cryptography；请安装后重启"
        ) from exc

    key = serialization.load_pem_private_key(
        private_key.encode("utf-8"),
        password=None,
    )

    def sign(canonical: str) -> str:
        signature = key.sign(
            canonical.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("ascii")

    return sign


def _default_transport(
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def _read_private_key() -> str:
    inline = os.getenv("WAFFO_PRIVATE_KEY", "").strip()
    if inline:
        return inline.replace("\\n", "\n")
    path = os.getenv("WAFFO_PRIVATE_KEY_PATH", "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"读取 WAFFO_PRIVATE_KEY_PATH 失败: {exc}") from exc
    return ""


def _retry_after(headers: Mapping[str, str]) -> int | None:
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return max(0, int(str(value).strip()))
            except (TypeError, ValueError):
                return None
    return None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


class PromptSiftClient:
    """一个无状态的 Prompt Sift 客户端；可注入 transport/signer 便于测试。"""

    def __init__(
        self,
        *,
        merchant_id: str,
        private_key: str = "",
        endpoint: str = DEFAULT_ENDPOINT,
        locale: str = "zh",
        semantic: str = "enforce",
        timeout: float = 5.0,
        transport: Transport | None = None,
        signer: Signer | None = None,
    ) -> None:
        if not merchant_id.strip():
            raise ValueError("merchant_id 不能为空")
        if endpoint != DEFAULT_ENDPOINT:
            # 只允许测试/自建代理显式覆盖，避免把签名发到意外主机。
            allowed = os.getenv("ALPHA_PROMPT_SIFT_ALLOW_CUSTOM_ENDPOINT") == "1"
            if not allowed:
                raise ValueError("自定义 Prompt Sift endpoint 需要显式允许")
        if locale not in {"ja", "en", "zh"}:
            raise ValueError("locale 必须是 ja/en/zh")
        if semantic not in {"off", "shadow", "enforce"}:
            raise ValueError("semantic 必须是 off/shadow/enforce")
        if not 0.5 <= timeout <= 30:
            raise ValueError("timeout 必须在 0.5–30 秒之间")

        self.merchant_id = merchant_id.strip()
        self.endpoint = endpoint
        self.locale = locale
        self.semantic = semantic
        self.timeout = timeout
        self._transport = transport or _default_transport
        self._signer = signer or _load_signer(private_key)

    @classmethod
    def from_env(cls) -> "PromptSiftClient | None":
        """按 ALPHA_PROMPT_SIFT=auto|off|enforce 构造；auto 缺凭据时返回 None。"""
        mode = os.getenv("ALPHA_PROMPT_SIFT", "auto").strip().lower()
        if mode not in {"auto", "off", "enforce"}:
            raise RuntimeError("ALPHA_PROMPT_SIFT 必须是 auto/off/enforce")
        if mode == "off":
            return None

        merchant_id = (
            os.getenv("WAFFO_MERCHANT_ID", "").strip()
            or os.getenv("WAFFO_PAYMENT_MERCHANT_ID", "").strip()
        )
        private_key = _read_private_key()
        if not merchant_id or not private_key:
            if mode == "enforce":
                raise RuntimeError(
                    "ALPHA_PROMPT_SIFT=enforce，但缺少 WAFFO_MERCHANT_ID "
                    "或 WAFFO_PRIVATE_KEY / WAFFO_PRIVATE_KEY_PATH"
                )
            return None

        return cls(
            merchant_id=merchant_id,
            private_key=private_key,
            locale=os.getenv("ALPHA_PROMPT_SIFT_LOCALE", "zh").strip() or "zh",
            semantic=os.getenv("ALPHA_PROMPT_SIFT_SEMANTIC", "enforce").strip()
            or "enforce",
            timeout=float(os.getenv("ALPHA_PROMPT_SIFT_TIMEOUT", "5")),
        )

    def _signed_headers(self, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        body_hash = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        path = urlsplit(self.endpoint).path or "/"
        canonical = "\n".join(("POST", path, timestamp, body_hash))
        return {
            "Content-Type": "application/json",
            "X-Merchant-Id": self.merchant_id,
            "X-Timestamp": timestamp,
            "X-Signature": self._signer(canonical),
        }

    async def scan(self, prompt: str, *, locale: str | None = None) -> ScanResult:
        if not isinstance(prompt, str) or not prompt.strip():
            raise PromptSiftError(
                "消息不能为空",
                status_code=400,
                code="content_safety_empty",
            )
        if len(prompt) > MAX_PROMPT_CHARS:
            raise PromptSiftError(
                f"单条消息不能超过 {MAX_PROMPT_CHARS} 字符",
                status_code=400,
                code="content_safety_too_long",
            )

        payload = {
            "prompt": prompt,
            "locale": locale or self.locale,
            "semantic": self.semantic,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = self._signed_headers(body)

        try:
            status, response_headers, raw = await asyncio.to_thread(
                self._transport,
                self.endpoint,
                body,
                headers,
                self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - 统一 fail-closed
            print(f"[safety] Prompt Sift 请求失败: {type(exc).__name__}: {exc}")
            raise PromptSiftError(UNAVAILABLE_MESSAGE) from exc

        if status == 429:
            raise PromptSiftError(
                "内容安全检查请求过于频繁，请稍后再试",
                status_code=429,
                code="content_safety_rate_limited",
                retry_after=_retry_after(response_headers),
            )
        if status >= 500:
            print(f"[safety] Prompt Sift 上游 {status}，拒绝本轮生成")
            raise PromptSiftError(UNAVAILABLE_MESSAGE)
        if status != 200:
            print(f"[safety] Prompt Sift 返回 HTTP {status}，拒绝本轮生成")
            raise PromptSiftError(UNAVAILABLE_MESSAGE)

        try:
            parsed = json.loads(raw.decode("utf-8"))
            data = parsed.get("data") or {}
            action = str(data.get("action") or "").strip().lower()
            if action not in {"allow", "review", "block"}:
                raise ValueError("缺少合法 action")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, ValueError) as exc:
            print(f"[safety] Prompt Sift 响应格式异常: {type(exc).__name__}")
            raise PromptSiftError(UNAVAILABLE_MESSAGE) from exc

        return ScanResult(
            action=action,
            reason_code=str(data.get("reasonCode") or ""),
            request_id=str(data.get("requestId") or ""),
            matched_categories=_string_tuple(data.get("matchedCategories")),
            semantic_status=str(data.get("semanticStatus") or ""),
        )
