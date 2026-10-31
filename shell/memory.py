# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""事实 / 共同记忆 → SQLite FTS5（#36）。

与 `shell/corrections.py` 的分工：

* **纠错层**：不要说这个，改说那个（约束）。
* **记忆层**：发生过这些事（事实、偏好、事件），可被检索、可注入 prompt。

为什么自建而不是直接用 vendor 的 `MemoryStore`：vendor 的表只有
`content / category / importance / source_turn`，**没有 role，也没有 source**。
本 issue 要求「role / 时间戳 / source」，vendor 结构装不下。桥接层的职责就是把我方模型
映射到引擎，所以这里自建，并留 `export_for_engine()` 把条目转成 vendor 能吃的形式。

中文检索的处理（实测结论）：SQLite 3.46 的 `trigram` tokenizer 对**少于 3 个字**的查询
返回空（"加班""对方" 都查不到），`unicode61` 又把整句当一个 token（只能整句匹配）。
所以写入时把中文逐字切分喂给 FTS（召回层），再用 `LIKE` 做精确子串精筛（准确层）。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL DEFAULT 'local',
    persona_id  TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'user',
    content     TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'fact',
    source      TEXT NOT NULL DEFAULT 'runtime',
    importance  REAL NOT NULL DEFAULT 0.5,
    source_turn TEXT,
    tokens      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_lookup
    ON memories (user_id, persona_id, category, importance DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_memories_dedup
    ON memories (user_id, persona_id, content);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5 (tokens, content='memories', content_rowid='id', tokenize='unicode61');

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts (rowid, tokens) VALUES (new.id, new.tokens);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts (memories_fts, rowid, tokens)
    VALUES ('delete', old.id, old.tokens);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts (memories_fts, rowid, tokens)
    VALUES ('delete', old.id, old.tokens);
    INSERT INTO memories_fts (rowid, tokens) VALUES (new.id, new.tokens);
END;
"""

ROLES = ("user", "assistant", "system", "fact")
CATEGORIES = ("fact", "event", "preference", "conversation")
SOURCES = ("runtime", "distilled", "import")
MAX_CONTENT = 500
MAX_CONTEXT_ITEMS = 8

_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


class MemoryError(ValueError):
    """记忆条目非法。"""


def tokenize(text: str) -> str:
    """中文逐字切分 + 英文/数字整词，供 unicode61 索引。

    例：`对方多次因加班` → `对 方 多 次 因 加 班`
         `后端 API 挂了` → `后 端 api 挂 了`
    """
    return " ".join(t.lower() for t in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text or ""))


def validate_memory(content: str, *, role: str = "user", category: str = "fact") -> list[str]:
    errors: list[str] = []
    text = (content or "").strip()
    if not text:
        errors.append("内容不能为空")
    elif len(text) > MAX_CONTENT:
        errors.append(f"内容过长（{len(text)} > {MAX_CONTENT}）")
    if role not in ROLES:
        errors.append(f"role 必须是 {ROLES} 之一")
    if category not in CATEGORIES:
        errors.append(f"category 必须是 {CATEGORIES} 之一")
    if _PHONE.search(text) or _EMAIL.search(text):
        errors.append("内容含疑似隐私信息")
    if re.search(r"【内心独白】|【最终回复】", text):
        errors.append("内容含 few-shot 空壳标记")
    return errors


@dataclass(frozen=True)
class MemoryRecord:
    id: int
    user_id: str
    persona_id: str
    role: str
    content: str
    category: str
    source: str
    importance: float
    source_turn: str | None
    created_at: float
    updated_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id, "user_id": self.user_id, "persona_id": self.persona_id,
            "role": self.role, "content": self.content, "category": self.category,
            "source": self.source, "importance": self.importance,
            "source_turn": self.source_turn,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


class MemoryStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------ 写

    def add(
        self,
        persona_id: str,
        content: str,
        *,
        role: str = "user",
        category: str = "fact",
        source: str = "runtime",
        importance: float = 0.5,
        source_turn: str | None = None,
        user_id: str = "local",
    ) -> MemoryRecord:
        errors = validate_memory(content, role=role, category=category)
        if errors:
            raise MemoryError("；".join(errors))
        if source not in SOURCES:
            raise MemoryError(f"source 必须是 {SOURCES} 之一")
        importance = min(1.0, max(0.0, float(importance)))

        text = content.strip()
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories (user_id, persona_id, role, content, category, source, "
                "importance, source_turn, tokens, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, persona_id, content) DO UPDATE SET "
                "importance = MAX(memories.importance, excluded.importance), "
                "category = excluded.category, source = excluded.source, "
                "updated_at = excluded.updated_at",
                (user_id, persona_id, role, text, category, source, importance,
                 source_turn, tokenize(text), now, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM memories WHERE user_id = ? AND persona_id = ? AND content = ?",
                (user_id, persona_id, text),
            ).fetchone()
        return _row_to_record(row)

    def add_facts(self, persona_id: str, facts: Iterable[str], *,
                  source: str = "distilled", importance: float = 0.9,
                  user_id: str = "local", source_turn: str | None = None) -> int:
        """批量写入高重要度事实。重复内容由唯一键吸收，返回新增条数。"""
        added = 0
        for fact in facts:
            text = (fact or "").strip()
            if not text:
                continue
            before = self.count(persona_id, user_id=user_id)
            try:
                self.add(persona_id, text, role="fact", category="fact", source=source,
                         importance=importance, source_turn=source_turn, user_id=user_id)
            except MemoryError:
                continue
            if self.count(persona_id, user_id=user_id) > before:
                added += 1
        return added

    def seed_from_profile(self, profile: dict, *, source: str = "distilled",
                          user_id: str = "local") -> int:
        """把 `relation.defining_facts` + `memory_signature` 灌入，幂等。"""
        persona_id = profile.get("persona_id", "")
        facts: list[str] = []

        def val(node):
            return node.get("value") if isinstance(node, dict) else node

        relation = profile.get("relation") or {}
        for item in relation.get("defining_facts") or []:
            text = val(item)
            if text:
                facts.append(str(text))
        for item in profile.get("memory_signature") or []:
            text = val(item)
            if text:
                facts.append(str(text))

        return self.add_facts(persona_id, facts, source=source,
                              source_turn="profile.json", user_id=user_id)

    def delete(self, memory_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------ 读

    def search(self, persona_id: str, query: str, *, limit: int = 5,
               user_id: str = "local") -> list[MemoryRecord]:
        """FTS5 召回 + LIKE 精筛：FTS 负责快，LIKE 负责准（见模块 docstring）。"""
        query = (query or "").strip()
        if not query:
            return self.get_recent(persona_id, limit=limit, user_id=user_id)

        tokens = tokenize(query)
        if not tokens:
            return []

        sql = (
            "SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.rowid "
            "WHERE memories_fts MATCH ? AND m.persona_id = ? AND m.user_id = ? "
            "AND m.content LIKE ? ORDER BY m.importance DESC, m.created_at DESC LIMIT ?"
        )
        with self._lock:
            try:
                rows = self._conn.execute(
                    sql, (tokens, persona_id, user_id, f"%{query}%", limit)
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                # 单字/符号等边角查询走 FTS 可能抛错或无解，退化为朴素子串查找
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE persona_id = ? AND user_id = ? "
                    "AND content LIKE ? ORDER BY importance DESC, created_at DESC LIMIT ?",
                    (persona_id, user_id, f"%{query}%", limit),
                ).fetchall()
        return [_row_to_record(r) for r in rows]

    def get_recent(self, persona_id: str, *, limit: int = 10,
                   user_id: str = "local") -> list[MemoryRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE persona_id = ? AND user_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (persona_id, user_id, limit),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def get_important(self, persona_id: str, *, min_importance: float = 0.7,
                      limit: int = 10, user_id: str = "local") -> list[MemoryRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE persona_id = ? AND user_id = ? "
                "AND importance >= ? ORDER BY importance DESC, created_at DESC LIMIT ?",
                (persona_id, user_id, min_importance, limit),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def count(self, persona_id: str, *, user_id: str = "local") -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE persona_id = ? AND user_id = ?",
                (persona_id, user_id),
            ).fetchone()
        return row["n"]

    def stats(self, persona_id: str, *, user_id: str = "local") -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS total, SUM(role='user') AS user_rows, "
                "SUM(role='assistant') AS assistant_rows, SUM(role='fact') AS fact_rows "
                "FROM memories WHERE persona_id = ? AND user_id = ?",
                (persona_id, user_id),
            ).fetchone()
        return {
            "total": row["total"] or 0,
            "user": row["user_rows"] or 0,
            "assistant": row["assistant_rows"] or 0,
            "fact": row["fact_rows"] or 0,
        }

    # ------------------------------------------------------------ 注入与导出

    def build_context(self, persona_id: str, *, query: str = "", max_items: int = MAX_CONTEXT_ITEMS,
                      user_id: str = "local", lang: str = "zh") -> str:
        """拼 prompt 用的记忆段。策略同 vendor：重要事实 → 相关命中 → 最近补齐，去重。"""
        picked: list[MemoryRecord] = []
        seen: set[str] = set()

        def take(records: Iterable[MemoryRecord]) -> None:
            for record in records:
                if record.content in seen:
                    continue
                seen.add(record.content)
                picked.append(record)
                if len(picked) >= max_items:
                    return

        take(self.get_important(persona_id, min_importance=0.8, limit=4, user_id=user_id))
        if query and len(picked) < max_items:
            take(self.search(persona_id, query, limit=max_items, user_id=user_id))
        if len(picked) < max_items:
            take(self.get_recent(persona_id, limit=max_items, user_id=user_id))

        if not picked:
            return ""

        zh = lang.lower().startswith("zh")
        head = "【你记得的事】" if zh else "【What you remember】"
        lines = [head]
        for record in picked:
            lines.append(f"- [{record.category}] {record.content}")
        return "\n".join(lines)

    def export_for_engine(self, persona_id: str, *, user_id: str = "local") -> list[dict]:
        """转成 vendor `MemoryStore.add()` 能吃的参数（vendor 没有 role/source 字段）。"""
        out = []
        for record in self.get_recent(persona_id, limit=10_000, user_id=user_id):
            out.append({
                "user_id": record.user_id,
                "persona_id": record.persona_id,
                "content": record.content,
                "category": record.category,
                "importance": record.importance,
                "source_turn": record.source_turn,
            })
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"], user_id=row["user_id"], persona_id=row["persona_id"],
        role=row["role"], content=row["content"], category=row["category"],
        source=row["source"], importance=row["importance"],
        source_turn=row["source_turn"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


__all__ = [
    "SCHEMA", "ROLES", "CATEGORIES", "SOURCES", "MAX_CONTENT", "MAX_CONTEXT_ITEMS",
    "MemoryError", "MemoryRecord", "MemoryStore", "tokenize", "validate_memory",
]
