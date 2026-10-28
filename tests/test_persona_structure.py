# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""P2 #28 产出物结构归档的回归校验。

校验三件事：
1. docs/p2-28-persona-structure.md 记录的结构与 golden 产物实际一致；
2. docs/data/p2-28.json 的哈希快照与磁盘一致；
3. 纠错层字段名与 Distilly prompt 原文一致（vendor 缺失时跳过）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN = ROOT / "tests" / "fixtures" / "golden"
DOC = ROOT / "docs" / "p2-28-persona-structure.md"
SNAPSHOT = ROOT / "docs" / "data" / "p2-28.json"
DISTILLY_PROMPT = ROOT / "vendor" / "distilly" / "prompts" / "shared" / "correction_handler.md"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_doc_and_snapshot_exist() -> None:
    assert DOC.is_file(), DOC
    assert SNAPSHOT.is_file(), SNAPSHOT
    data = _snapshot()
    assert data["issues"] == [28]
    assert data["doc"] == "docs/p2-28-persona-structure.md"


def test_doc_covers_required_sections() -> None:
    text = DOC.read_text(encoding="utf-8")
    for heading in (
        "## 2. 六层人格字段清单",
        "## 3. 口头禅字段",
        "## 4. 纠错层",
        "## 5. OpenHer 落点字段（源码核实）",
        "## 6. Distilly → OpenHer 映射判定",
        "## 7. 待确认清单（insufficient source material）",
    ):
        assert heading in text, heading


def test_layer_titles_match_golden() -> None:
    titles = _snapshot()["structure"]["layer_titles"]
    for case, layers in titles.items():
        persona = (GOLDEN / case / "persona.md").read_text(encoding="utf-8")
        for layer, title in layers.items():
            assert f"## {layer}: {title}" in persona, f"{case}: {layer}={title}"
        assert "## Correction Log" in persona, case
        assert "## General Behavior Principles" in persona, case


def test_correction_log_is_empty_in_goldens() -> None:
    """golden 里纠错层没有条目；文档必须与之一致，不得虚构条目格式。"""
    data = _snapshot()
    assert data["structure"]["correction_fields"] == ["scene", "wrong", "correct"]
    for case in data["structure"]["layer_titles"]:
        persona = (GOLDEN / case / "persona.md").read_text(encoding="utf-8")
        tail = persona.split("## Correction Log", 1)[1].split("##", 1)[0]
        assert "(No entries yet)" in tail, case
        meta = json.loads((GOLDEN / case / "meta.json").read_text(encoding="utf-8"))
        assert meta["corrections_count"] == 0, case


def test_correction_fields_come_from_distilly_prompt() -> None:
    if not DISTILLY_PROMPT.is_file():
        print("SKIP: vendor/distilly 未就绪")
        return
    prompt = DISTILLY_PROMPT.read_text(encoding="utf-8")
    for field in ("scene", "wrong", "correct"):
        assert f'"{field}"' in prompt, field
    assert "50" in prompt, "维护上限未在 prompt 中确认"


def test_snapshot_hashes_match_disk() -> None:
    data = _snapshot()["hashes"]
    for case, files in data["goldens"].items():
        for name, digest in files.items():
            assert _sha256(GOLDEN / case / name) == digest, f"{case}/{name}"
    for name, digest in data["sources"].items():
        assert _sha256(ROOT / name) == digest, name


def test_openher_slots_match_doc() -> None:
    """文档第 5 节列出的落点字段必须与快照一致。"""
    data = _snapshot()
    slots = data["openher_slots"]
    assert slots["drives"] == ["connection", "novelty", "expression", "safety", "play"]
    assert slots["body_sections"] == [
        "personality",
        "speaking_style",
        "background",
        "behavioral_rules",
    ]
    text = DOC.read_text(encoding="utf-8")
    for drive in slots["drives"]:
        assert f"`{drive}`" in text, drive
    for section in ("性格", "说话风格", "背景故事", "行为规则"):
        assert section in text, section


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} persona-structure tests passed")
