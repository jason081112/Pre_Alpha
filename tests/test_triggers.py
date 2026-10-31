# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#38 触发/冲突/修复 → critic 特征 + phase_threshold 校准。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.model import Asserted, Expression, Example, Identity, PersonProfile, profile_from_dict  # noqa: E402
from bridge.triggers import (  # noqa: E402
    PHASE_BASE,
    PHASE_MAX,
    PHASE_MIN,
    SIGNALS,
    analyze,
    critic_persona_section,
    drive_sensitivity,
    phase_threshold,
    signal_priors,
)

PROFILES = ROOT / "tests" / "fixtures" / "profiles"


def _load(persona_id: str) -> PersonProfile:
    return profile_from_dict(json.loads((PROFILES / f"{persona_id}.json").read_text(encoding="utf-8")))


def _bare(**kwargs) -> PersonProfile:
    return PersonProfile(persona_id="p", identity=Identity(name=Asserted.source("甲")), **kwargs)


# ---------------------------------------------------------------- signal priors


def test_all_eight_signals_present() -> None:
    priors = signal_priors(_load("xiaolan"))
    assert set(priors) == set(SIGNALS)


def test_directness_from_sentence_length() -> None:
    short = _bare(expression=Expression(examples=[Example("s", "算了。")]))
    long_ex = _bare(expression=Expression(examples=[Example("s", "这是一句明显更长的例句用来测试长度判断")]))
    assert signal_priors(short)["directness"].value > signal_priors(long_ex)["directness"].value


def test_no_examples_means_missing_directness() -> None:
    priors = signal_priors(_bare())
    assert priors["directness"].provenance.value == "missing"
    assert priors["directness"].value is None


def test_playfulness_only_when_declared() -> None:
    assert signal_priors(_load("xiaolan"))["playfulness"].provenance.value == "missing"
    declared = _bare()
    declared.meta = {"playfulness": 0.8}
    priors = signal_priors(declared)
    assert priors["playfulness"].value == 0.8
    assert "显式" in priors["playfulness"].basis


def test_curiosity_from_question_ratio() -> None:
    asking = _bare(expression=Expression(examples=[Example("s", "真的吗？"), Example("s", "为什么呢？")]))
    stating = _bare(expression=Expression(examples=[Example("s", "好的。"), Example("s", "知道了。")]))
    assert signal_priors(asking)["curiosity"].value > signal_priors(stating)["curiosity"].value


def test_defiance_grows_with_boundaries() -> None:
    from bridge.model import ConflictProfile

    few = _bare(conflict=ConflictProfile(boundaries=[Asserted.source("a")]))
    many = _bare(conflict=ConflictProfile(
        boundaries=[Asserted.source("a"), Asserted.source("b"), Asserted.source("c")]))
    assert signal_priors(many)["defiance"].value > signal_priors(few)["defiance"].value


def test_every_value_has_basis() -> None:
    for pid in ("xiaolan", "linzhou", "aria"):
        for signal, prior in signal_priors(_load(pid)).items():
            if prior.value is not None:
                assert prior.basis, f"{pid}.{signal} 有值但无依据"


def test_values_stay_in_unit_range() -> None:
    for pid in ("xiaolan", "linzhou", "aria"):
        for signal, prior in signal_priors(_load(pid)).items():
            if prior.value is not None:
                assert 0.0 <= prior.value <= 1.0, (pid, signal, prior.value)


# ---------------------------------------------------------------- phase_threshold


def test_suppressed_persona_raises_threshold() -> None:
    """小岚「先收短、不说」→ 阈值高于 base。"""
    result = phase_threshold(_load("xiaolan"))
    assert result.value > PHASE_BASE
    assert "压抑" in result.basis


def test_outburst_persona_lowers_threshold() -> None:
    """林舟「当场指出」→ 阈值低于 base。"""
    result = phase_threshold(_load("linzhou"))
    assert result.value < PHASE_BASE
    assert "当场" in result.basis


def test_no_conflict_description_stays_missing() -> None:
    result = phase_threshold(_load("aria"))
    assert result.provenance.value == "missing"
    assert result.value is None


def test_threshold_is_clamped() -> None:
    from bridge.model import ConflictProfile

    extreme = _bare(conflict=ConflictProfile(
        style=Asserted.source("收短 不说 憋 忍着 沉默 结束一轮 不主动升级 " * 3)))
    result = phase_threshold(extreme)
    assert PHASE_MIN <= result.value <= PHASE_MAX
    if result.value == PHASE_MAX:
        assert "截断" in result.basis


def test_repeated_evidence_accumulates() -> None:
    from bridge.model import ConflictProfile

    one = _bare(conflict=ConflictProfile(style=Asserted.source("沉默，不说话")))
    two = _bare(conflict=ConflictProfile(
        style=Asserted.source("沉默，不说话"),
        repair=Asserted.source("看他有没有动作"),
    ))
    assert phase_threshold(two).value >= phase_threshold(one).value


# ---------------------------------------------------------------- drive sensitivity


def test_drive_sensitivity_uses_calibration() -> None:
    drives = drive_sensitivity(_load("xiaolan"))
    assert set(drives) == {"connection", "novelty", "expression", "safety", "play"}
    for drive, entry in drives.items():
        if entry.value is not None:
            assert 0.0 <= entry.value <= 1.0, drive


def test_drive_sensitivity_missing_without_mbti_or_text() -> None:
    drives = drive_sensitivity(_bare())
    assert all(v.provenance.value == "missing" for v in drives.values())


# ---------------------------------------------------------------- critic section


def test_persona_section_contains_triggers() -> None:
    section = critic_persona_section(_load("xiaolan"))
    assert "小岚" in section
    assert "退开" in section
    assert "边界" in section


def test_persona_section_english() -> None:
    section = critic_persona_section(_load("aria"))
    assert "Persona: Aria" in section
    assert "Insufficient source material" in section


def test_persona_section_marks_insufficient() -> None:
    section = critic_persona_section(_bare())
    assert "材料不足" in section
    assert "不要为该角色做特化推断" in section


def test_analyze_reports_usable_flag() -> None:
    assert analyze(_load("xiaolan"))["usable"] is True
    assert analyze(_load("aria"))["usable"] is False


def test_analyze_shapes() -> None:
    report = analyze(_load("linzhou"))
    assert set(report) >= {"persona_id", "usable", "signal_priors", "phase_threshold",
                           "drive_sensitivity", "critic_persona_section", "coverage"}
    assert report["coverage"]["signals_total"] == 8
    assert isinstance(report["coverage"]["missing"], list)


def test_no_text_persona_gives_maximal_missing() -> None:
    report = analyze(_bare())
    assert report["coverage"]["signals_with_evidence"] <= 2
    assert report["usable"] is False


# ---------------------------------------------------------------- CLI


def test_cli_triggers_output() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "cli.main", "persona", "triggers",
         str(PROFILES / "xiaolan.json"), "--json"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "phase_threshold" in proc.stdout
    payload = proc.stdout[proc.stdout.index("{"):]
    report = json.loads(payload)
    assert report["persona_id"] == "xiaolan"


def test_cli_triggers_missing_file() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "cli.main", "persona", "triggers", "/nope/x.json"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 2


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} trigger tests passed")
