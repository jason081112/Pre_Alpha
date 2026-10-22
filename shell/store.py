"""用户路由配置的持久化（SQLite）。

产品语义：没有默认模型，只有一条持久化的「当前模型」。
用户没有配置过时，get() 返回 None，上层据此返回 route_not_configured。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .providers import Route

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_routing (
    user_id       TEXT PRIMARY KEY,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    fallback_json TEXT NOT NULL DEFAULT '[]',
    updated_at    REAL NOT NULL
);
"""


@dataclass(frozen=True)
class RoutingRecord:
    user_id: str
    route: Route
    fallback: tuple[Route, ...]
    updated_at: float


class RoutingStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def get(self, user_id: str) -> RoutingRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id, provider, model, fallback_json, updated_at "
                "FROM user_routing WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        fallback = tuple(
            Route(item["provider"], item["model"])
            for item in json.loads(row["fallback_json"] or "[]")
        )
        return RoutingRecord(
            user_id=row["user_id"],
            route=Route(row["provider"], row["model"]),
            fallback=fallback,
            updated_at=row["updated_at"],
        )

    def put(self, user_id: str, route: Route, fallback: Sequence[Route] = ()) -> RoutingRecord:
        payload = json.dumps([r.as_dict() for r in fallback], ensure_ascii=False)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_routing (user_id, provider, model, fallback_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "provider = excluded.provider, model = excluded.model, "
                "fallback_json = excluded.fallback_json, updated_at = excluded.updated_at",
                (user_id, route.provider, route.model, payload, now),
            )
            self._conn.commit()
        return RoutingRecord(user_id, route, tuple(fallback), now)

    def delete(self, user_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM user_routing WHERE user_id = ?", (user_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def all_users(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id FROM user_routing ORDER BY updated_at DESC"
            ).fetchall()
        return [row["user_id"] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
