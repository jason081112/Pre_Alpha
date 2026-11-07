# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#50 会话历史导出：把 chat_log 里的消息渲染成 Markdown / JSON。

设计取舍：

- **纯函数**：`render_markdown` / `build_payload` 不碰数据库，便于单测
- **不丢信息**：时间、角色、模态、图片链接都带上；不做摘要、不截断
- **按天分组**：Markdown 里一天一个分隔，长对话才不会糊成一片
- **文件名可读**：`alpha-<persona>-<日期>.md`，下载下来就知道是谁哪天
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "render_markdown",
    "build_payload",
    "suggest_filename",
    "SPEAKER_ME",
    "SPEAKER_THEM",
]

SPEAKER_ME = "我"
SPEAKER_THEM = "她"


def _fmt(ts: float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime(fmt)


def _day(ts: float) -> str:
    return _fmt(ts, "%Y-%m-%d")


def build_payload(
    messages: list[dict[str, Any]],
    *,
    persona_id: str,
    persona_name: str | None = None,
    exported_at: float | None = None,
) -> dict[str, Any]:
    """导出用的 JSON 结构。字段名与 chat_log 保持一致，便于二次处理。"""
    ts = exported_at if exported_at is not None else time.time()
    return {
        "schema": "alpha.export.chat/v1",
        "exported_at": int(ts),
        "exported_at_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "persona_id": persona_id,
        "persona_name": persona_name or persona_id,
        "count": len(messages),
        "messages": [
            {
                "id": m.get("id"),
                "role": m.get("role"),
                "content": m.get("content"),
                "modality": m.get("modality") or "文字",
                "image_url": m.get("image_url"),
                "created_at": m.get("created_at"),
                "created_at_iso": (
                    datetime.fromtimestamp(
                        m["created_at"], tz=timezone.utc
                    ).isoformat()
                    if m.get("created_at")
                    else None
                ),
            }
            for m in messages
        ],
    }


def render_markdown(
    messages: list[dict[str, Any]],
    *,
    persona_id: str,
    persona_name: str | None = None,
    exported_at: float | None = None,
    me: str = SPEAKER_ME,
    them: str = SPEAKER_THEM,
) -> str:
    """渲染成可读的 Markdown。空历史也返回一个合法文档（而不是空串）。"""
    ts = exported_at if exported_at is not None else time.time()
    name = persona_name or persona_id

    lines: list[str] = [
        f"# {name} · 对话记录",
        "",
        f"- 人格：`{persona_id}`",
        f"- 消息数：{len(messages)}",
        f"- 导出时间：{_fmt(ts)}",
        "",
    ]

    if not messages:
        lines += ["---", "", "_（这段对话还没有任何消息）_", ""]
        return "\n".join(lines)

    current_day = None
    for m in messages:
        created = m.get("created_at") or ts
        day = _day(created)
        if day != current_day:
            current_day = day
            lines += ["---", "", f"## {day}", ""]

        role = m.get("role")
        speaker = me if role == "user" else them
        modality = m.get("modality") or "文字"
        suffix = "" if modality in ("文字", "", None) else f"（{modality}）"
        content = (m.get("content") or "").strip()

        lines.append(f"**{speaker}**{suffix} · {_fmt(created, '%H:%M')}")
        lines.append("")
        if content:
            # 引用块：多行内容也保持在一个块内，避免和下一个说话人混淆
            for para in content.split("\n"):
                lines.append(f"> {para}" if para else ">")
        else:
            lines.append("> _（无文字内容）_")
        image_url = m.get("image_url")
        if image_url:
            lines.append("")
            lines.append(f"![图片]({image_url})")
        lines.append("")

    return "\n".join(lines)


def suggest_filename(persona_id: str, *, ext: str = "md", ts: float | None = None) -> str:
    stamp = datetime.fromtimestamp(
        ts if ts is not None else time.time(), tz=timezone.utc
    ).astimezone().strftime("%Y%m%d")
    safe = "".join(c for c in persona_id if c.isalnum() or c in "-_") or "chat"
    return f"alpha-{safe}-{stamp}.{ext}"
