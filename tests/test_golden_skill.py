# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""P2 #26/#27 合成 Skill golden 的结构校验。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN = ROOT / "tests" / "fixtures" / "golden"
CASES = {
    "demo-colleague": {
        "character": "colleague",
        "fixture": "tests/fixtures/chats/colleague_zh.canonical.json",
    },
    "demo-relationship": {
        "character": "relationship",
        "fixture": "tests/fixtures/chats/relationship_zh.canonical.json",
    },
}
REQUIRED_FILES = {
    "SKILL.md",
    "work.md",
    "persona.md",
    "work_skill.md",
    "persona_skill.md",
    "manifest.json",
    "meta.json",
    "PROVENANCE.md",
}
PII_PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "absolute_path": re.compile(r"(?:/workspaces/|/home/|/tmp/)"),
    "api_key": re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
}


def _load(case: str, name: str) -> dict:
    return json.loads((GOLDEN / case / name).read_text(encoding="utf-8"))


def test_golden_files_exist() -> None:
    assert GOLDEN.is_dir(), GOLDEN
    for case in CASES:
        missing = REQUIRED_FILES - {p.name for p in (GOLDEN / case).iterdir()}
        assert not missing, f"{case}: missing {sorted(missing)}"


def test_golden_metadata() -> None:
    for case, expected in CASES.items():
        meta = _load(case, "meta.json")
        assert meta["synthetic"] is True, case
        assert meta["character"] == expected["character"], case
        assert meta["classification"]["language"] == "zh-CN", case
        assert meta["slug"] == case, case
        assert expected["fixture"] in meta["knowledge_sources"], case
        assert (ROOT / expected["fixture"]).is_file(), case
        assert meta["generation"]["engine"] == "distilly", case
        assert meta["generation"]["created_from"] == [expected["fixture"]], case


def test_golden_manifest() -> None:
    for case in CASES:
        manifest = _load(case, "manifest.json")
        assert manifest["character"] == CASES[case]["character"], case
        assert manifest["display_name"], case
        for entrypoint in manifest["entrypoints"].values():
            assert (GOLDEN / case / entrypoint).is_file(), f"{case}: {entrypoint}"


def test_golden_skill_parts() -> None:
    for case in CASES:
        skill = (GOLDEN / case / "SKILL.md").read_text(encoding="utf-8")
        assert "PART A" in skill, case
        assert "PART B" in skill, case
        persona = (GOLDEN / case / "persona.md").read_text(encoding="utf-8")
        for layer in range(6):
            assert f"## Layer {layer}" in persona, f"{case}: Layer {layer}"
        assert "## Correction Log" in persona, case
        assert (GOLDEN / case / "PROVENANCE.md").read_text(encoding="utf-8").count(
            "synthetic test fixture"
        ) >= 1, case


def test_golden_has_no_obvious_pii_or_paths() -> None:
    for case in CASES:
        for path in (GOLDEN / case).iterdir():
            if path.suffix not in {".md", ".json"}:
                continue
            text = path.read_text(encoding="utf-8")
            for label, pattern in PII_PATTERNS.items():
                match = pattern.search(text)
                assert match is None, f"{case}/{path.name}: {label}: {match.group(0)!r}"


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} golden tests passed")
