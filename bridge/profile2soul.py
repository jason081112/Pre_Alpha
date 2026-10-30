# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""profile2soul —— 把 Distilly 蒸馏出的人格画像翻译成 OpenHer 可消费的种子。

数据模型见 `bridge/model.py`（#31）。本模块的映射实现属 #32–#38，
当前只固定函数签名与返回结构轮廓。
"""

from __future__ import annotations

from typing import Any

from bridge.model import PersonProfile


def profile_to_soul(profile: PersonProfile) -> dict[str, Any]:
    """把 PersonProfile 映射为 SOUL.md 所需结构。

    返回形如::

        {"frontmatter": {...}, "body": {"personality": "...", "speaking_style": "...",
                                       "behavioral_rules": "..."}}

    约束（来自 #30 实测）：

    - 正文只允许四个 H2：性格 / 说话风格 / 背景故事 / 行为规则；
    - 输出中不得出现 ``Layer N`` 字样（会被当结构标记泄漏）；
    - ``basis`` 里的推断说明不得写入正文。

    实现见 #32（身份字段）与 #33–#38（数值与风格），当前为占位。
    """
    raise NotImplementedError("P3 阶段实现：见看板 #32–#38")
