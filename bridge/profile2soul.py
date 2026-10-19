"""profile2soul —— 把 Distilly 蒸馏出的人格画像翻译成 OpenHer 可消费的种子。

P1 阶段仅占位，用于固定模块路径与数据模型轮廓；实现属 P3（看板 #31–#44）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PersonProfile:
    """Distilly 蒸馏产物在我方内部的统一表示（占位，字段随 P3 收敛）。"""

    identity: dict[str, Any] = field(default_factory=dict)
    expression: dict[str, Any] = field(default_factory=dict)
    drives: dict[str, float] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)


def profile_to_soul(profile: PersonProfile) -> dict[str, Any]:
    """把 PersonProfile 映射为 SOUL.md frontmatter 所需的字典。

    占位实现，抛 NotImplementedError。
    """
    raise NotImplementedError("P3 阶段实现：见看板 #31–#44")
