# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""P2 #30（A 部分）字段对照表的离线校验。

不需要 OpenHer 运行；只断言映射表自身的完整性与内部一致性。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOC = ROOT / "docs" / "p2-30-field-mapping.md"
SNAPSHOT = ROOT / "docs" / "data" / "p2-30.json"

# loader.py 实际读取的目标字段（不含载体的前缀）
ALLOWED_TARGETS = {
    "frontmatter.name", "frontmatter.name_zh", "frontmatter.age", "frontmatter.gender",
    "frontmatter.lang", "frontmatter.mbti", "frontmatter.tags", "frontmatter.bio",
    "body.personality", "body.speaking_style", "body.background", "body.behavioral_rules",
    "seeds", "memories", "drive_baseline", "engine_params", "critic", "无",
}
VERDICTS = {"D", "L", "M"}


def _data() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_doc_and_snapshot_exist() -> None:
    assert DOC.is_file(), DOC
    assert SNAPSHOT.is_file(), SNAPSHOT
    data = _data()
    assert data["issues"] == [30]
    assert data["doc"] == "docs/p2-30-field-mapping.md"
    assert "未执行" in data["scope"], "B 部分未执行时必须在 scope 中声明"


def test_mapping_rows_complete() -> None:
    rows = _data()["mapping"]
    assert len(rows) == 21, len(rows)
    assert [r["id"] for r in rows] == list(range(1, 22))
    for row in rows:
        assert row["verdict"] in VERDICTS, row
        assert row["source"], row
        assert row["rule"], row


def test_targets_are_real_loader_fields() -> None:
    """防止写出 OpenHer 根本不读的悬空字段。复合目标（A + B）逐个校验。"""
    for row in _data()["mapping"]:
        for target in row["target"].split("+"):
            assert target.strip() in ALLOWED_TARGETS, row["target"]


def test_stats_match_rows() -> None:
    data = _data()
    expected = {k: sum(1 for r in data["mapping"] if r["verdict"] == k) for k in VERDICTS}
    assert data["stats"] == expected, (data["stats"], expected)
    assert expected == {"D": 3, "L": 10, "M": 8}, expected


def test_pending_items_preserved() -> None:
    pending = _data()["pending"]
    assert len(pending) == 7, pending
    doc = DOC.read_text(encoding="utf-8")
    for item in pending:
        key = item.split("的")[0][:8]
        assert key in doc, item


def test_doc_records_raw_content_trap() -> None:
    """§0.2 的静默丢失结论是本文最重要的发现，必须写进文档。"""
    doc = DOC.read_text(encoding="utf-8")
    assert "raw_content" in doc
    assert "静默消失" in doc or "静默丢失" in doc
    assert "不得保留 `Layer N` 字样" in doc or "不得保留 `Layer N`" in doc


def test_doc_records_comment_mismatch() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert "mbti" in doc and "注入" in doc
    assert "注释与实现不一致" in doc


def test_snapshot_hashes_match_disk() -> None:
    for name, digest in _data()["hashes"].items():
        path = ROOT / name
        assert path.is_file(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name


def test_doc_table_has_21_rows() -> None:
    doc = DOC.read_text(encoding="utf-8")
    for i in range(1, 22):
        assert f"| {i} |" in doc, f"row {i}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} field-mapping tests passed")
