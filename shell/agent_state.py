"""ChatAgent 补充状态持久化。

上游 `StateStore` 只序列化 Genome Agent（驱力/权重）和 DriveMetabolism，
没有保存 `ChatAgent._relationship_ema`、`_turn_count` 等外壳层状态。
这里用独立 SQLite 表补上，冷启动时回填到新建的 ChatAgent。

边界：
- 只存数值/字典状态，不存聊天原文、不存 Key
- 不修改 vendor；由 shell.app 在运行时包装上游函数
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_extra (
    user_id      TEXT NOT NULL,
    persona_id   TEXT NOT NULL,
    extra_json   TEXT NOT NULL DEFAULT '{}',
    updated_at   REAL NOT NULL,
    PRIMARY KEY (user_id, persona_id)
);
"""

# ChatAgent 属性 → JSON 字段
_FIELDS = {
    "_relationship_ema": "relationship",
    "_turn_count": "turn_count",
    "_last_reward": "last_reward",
    "_last_modality": "last_modality",
    "_last_signals": "last_signals",
    "_last_drive_satisfaction": "last_drive_satisfaction",
    "_prev_signals": "prev_signals",
    "_search_hit": "search_hit",
    "_search_timeout": "search_timeout",
    "_search_fallback": "search_fallback",
    "_search_relevant_used": "search_relevant_used",
}

_SCALARS = (int, float, bool, str)
_DICTS = (dict,)


def _extract(agent: Any) -> dict:
    data: dict = {}
    for attr, key in _FIELDS.items():
        value = getattr(agent, attr, None)
        if isinstance(value, _DICTS):
            data[key] = value
        elif isinstance(value, _SCALARS):
            data[key] = value
    return data


class AgentExtraStore:
    """按 (user_id, persona_id) 保存 ChatAgent 的外壳状态。"""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def save(self, agent: Any) -> bool:
        """保存一轮之后的状态；空 Agent 不写，避免覆盖已有关系值。"""
        turn_count = int(getattr(agent, "_turn_count", 0) or 0)
        relationship = getattr(agent, "_relationship_ema", None)
        if turn_count <= 0 and not relationship:
            return False

        user_id = getattr(agent, "user_id", "")
        persona = getattr(agent, "persona", None)
        persona_id = getattr(persona, "persona_id", "") if persona else ""
        if not user_id or not persona_id:
            return False

        payload = _extract(agent)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_extra (user_id, persona_id, extra_json, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, persona_id) DO UPDATE SET "
                "extra_json = excluded.extra_json, updated_at = excluded.updated_at",
                (user_id, persona_id, encoded, now),
            )
            self._conn.commit()
        return True

    def load(self, user_id: str, persona_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT extra_json FROM agent_extra WHERE user_id = ? AND persona_id = ?",
                (user_id, persona_id),
            ).fetchone()
        if row is None:
            return {}
        try:
            data = json.loads(row["extra_json"] or "{}")
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def apply(self, agent: Any, data: dict) -> bool:
        """只在全新的 ChatAgent 上回填，避免覆盖活跃会话。"""
        if not data:
            return False
        if getattr(agent, "_turn_count", 0):
            return False
        if getattr(agent, "_relationship_ema", None):
            return False

        for attr, key in _FIELDS.items():
            if key not in data:
                continue
            value = data[key]
            if attr in ("_relationship_ema", "_last_signals",
                        "_last_drive_satisfaction", "_prev_signals"):
                if isinstance(value, dict):
                    setattr(agent, attr, dict(value))
            elif attr == "_last_modality":
                if isinstance(value, str):
                    setattr(agent, attr, value)
            elif attr == "_last_reward":
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    setattr(agent, attr, float(value))
            else:
                if isinstance(value, int) and not isinstance(value, bool):
                    setattr(agent, attr, value)
        return True

    def size(self, user_id: str, persona_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT length(extra_json) FROM agent_extra "
                "WHERE user_id = ? AND persona_id = ?",
                (user_id, persona_id),
            ).fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
