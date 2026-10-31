# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#35 seeds.bin：解析、校验、打包与合并。"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.seeds import (  # noqa: E402
    CONTEXT_KEYS,
    HIGH_MASS_SITUATIONS,
    SITUATION_VECTORS,
    SeedDraft,
    build_from_file,
    parse_seed_text,
    persona_lang,
    read_seeds_bin,
    to_openher_seeds,
    validate_seeds,
    write_seeds_bin,
)

MATERIAL = ROOT / "tests" / "fixtures" / "seeds" / "xiaolan.txt"


def test_fixture_parses_and_validates() -> None:
    drafts, errors = parse_seed_text(MATERIAL.read_text(encoding="utf-8"))
    assert len(drafts) == 8, len(drafts)
    assert errors == []
    assert validate_seeds(drafts) == []


def test_covers_core_situations() -> None:
    drafts, _ = parse_seed_text(MATERIAL.read_text(encoding="utf-8"))
    situations = {d.situation for d in drafts}
    assert "被忽略" in situations
    assert "冲突升级" in situations


def test_all_situations_are_known() -> None:
    drafts, _ = parse_seed_text(MATERIAL.read_text(encoding="utf-8"))
    for draft in drafts:
        assert draft.situation in SITUATION_VECTORS


def test_vectors_have_eight_dims() -> None:
    assert len(CONTEXT_KEYS) == 8
    for situation, vector in SITUATION_VECTORS.items():
        assert len(vector) == 8, situation
        for index, value in enumerate(vector):
            if CONTEXT_KEYS[index] == "user_emotion":
                assert -1.0 <= value <= 1.0
            else:
                assert 0.0 <= value <= 1.0, (situation, index, value)


def test_mass_for_conflict_situations() -> None:
    drafts, _ = parse_seed_text(MATERIAL.read_text(encoding="utf-8"))
    seeds = to_openher_seeds(drafts)
    for draft, seed in zip(drafts, seeds):
        expected = 2.0 if draft.situation in HIGH_MASS_SITUATIONS else 1.0
        assert seed["mass"] == expected, (draft.situation, seed["mass"])
    assert any(seed["mass"] == 2.0 for seed in seeds)


def test_seed_shape_matches_upstream() -> None:
    drafts, _ = parse_seed_text(MATERIAL.read_text(encoding="utf-8"))
    seeds = to_openher_seeds(drafts)
    for seed in seeds:
        assert set(seed) == {"user_input", "monologue", "reply", "vector", "mass", "lang"}
        assert seed["monologue"].strip()
        assert seed["reply"].strip()
        assert len(seed["vector"]) == 8


def test_reject_short_monologue() -> None:
    drafts = [SeedDraft(scene="s", monologue="太短", reply="嗯", situation="被忽略"),
              SeedDraft(scene="s2", monologue="够长够长够长够长够长够长够长够长", reply="好",
                        situation="冲突升级")]
    errors = validate_seeds(drafts)
    assert any("心里" in e for e in errors)


def test_reject_long_reply() -> None:
    drafts = [SeedDraft(scene="s", monologue="这是一段足够长的内心独白内容用于测试长度限制",
                        reply="这句回复特别特别特别特别特别特别特别特别特别特别特别长超过四十字 " * 2,
                        situation="被忽略"),
              SeedDraft(scene="s2", monologue="另一段足够长的内心独白内容用于凑齐情境要求",
                        reply="好", situation="冲突升级")]
    errors = validate_seeds(drafts)
    assert any("回复" in e for e in errors)


def test_reject_same_monologue_and_reply() -> None:
    text = "算了，你忙。我早就习惯了这种事情。"
    drafts = [SeedDraft(scene="s", monologue=text, reply=text, situation="被忽略"),
              SeedDraft(scene="s2", monologue="另一段足够长的内心独白内容用于凑齐情境要求",
                        reply="好", situation="冲突升级")]
    errors = validate_seeds(drafts)
    assert any("落差" in e for e in errors)


def test_reject_action_markers() -> None:
    drafts = [SeedDraft(scene="s", monologue="（叹了口气）我真的不想再说了，可是又忍不住想他",
                        reply="嗯", situation="被忽略"),
              SeedDraft(scene="s2", monologue="另一段足够长的内心独白内容用于凑齐情境要求",
                        reply="好", situation="冲突升级")]
    errors = validate_seeds(drafts)
    assert any("action_paren" in e for e in errors)


def test_reject_unknown_situation() -> None:
    drafts = [SeedDraft(scene="s", monologue="这是一段足够长的内心独白内容用于测试长度限制",
                        reply="嗯", situation="随便什么"),
              SeedDraft(scene="s2", monologue="另一段足够长的内心独白内容用于凑齐情境要求",
                        reply="好", situation="冲突升级")]
    errors = validate_seeds(drafts)
    assert any("情境" in e for e in errors)


def test_require_core_situations() -> None:
    drafts = [SeedDraft(scene="s", monologue="这是一段足够长的内心独白内容用于测试长度限制",
                        reply="嗯", situation="日常闲聊")]
    errors = validate_seeds(drafts)
    assert any("缺少「被忽略」" in e for e in errors)


def test_parse_reports_missing_field() -> None:
    drafts, errors = parse_seed_text("场景: 只有场景\n回复: 嗯")
    assert drafts == []
    assert any("缺少" in e for e in errors)


def test_bin_roundtrip_and_merge() -> None:
    drafts, _ = parse_seed_text(MATERIAL.read_text(encoding="utf-8"))
    seeds = to_openher_seeds(drafts)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "seeds.bin"
        write_seeds_bin(path, "luna", [{"user_input": "x", "monologue": "y", "reply": "z",
                                       "vector": [0] * 8, "mass": 1.0, "lang": "zh"}])
        result = write_seeds_bin(path, "xiaolan", seeds)
        assert result["added"] == 8
        assert result["personas"] == 2

        data = read_seeds_bin(path)
        assert set(data) == {"luna", "xiaolan"}
        assert len(data["xiaolan"]) == 8
        raw = path.read_bytes()
        decoded = json.loads(gzip.decompress(raw).decode("utf-8"))
        assert decoded["xiaolan"][0]["reply"]


def test_replace_existing_persona() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "seeds.bin"
        write_seeds_bin(path, "xiaolan", [{"user_input": "a", "monologue": "b", "reply": "c",
                                          "vector": [0] * 8, "mass": 1.0, "lang": "zh"}])
        result = write_seeds_bin(path, "xiaolan", [{"user_input": "d", "monologue": "e",
                                                    "reply": "f", "vector": [0] * 8,
                                                    "mass": 1.0, "lang": "zh"}])
        assert result["replaced"] == 1
        assert len(read_seeds_bin(path)["xiaolan"]) == 1


def test_build_from_file_ok() -> None:
    seeds, errors, drafts = build_from_file(MATERIAL, persona_id="xiaolan")
    assert errors == []
    assert len(seeds) == 8
    assert len(drafts) == 8


def test_persona_lang_defaults_to_zh() -> None:
    from bridge.model import Asserted, Identity, PersonProfile

    profile = PersonProfile(persona_id="a", identity=Identity(name=Asserted.source("甲")))
    assert persona_lang(profile) == "zh"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} seeds tests passed")
