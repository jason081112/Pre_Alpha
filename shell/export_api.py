# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#50 导出端点：把对话带得走。

    GET /api/export/chat/{persona_id}?client_id=...&format=md|json
    GET /api/export/chat?format=md|json&persona_id=...     （等价，便于前端拼参数）

隔离靠 `ScopedChatLog`：它按 `current_auth_user` 给 client_id 加前缀，
所以 A 用户拿不到 B 的消息，即使 client_id 撞上了。

不导出人格定义与记忆（那是 #74 的事），这里只做对话。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from .export_core import build_payload, render_markdown, suggest_filename

__all__ = ["build_export_router"]

MAX_MESSAGES = 20000


def build_export_router(upstream: Any) -> APIRouter:
    router = APIRouter(prefix="/api/export", tags=["export"])

    def _load(persona_id: str, client_id: str) -> list[dict]:
        log = getattr(upstream, "chat_log_store", None)
        if log is None:
            raise HTTPException(status_code=503, detail="会话存储未启用")
        if not persona_id:
            raise HTTPException(status_code=400, detail="persona_id 必填")
        if not client_id:
            raise HTTPException(status_code=400, detail="client_id 必填")
        try:
            return log.load_messages(
                client_id=client_id,
                persona_id=persona_id,
                limit=MAX_MESSAGES,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"读取历史失败：{type(exc).__name__}"
            ) from exc

    @router.get("/chat")
    @router.get("/chat/{persona_id}")
    def export_chat(
        persona_id: str | None = None,
        client_id: str = Query(..., description="前端 client_id"),
        format: str = Query("md", pattern="^(md|json)$"),
    ) -> Response:
        if not persona_id:
            raise HTTPException(status_code=400, detail="persona_id 必填")

        messages = _load(persona_id, client_id)

        if format == "json":
            import json as _json

            payload = build_payload(messages, persona_id=persona_id)
            body = _json.dumps(payload, ensure_ascii=False, indent=2)
            media = "application/json; charset=utf-8"
            ext = "json"
        else:
            body = render_markdown(messages, persona_id=persona_id)
            media = "text/markdown; charset=utf-8"
            ext = "md"

        filename = suggest_filename(persona_id, ext=ext)
        return Response(
            content=body,
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Alpha-Messages": str(len(messages)),
            },
        )

    return router
