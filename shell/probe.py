# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""BYOK 探活：最小连通性检查。

保存时探活（POST/PATCH /api/me/providers）与独立「测试连接」端点
（POST /api/me/providers/probe）共用这一份逻辑：
- 与真实请求共用 `plan_outbound()`：先解析并固定 IP，再发请求；
- 不跟随重定向，避免 3xx 把探活带到内网；
- 401/403 说明目标可达、只是 Key 不对，报「鉴权失败」；
- /models 返回其他状态（如 404）不阻塞保存，只提示状态码。
"""

from __future__ import annotations

from .outbound import (
    OutboundPolicyError,
    allow_private_providers,
    build_async_client,
    plan_outbound,
)


async def probe_provider(
    base_url: str,
    api_key: str,
    timeout: float = 5.0,
    client_factory=None,
) -> tuple[bool, str]:
    """最小连通性检查；`client_factory` 仅用于测试注入。

    工厂签名：(target, timeout) -> 异步上下文管理器，需提供 `.get(url, headers=...)`。
    """
    try:
        target = plan_outbound(base_url, allow_private=allow_private_providers())
        factory = client_factory or (
            lambda t, timeout: build_async_client(t, timeout=timeout)
        )
        async with factory(target, timeout) as client:
            response = await client.get(
                base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except OutboundPolicyError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败: {type(exc).__name__}"

    if response.status_code in (301, 302, 303, 307, 308):
        return False, "provider 返回重定向，已拒绝"
    if response.status_code in (401, 403):
        return False, f"鉴权失败 (HTTP {response.status_code})"
    return True, f"HTTP {response.status_code}"
