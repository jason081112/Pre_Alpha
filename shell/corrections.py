# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""纠错层（#39）：运行期可写的「别说这个，改成那个」。

设计要点：

* **不写 SOUL.md**。SOUL.md 是 golden 快照锁住的静态产物（#43），运行期条目写进去会让
  每次用户纠正都漂移快照，而且 loader 只认 4 个固定 H2（#28），没有多余槽位。
  纠错条目独立持久化，由 prompt 组装时额外注入（注入文本见 `render_section`）。
* **可停用而非只可删**。用户可能只是想临时压住一句话，`active` 字段让停用可逆、可审计。
* **幂等导入**。`seed_from_profile` 把静态 `profile.corrections` 灌进来时不重复插入。
* **重复纠正覆盖而不是堆积**。同一 (persona, user, scene, wrong) 只保留最新 correct ——
  否则用户改三次就会得到三段互相矛盾的规则。
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS corrections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    persona_id TEXT NOT NULL,
    user_id    TEXT NOT NULL DEFAULT 'local',
    scene      TEXT NOT NULL DEFAULT '',
    wrong      TEXT NOT NULL,
    correct    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'runtime',
    active     INTEGER NOT NULL DEFAULT 1,
    note       TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_corrections_key
    ON corrections (persona_id, user_id, scene, wrong);
CREATE INDEX IF NOT EXISTS idx_corrections_lookup
    ON corrections (persona_id, user_id, active);
"""

SOURCES = ("runtime", "distilled", "import")
MAX_SECTION_ITEMS = 8  # 注入上限：多了会挤掉人格本身

_ACTION_MARKERS = (
    re.compile(r"[（(][^）)]{0,20}[）)]"),
    re.compile(r"\*[^*\n]{1,20}\*"),
    re.compile(r"[【\[][^】\]]{0,20}[】\]]"),
)
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


@dataclass(frozen=True)
class CorrectionRecord:
    id: int
    persona_id: str
    user_id: str
    scene: str
    wrong: str
    correct: str
    source: str
    active: bool
    note: str | None
    created_at: float
    updated_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "persona_id": self.persona_id,
            "user_id": self.user_id,
            "scene": self.scene,
            "wrong": self.wrong,
            "correct": self.correct,
            "source": self.source,
            "active": self.active,
            "note": self.note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CorrectionError(ValueError):
    """条目非法（空、过长、含动作标记/隐私）。"""


def validate_correction(scene: str, wrong: str, correct: str) -> list[str]:
    errors: list[str] = []
    scene, wrong, correct = (scene or "").strip(), (wrong or "").strip(), (correct or "").strip()

    if not wrong:
        errors.append("wrong 不能为空")
    elif len(wrong) > 200:
        errors.append(f"wrong 过长（{len(wrong)} > 200）")
    if not correct:
        errors.append("correct 不能为空")
    elif len(correct) > 200:
        errors.append(f"correct 过长（{len(correct)} > 200）")
    if len(scene) > 60:
        errors.append(f"scene 过长（{len(scene)} > 60）")
    if wrong and correct and wrong == correct:
        errors.append("wrong 与 correct 相同，没有纠错意义")

    for text, label in ((wrong, "wrong"), (correct, "correct"), (scene, "scene")):
        if not text:
            continue
        for pattern in _ACTION_MARKERS:
            if pattern.search(text):
                errors.append(f"{label} 含动作/旁白标记，会被 clean_action_markers 剥掉")
                break
        if _PHONE.search(text) or _EMAIL.search(text):
            errors.append(f"{label} 含疑似隐私信息")
    return errors


class CorrectionStore:
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
        wrong: str,
        correct: str,
        *,
        scene: str = "",
        user_id: str = "local",
        source: str = "runtime",
        note: str | None = None,
    ) -> CorrectionRecord:
        errors = validate_correction(scene, wrong, correct)
        if errors:
            raise CorrectionError("；".join(errors))
        if source not in SOURCES:
            raise CorrectionError(f"source 必须是 {SOURCES} 之一")

        scene, wrong, correct = scene.strip(), wrong.strip(), correct.strip()
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO corrections "
                "(persona_id, user_id, scene, wrong, correct, source, active, note, "
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?) "
                "ON CONFLICT(persona_id, user_id, scene, wrong) DO UPDATE SET "
                "correct = excluded.correct, source = excluded.source, "
                "note = excluded.note, active = 1, updated_at = excluded.updated_at",
                (persona_id, user_id, scene, wrong, correct, source, note, now, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM corrections WHERE persona_id = ? AND user_id = ? "
                "AND scene = ? AND wrong = ?",
                (persona_id, user_id, scene, wrong),
            ).fetchone()
        return _row_to_record(row)

    def set_active(self, correction_id: int, active: bool) -> CorrectionRecord:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE corrections SET active = ?, updated_at = ? WHERE id = ?",
                (1 if active else 0, now, correction_id),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                raise CorrectionError(f"条目 {correction_id} 不存在")
            row = self._conn.execute(
                "SELECT * FROM corrections WHERE id = ?", (correction_id,)
            ).fetchone()
        return _row_to_record(row)

    def delete(self, correction_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM corrections WHERE id = ?", (correction_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def seed_from_profile(self, persona_id: str, corrections: Iterable, *,
                          user_id: str = "local") -> int:
        """把静态 `profile.corrections` 灌入，幂等（已存在则跳过而不覆盖运行期修改）。"""
        added = 0
        now = time.time()
        with self._lock:
            for item in corrections:
                scene = (getattr(item, "scene", "") or "").strip()
                wrong = (getattr(item, "wrong", "") or "").strip()
                correct = (getattr(item, "correct", "") or "").strip()
                if not wrong or not correct:
                    continue
                cur = self._conn.execute(
                    "INSERT INTO corrections "
                    "(persona_id, user_id, scene, wrong, correct, source, active, note, "
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'distilled', 1, NULL, ?, ?) "
                    "ON CONFLICT(persona_id, user_id, scene, wrong) DO NOTHING",
                    (persona_id, user_id, scene, wrong, correct, now, now),
                )
                added += cur.rowcount
            self._conn.commit()
        return added

    # ------------------------------------------------------------ 读

    def list(self, persona_id: str, *, user_id: str = "local",
             include_inactive: bool = False) -> list[CorrectionRecord]:
        sql = ("SELECT * FROM corrections WHERE persona_id = ? AND user_id = ?"
               + ("" if include_inactive else " AND active = 1")
               + " ORDER BY source = 'runtime' DESC, created_at DESC, id DESC")
        with self._lock:
            rows = self._conn.execute(sql, (persona_id, user_id)).fetchall()
        return [_row_to_record(r) for r in rows]

    def stats(self, persona_id: str, *, user_id: str = "local") -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active, "
                "SUM(CASE WHEN source = 'runtime' THEN 1 ELSE 0 END) AS runtime "
                "FROM corrections WHERE persona_id = ? AND user_id = ?",
                (persona_id, user_id),
            ).fetchone()
        return {"total": row["total"] or 0, "active": row["active"] or 0,
                "runtime": row["runtime"] or 0}

    # ------------------------------------------------------------ 注入

    def render_section(self, persona_id: str, *, user_id: str = "local",
                       lang: str = "zh", limit: int = MAX_SECTION_ITEMS) -> str:
        """生成要拼进 prompt 的纠错段。没有条目时返回空串（不产生空壳标记）。"""
        records = self.list(persona_id, user_id=user_id)[:limit]
        if not records:
            return ""

        zh = lang.lower().startswith("zh")
        head = ("【纠错规则】以下说法不要用，遇到对应场景时改用右边的说法："
                if zh else
                "【Corrections】Do not use the phrasing on the left; use the right one instead:")
        lines = [head]
        for item in records:
            scene = f"[{item.scene}] " if item.scene else ""
            if zh:
                lines.append(f"- {scene}不要说「{item.wrong}」，改说「{item.correct}」")
            else:
                lines.append(f'- {scene}never "{item.wrong}" → say "{item.correct}"')
        return "\n".join(lines)


def _row_to_record(row: sqlite3.Row) -> CorrectionRecord:
    return CorrectionRecord(
        id=row["id"],
        persona_id=row["persona_id"],
        user_id=row["user_id"],
        scene=row["scene"],
        wrong=row["wrong"],
        correct=row["correct"],
        source=row["source"],
        active=bool(row["active"]),
        note=row["note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


__all__ = [
    "SCHEMA", "SOURCES", "MAX_SECTION_ITEMS", "CorrectionError", "CorrectionRecord",
    "CorrectionStore", "validate_correction",
]
