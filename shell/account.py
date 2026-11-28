# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#75 账号注销：冷静期 → 级联清除 → 回执。

设计：
- 先登记（`account_deletion`），默认 7 天冷静期，期内可撤销；
- 到期由后台任务/管理端点执行，逐个库清掉 `user_id` 相关行；
- 每个来源的删除行数写进 `account_purge_log` 与回执，事后可核；
- 备份：没有备份目录时不假装有；配了 `ALPHA_BACKUP_DIR` 就按 user_id 删匹配文件，
  并把保留策略一并写进回执。

本模块零依赖；HTTP 层延迟导入 fastapi，文件里**不要**加
`from __future__ import annotations`（注解字符串化会让 FastAPI 认不出 Request，
与 consent.py / moderation.py 踩过的是同一个坑）。
"""

import json
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CONFIRM_WORD = "删除"
DEFAULT_COOLING_DAYS = 7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_deletion (
    user_id      TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'pending',
    requested_at REAL NOT NULL,
    execute_at   REAL NOT NULL,
    completed_at REAL,
    cancelled_at REAL,
    receipt      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS account_purge_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    scope      TEXT NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_purge_log_user ON account_purge_log(user_id, id);
"""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class DeletionStore:
    """注销申请与执行记录。"""

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def request(self, user_id: str, *, cooling_days: float = DEFAULT_COOLING_DAYS) -> dict:
        user_id = str(user_id or "").strip()
        if not user_id:
            raise ValueError("user_id 不能为空")
        now = self._clock()
        execute_at = now + max(0.0, float(cooling_days)) * 86400
        self._conn.execute(
            "INSERT INTO account_deletion"
            " (user_id, status, requested_at, execute_at, completed_at, cancelled_at, receipt)"
            " VALUES (?, 'pending', ?, ?, NULL, NULL, '')"
            " ON CONFLICT(user_id) DO UPDATE SET"
            " status = 'pending', requested_at = excluded.requested_at,"
            " execute_at = excluded.execute_at, completed_at = NULL,"
            " cancelled_at = NULL, receipt = ''",
            (user_id, now, execute_at),
        )
        self._conn.commit()
        return self.get(user_id) or {}

    def cancel(self, user_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE account_deletion SET status = 'cancelled', cancelled_at = ?"
            " WHERE user_id = ? AND status = 'pending'",
            (self._clock(), str(user_id)),
        )
        self._conn.commit()
        return bool(cur.rowcount)

    def get(self, user_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM account_deletion WHERE user_id = ?", (str(user_id),)
        ).fetchone()
        return self._row(row) if row else None

    def due(self, *, now: float | None = None, limit: int = 20) -> list[dict]:
        now = self._clock() if now is None else now
        rows = self._conn.execute(
            "SELECT * FROM account_deletion WHERE status = 'pending' AND execute_at <= ?"
            " ORDER BY execute_at ASC LIMIT ?",
            (now, max(1, int(limit))),
        ).fetchall()
        return [self._row(r) for r in rows]

    def complete(self, user_id: str, receipt: dict) -> None:
        self._conn.execute(
            "UPDATE account_deletion SET status = 'done', completed_at = ?, receipt = ?"
            " WHERE user_id = ?",
            (self._clock(), json.dumps(receipt, ensure_ascii=False), str(user_id)),
        )
        self._conn.commit()

    def log_scope(self, user_id: str, scope: str, deleted: int) -> None:
        self._conn.execute(
            "INSERT INTO account_purge_log (user_id, scope, deleted, at) VALUES (?, ?, ?, ?)",
            (str(user_id), str(scope), int(deleted), self._clock()),
        )
        self._conn.commit()

    def logs(self, user_id: str, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT scope, deleted, at FROM account_purge_log"
            " WHERE user_id = ? ORDER BY id ASC LIMIT ?",
            (str(user_id), max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["receipt"] = json.loads(data["receipt"]) if data.get("receipt") else None
        data["execute_at_iso"] = _iso(data["execute_at"])
        data["requested_at_iso"] = _iso(data["requested_at"])
        return data

    def close(self) -> None:
        self._conn.close()


# ── 级联清除 ────────────────────────────────────────────────

def _purge_sqlite(path: str | Path | None, statements: list[tuple[str, tuple]]) -> dict:
    """对一个 SQLite 库执行若干 DELETE；库/表不存在时跳过而不是失败。"""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {"missing": True}
    counts: dict[str, int] = {}
    try:
        conn = sqlite3.connect(str(p), timeout=10)
    except sqlite3.Error:
        return {"error": "open_failed"}
    try:
        for sql, args in statements:
            try:
                cur = conn.execute(sql, args)
                key = sql.split("FROM", 1)[1].strip().split()[0] if "FROM" in sql else sql
                counts[key] = int(cur.rowcount or 0)
            except sqlite3.Error:
                continue
        conn.commit()
    finally:
        conn.close()
    return counts


def purge_user(
    user_id: str,
    *,
    auth_store: Any = None,
    limits: Any = None,
    moderation: Any = None,
    billing: Any = None,
    custom_store: Any = None,
    agent_extra: Any = None,
    routes: Any = None,
    chat_db: str | Path | None = None,
    engine_db: str | Path | None = None,
    memory_db: str | Path | None = None,
    persona_root: str | Path | None = None,
    backup_dir: str | Path | None = None,
    backup_retention_days: int = 30,
    now: float | None = None,
) -> dict:
    """清掉一个用户的全部数据，返回回执（各来源删除条数）。"""
    from .auth.scope import session_prefix

    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("user_id 不能为空")
    now = time.time() if now is None else now
    receipt: dict[str, Any] = {
        "user_id": user_id,
        "deleted_at": _iso(now),
        "scope": {},
        "files": {},
        "persona_dirs": [],
        "backups": {},
    }
    scope = receipt["scope"]

    if auth_store is not None:
        scope["auth"] = auth_store.delete_user(user_id)
    if limits is not None:
        scope["limits"] = {"token_usage": limits.usage.clear(user_id)}
    if moderation is not None:
        scope["moderation"] = moderation.delete_user(user_id)
    if billing is not None:
        scope["billing"] = billing.delete_user(user_id)
    if custom_store is not None:
        scope["providers"] = custom_store.delete_user(user_id)
    if agent_extra is not None:
        scope["agent_extra"] = {"agent_extra": agent_extra.delete_user(user_id)}
    if routes is not None:
        scope["routing"] = {"user_routing": 1 if routes.delete(user_id) else 0}

    prefix = session_prefix(user_id)
    receipt["files"]["chat.db"] = _purge_sqlite(
        chat_db,
        [
            (
                # 正常记录带 session_prefix，个别旧路径（开场白）直接写 user_id
                "DELETE FROM chat_messages"
                " WHERE client_id IN (?, ?) OR client_id LIKE ?",
                (prefix, user_id, prefix + ":%"),
            )
        ],
    )
    engine_statements = [
        (f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        for table in ("chat_summary", "genome_state", "proactive_outbox", "style_memory")
    ]
    receipt["files"]["openher.db"] = _purge_sqlite(engine_db, engine_statements)
    receipt["files"]["memory.db"] = _purge_sqlite(
        memory_db, [("DELETE FROM memories WHERE user_id = ?", (user_id,))]
    )

    if persona_root:
        target = Path(persona_root) / user_id
        if target.is_dir():
            try:
                shutil.rmtree(target)
                receipt["persona_dirs"].append(str(target))
            except OSError as exc:
                # 回执必须如实：删不掉要写清楚，不能假装删过
                receipt.setdefault("errors", []).append(
                    f"persona_dir {target}: {exc}"
                )

    backups: dict[str, Any] = {
        "dir": str(backup_dir) if backup_dir else "",
        "retention_days": int(backup_retention_days),
    }
    if backup_dir:
        bdir = Path(backup_dir)
        removed: list[str] = []
        stale = 0
        cutoff = now - max(1, int(backup_retention_days)) * 86400
        if bdir.is_dir():
            for f in bdir.iterdir():
                if not f.is_file():
                    continue
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        stale += 1
                        continue
                except OSError:
                    continue
                if user_id in f.name:
                    try:
                        f.unlink()
                        removed.append(f.name)
                    except OSError:
                        continue
        backups["removed"] = removed
        backups["expired_rotated"] = stale
    else:
        backups["removed"] = []
        backups["note"] = "未配置异地备份；数据只在本机数据库与引擎数据目录，随本次删除一并清除"
    receipt["backups"] = backups

    return receipt


def run_due(
    store: DeletionStore,
    purger: Callable[[str], dict],
    *,
    now: float | None = None,
    limit: int = 20,
) -> list[dict]:
    """执行到期的注销，返回 [{user_id, receipt}]。"""
    done: list[dict] = []
    for row in store.due(now=now, limit=limit):
        uid = row["user_id"]
        try:
            receipt = purger(uid)
        except Exception as exc:  # noqa: BLE001 - 单个失败不能拖垮整批
            store.log_scope(uid, "error", 0)
            done.append({"user_id": uid, "error": str(exc)[:200]})
            continue
        for name, payload in (receipt.get("scope") or {}).items():
            deleted = sum(v for v in payload.values() if isinstance(v, int))
            store.log_scope(uid, name, deleted)
        for name, payload in (receipt.get("files") or {}).items():
            deleted = sum(v for v in payload.values() if isinstance(v, int))
            store.log_scope(uid, "file:" + name, deleted)
        store.complete(uid, receipt)
        done.append({"user_id": uid, "receipt": receipt})
    return done


# ── HTTP 层 ─────────────────────────────────────────────────

def build_account_router(
    store: DeletionStore,
    purger: Callable[[str], dict],
    *,
    cooling_days: float = DEFAULT_COOLING_DAYS,
    admin_token: str = "",
    run_due_fn: Callable[..., list] | None = None,
):
    from fastapi import APIRouter, HTTPException, Request

    from .auth.scope import current_auth_user

    router = APIRouter(tags=["account"])

    def _user() -> str:
        user = current_auth_user.get()
        if not user or user == "local":
            raise HTTPException(401, "请先登录")
        return user

    @router.get("/api/me/delete")
    def delete_status() -> dict:
        user = _user()
        row = store.get(user)
        return {
            "pending": bool(row and row["status"] == "pending"),
            "status": row["status"] if row else "none",
            "requested_at": row["requested_at_iso"] if row else "",
            "execute_at": row["execute_at_iso"] if row else "",
            "cooling_days": cooling_days,
            "confirm_word": CONFIRM_WORD,
            "note": "冷静期内可撤销；到期后账号与聊天记录、记忆、订阅记录一并删除，不可恢复。"
            " 如已订阅，请先通过邮件取消订阅，注销不会自动退款。",
        }

    @router.post("/api/me/delete")
    async def request_delete(request: Request) -> dict:
        user = _user()
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if str(payload.get("confirm") or "").strip() != CONFIRM_WORD:
            raise HTTPException(422, f"请把「{CONFIRM_WORD}」两个字输入到确认框")
        row = store.request(user, cooling_days=cooling_days)
        return {
            "pending": True,
            "execute_at": row["execute_at_iso"],
            "status": row["status"],
        }

    @router.post("/api/me/delete/cancel")
    def cancel_delete() -> dict:
        user = _user()
        return {"cancelled": store.cancel(user)}

    @router.post("/api/admin/deletions/run")
    def admin_run(request: Request) -> dict:
        expected = admin_token.strip()
        if not expected:
            raise HTTPException(503, "未配置 ALPHA_ADMIN_TOKEN")
        given = request.headers.get("x-alpha-admin-token", "")
        if given != expected:
            raise HTTPException(403, "无权执行")
        fn = run_due_fn or (lambda **kw: run_due(store, purger, **kw))
        return {"done": fn()}

    return router
