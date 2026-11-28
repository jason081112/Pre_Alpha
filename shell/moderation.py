# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""违规处置：举报、封禁、额度清零与审计（#100）。

范围与 AUP（site/legal/aup.html）第 5、6 节一一对应：

- 举报：登录用户在聊天页提交，落库待查（不保存聊天原文，只存用户提供的描述）。
- 封禁：管理端操作，立即阻断 WS 与 `/v1` 生成；解封同样留痕。
- 额度清零：清掉当天该用户的 token 用量，重新计算免费额度。
- 审计：每一次举报与处置都写 `moderation_action`，可追溯操作人/原因/时间。

管理接口沿用 `X-Alpha-Admin-Token`（与 limits 管理接口一致），未配置
`ALPHA_ADMIN_TOKEN` 时返回 503，而不是默认放行。
"""

from __future__ import annotations

import hmac
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "ModerationStore",
    "build_moderation_router",
    "REPORT_CATEGORIES",
]

REPORT_CATEGORIES = (
    "sexual",
    "violence",
    "hate",
    "csam",
    "impersonation",
    "copyright",
    "self_harm",
    "other",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS moderation_report (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    category    TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_report_status ON moderation_report(status, created_at);
CREATE TABLE IF NOT EXISTS moderation_action (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    action      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT 'admin',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_action_user ON moderation_action(user_id, created_at);
CREATE TABLE IF NOT EXISTS moderation_ban (
    user_id     TEXT PRIMARY KEY,
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT 'admin',
    created_at  REAL NOT NULL
);
"""


class ModerationStore:
    """一个 SQLite 存储；线程安全（uvicorn 线程池会并发调用）。"""

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── 举报 ────────────────────────────────────────────────────
    def add_report(
        self,
        user_id: str,
        category: str,
        *,
        detail: str = "",
        target: str = "",
    ) -> int:
        category = category if category in REPORT_CATEGORIES else "other"
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO moderation_report (user_id, category, target, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(user_id or "")[:128],
                    category,
                    str(target or "")[:512],
                    str(detail or "")[:4000],
                    self._clock(),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def reports(self, *, status: str | None = None, limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit or 200), 1000))
        sql = "SELECT * FROM moderation_report"
        args: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY created_at DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (*args, limit)).fetchall()
        return [dict(row) for row in rows]

    def set_report_status(self, report_id: int, status: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE moderation_report SET status = ? WHERE id = ?",
                (str(status or "open")[:32], int(report_id)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ── 处置与审计 ──────────────────────────────────────────────
    def _log(self, user_id: str, action: str, reason: str, actor: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO moderation_action (user_id, action, reason, actor, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(user_id or "")[:128], action, str(reason or "")[:2000], actor, self._clock()),
        )
        return int(cur.lastrowid)

    def ban(self, user_id: str, *, reason: str = "", actor: str = "admin") -> int:
        target = str(user_id or "").strip()
        if not target:
            raise ValueError("user_id 不能为空")
        with self._lock:
            self._conn.execute(
                "INSERT INTO moderation_ban (user_id, reason, actor, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET reason = excluded.reason, "
                "actor = excluded.actor, created_at = excluded.created_at",
                (target, str(reason or "")[:2000], actor, self._clock()),
            )
            action_id = self._log(target, "ban", reason, actor)
            self._conn.commit()
            return action_id

    def unban(self, user_id: str, *, reason: str = "", actor: str = "admin") -> int:
        target = str(user_id or "").strip()
        if not target:
            raise ValueError("user_id 不能为空")
        with self._lock:
            self._conn.execute("DELETE FROM moderation_ban WHERE user_id = ?", (target,))
            action_id = self._log(target, "unban", reason, actor)
            self._conn.commit()
            return action_id

    def is_banned(self, user_id: str | None) -> bool:
        target = str(user_id or "").strip()
        if not target or target == "local":
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM moderation_ban WHERE user_id = ?", (target,)
            ).fetchone()
        return row is not None

    def record_action(
        self,
        user_id: str,
        action: str,
        *,
        reason: str = "",
        actor: str = "admin",
    ) -> int:
        with self._lock:
            action_id = self._log(str(user_id or ""), str(action or ""), reason, actor)
            self._conn.commit()
            return action_id

    def actions(self, *, user_id: str | None = None, limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit or 200), 1000))
        sql = "SELECT * FROM moderation_action"
        args: tuple[Any, ...] = ()
        if user_id:
            sql += " WHERE user_id = ?"
            args = (str(user_id),)
        sql += " ORDER BY created_at DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (*args, limit)).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _require_admin(token: str | None) -> None:
    from fastapi import HTTPException

    expected = os.getenv("ALPHA_ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(503, "管理接口未启用：请设置 ALPHA_ADMIN_TOKEN")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "admin token 无效")


def build_moderation_router(
    store: ModerationStore,
    *,
    limits: Any = None,
):
    """举报给用户、处置给管理员；延迟导入 fastapi/pydantic 便于零依赖测试。"""
    from fastapi import APIRouter, Header, HTTPException
    from pydantic import BaseModel, Field

    from .auth.scope import current_auth_user

    class ReportIn(BaseModel):
        category: str = Field(default="other")
        target: str = ""
        detail: str = Field(default="", max_length=4000)

    class ActionIn(BaseModel):
        user_id: str
        action: str
        reason: str = Field(default="", max_length=2000)

    router = APIRouter(tags=["moderation"])

    @router.post("/api/moderation/report")
    def report(payload: ReportIn) -> dict:
        user = current_auth_user.get()
        if not user:
            raise HTTPException(401, "请先登录")
        report_id = store.add_report(
            user,
            payload.category,
            target=payload.target,
            detail=payload.detail,
        )
        return {"id": report_id, "status": "open"}

    @router.get("/api/admin/moderation/reports")
    def list_reports(
        status: str | None = None,
        limit: int = 200,
        x_alpha_admin_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(x_alpha_admin_token)
        return {"reports": store.reports(status=status, limit=limit)}

    @router.post("/api/admin/moderation/reports/{report_id}/status")
    def update_report(
        report_id: int,
        body: dict,
        x_alpha_admin_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(x_alpha_admin_token)
        status = str(body.get("status") or "").strip()
        if status not in {"open", "reviewing", "resolved", "rejected"}:
            raise HTTPException(422, "status 必须是 open/reviewing/resolved/rejected")
        if not store.set_report_status(report_id, status):
            raise HTTPException(404, "举报不存在")
        return {"id": report_id, "status": status}

    @router.get("/api/admin/moderation/actions")
    def list_actions(
        user_id: str | None = None,
        limit: int = 200,
        x_alpha_admin_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(x_alpha_admin_token)
        return {"actions": store.actions(user_id=user_id, limit=limit)}

    @router.post("/api/admin/moderation/actions")
    def take_action(
        payload: ActionIn,
        x_alpha_admin_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(x_alpha_admin_token)
        target = payload.user_id.strip()
        if not target:
            raise HTTPException(422, "user_id 不能为空")

        if payload.action == "ban":
            action_id = store.ban(target, reason=payload.reason)
        elif payload.action == "unban":
            action_id = store.unban(target, reason=payload.reason)
        elif payload.action == "quota_reset":
            if limits is None:
                raise HTTPException(503, "额度服务未启用")
            cleared = limits.reset_user_usage(target)
            action_id = store.record_action(
                target,
                "quota_reset",
                reason=f"{payload.reason} (cleared_rows={cleared})".strip(),
            )
        else:
            raise HTTPException(422, "action 必须是 ban/unban/quota_reset")

        return {"id": action_id, "user_id": target, "action": payload.action}

    @router.get("/api/admin/moderation/bans")
    def list_bans(
        x_alpha_admin_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(x_alpha_admin_token)
        rows = [a for a in store.actions(limit=1000) if a["action"] == "ban"]
        banned = []
        for row in rows:
            if store.is_banned(row["user_id"]):
                banned.append(row)
        return {"bans": banned}

    return router
