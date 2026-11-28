# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#99 单人格收窄：上线阶段只暴露一个可聊人格（默认 xiaolan）。

其他内置人格的文件仍在仓库里，但不再出现在 ``/v1/models``、默认人格和
WS 会话里。用环境变量控制白名单：

    ALPHA_PERSONAS=xiaolan           # 只暴露 xiaolan
    ALPHA_PERSONAS=xiaolan,luna      # 暴露两个
    （不设置 / 空）                   # 不限制，开发默认

白名单只在「暴露」层生效：人格文件、CLI、测试都不受影响，改环境变量即可放开。
"""

from __future__ import annotations

import os

__all__ = [
    "DEFAULT_PERSONA",
    "ENV_ALLOWLIST",
    "parse_allowlist",
    "allowlist",
    "filter_personas",
    "resolve_persona",
]

DEFAULT_PERSONA = "xiaolan"
ENV_ALLOWLIST = "ALPHA_PERSONAS"


def parse_allowlist(raw: str | None) -> tuple[str, ...] | None:
    """把 ``ALPHA_PERSONAS`` 解析成元组；空/未设置返回 None（表示不限制）。"""
    if raw is None:
        return None
    items = tuple(
        part.strip()
        for part in raw.replace(";", ",").replace(" ", ",").split(",")
        if part.strip()
    )
    return items or None


def allowlist(env: dict[str, str] | None = None) -> tuple[str, ...] | None:
    """读取当前进程环境的白名单。"""
    source = os.environ if env is None else env
    return parse_allowlist(source.get(ENV_ALLOWLIST))


def filter_personas(
    persona_ids: list[str],
    allowed: tuple[str, ...] | None = None,
) -> list[str]:
    """按白名单过滤人格列表。

    白名单与已加载人格没有交集时保留原列表：配置打错不该让服务直接
    「没有可用人格」而 500，宁可按原样提供，日志里能看到请求参数。
    """
    if allowed is None:
        return list(persona_ids)
    kept = [pid for pid in persona_ids if pid in allowed]
    return kept or list(persona_ids)


def resolve_persona(
    requested: str,
    allowed: tuple[str, ...] | None = None,
    default: str = DEFAULT_PERSONA,
) -> str:
    """把请求里的人格 ID 收窄到白名单内。

    - 未设置白名单：原样返回（开发模式不干预）
    - 在白名单内：原样返回
    - 不在白名单内：回落到默认人格（默认不在白名单时取白名单第一个）
    """
    value = (requested or "").strip()
    if allowed is None:
        return value
    if value in allowed:
        return value
    if default in allowed:
        return default
    return allowed[0]
