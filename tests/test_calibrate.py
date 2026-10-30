# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#33 标定 v1：先验、公式、clamp 与 10 样本回代精度。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.calibrate import (  # noqa: E402
    FIT_QUALITY,
    PARAM_BOUNDS,
    PARAM_COEFFS,
    calibrate,
    detect_text_hints,
    drives_from_mbti,
    engine_params_from_drives,
)
from bridge.model import (  # noqa: E402
    Asserted,
    ConflictProfile,
    DRIVES,
    ENGINE_PARAM_KEYS,
    Expression,
    Identity,
    LogicProfile,
    PersonProfile,
    Provenance,
    validate_profile,
)

SAMPLES = ROOT / "vendor" / "OpenHer" / "persona" / "personas"


def xiaolan() -> PersonProfile:
    return PersonProfile(
        persona_id="xiaolan",
        identity=Identity(
            name=Asserted.source("小岚"),
            mbti=Asserted.missing("六层不含 MBTI"),
        ),
        expression=Expression(rhythm=Asserted.source("短句为主，停顿多；不连续输出大段解释")),
        logic=LogicProfile(
            defends_by=Asserted.source("把话收短、用反问让对方先意识到问题"),
            shows_care_by=Asserted.source("提醒具体风险、安排具体见面"),
        ),
        conflict=ConflictProfile(
            boundaries=[Asserted.source("不接受反复承诺"), Asserted.source("不接受把忙当理由")],
        ),
    )


def load_samples() -> list[dict]:
    if not SAMPLES.is_dir():
        return []
    out = []
    for d in sorted(p for p in SAMPLES.iterdir() if p.is_dir()):
        soul = d / "SOUL.md"
        if not soul.is_file():
            continue
        fm = soul.read_text(encoding="utf-8").split("---")[1]

        def num(key: str) -> float | None:
            m = re.search(rf"^\s*{key}:\s*([0-9.]+)", fm, re.M)
            return float(m.group(1)) if m else None

        mbti_match = re.search(r"^\s*mbti:\s*(\w+)", fm, re.M)
        out.append(
            {
                "id": d.name,
                "mbti": mbti_match.group(1) if mbti_match else None,
                "drives": {k: num(k) for k in DRIVES},
                "params": {k: num(k) for k in ENGINE_PARAM_KEYS},
            }
        )
    return out


def test_mbti_priors() -> None:
    drives = drives_from_mbti("ENFP")
    assert drives["connection"].value == 0.60
    assert drives["novelty"].value == 0.67
    assert drives["expression"].value == 0.66
    assert drives["safety"].value == 0.39
    assert drives["play"].value == 0.63
    for asserted in drives.values():
        assert asserted.provenance is Provenance.INFERRED
        assert "ENFP" in (asserted.basis or "")


def test_no_mbti_no_hints_gives_missing() -> None:
    profile = PersonProfile(persona_id="a", identity=Identity(name=Asserted.source("甲")))
    hints = calibrate(profile)
    assert all(v.provenance is Provenance.MISSING for v in hints.drive_baseline.values())
    assert all(v.provenance is Provenance.MISSING for v in hints.engine_params.values())


def test_text_hints_are_detected() -> None:
    hints = detect_text_hints(xiaolan())
    assert "short_sentences" in hints
    assert "guarded" in hints
    assert "care_actions" in hints
    assert "hard_shell" in hints


def test_calibrate_without_mbti_uses_text_hints() -> None:
    hints = calibrate(xiaolan())
    assert all(v.provenance is Provenance.INFERRED for v in hints.drive_baseline.values())
    assert hints.drive_baseline["safety"].value > 0.5
    assert hints.calibration is not None
    assert "文本线索" in (hints.calibration.basis or "")


def test_basis_never_claims_insufficient() -> None:
    hints = calibrate(xiaolan())
    for asserted in list(hints.drive_baseline.values()) + list(hints.engine_params.values()):
        if asserted.provenance is Provenance.INFERRED:
            assert "insufficient" not in (asserted.basis or "").lower()


def test_output_passes_validation() -> None:
    profile = xiaolan()
    profile.engine = calibrate(profile)
    errors = validate_profile(profile)
    assert not errors, errors


def test_clamp_keeps_bounds() -> None:
    drives = {d: Asserted.inferred(1.0, basis="测试边界") for d in DRIVES}
    params = engine_params_from_drives(drives)
    for key, asserted in params.items():
        low, high = PARAM_BOUNDS[key]
        assert low <= float(asserted.value) <= high, (key, asserted.value)


def test_all_params_covered() -> None:
    assert set(PARAM_COEFFS) == set(ENGINE_PARAM_KEYS)
    assert set(FIT_QUALITY) == set(ENGINE_PARAM_KEYS)


def test_backfit_accuracy_on_upstream_samples() -> None:
    """用真实 drive 值预测参数，检查 v1 公式在样本内的精度。"""
    samples = load_samples()
    if not samples:
        print("SKIP: vendor/OpenHer 未就绪")
        return
    worst = []
    for sample in samples:
        if not all(sample["drives"].values()):
            continue
        drives = {d: Asserted.source(sample["drives"][d]) for d in DRIVES}
        predicted = engine_params_from_drives(drives)
        for key, actual in sample["params"].items():
            if actual is None:
                continue
            rel = abs(float(predicted[key].value) - actual) / actual
            worst.append((rel, sample["id"], key, actual, float(predicted[key].value)))
    worst.sort(reverse=True)
    mean_rel = sum(w[0] for w in worst) / len(worst)
    print(f"回代：{len(worst)} 个参数值，平均相对误差 {mean_rel:.1%}；最差 5 项：")
    for rel, sid, key, actual, pred in worst[:5]:
        print(f"  {sid:<8}{key:<22}实际 {actual:.5f} 预测 {pred:.5f} 误差 {rel:.1%}")
    assert mean_rel < 0.25, f"平均相对误差 {mean_rel:.1%} 超出 v1 目标"


def test_mbti_prior_matches_sample_means() -> None:
    """先验是「E 组均值」，应与全部 E 样本的均值一致（不是 ENFP 子集）。"""
    samples = {s["id"]: s for s in load_samples()}
    if not samples:
        print("SKIP: vendor/OpenHer 未就绪")
        return
    e_group = [s for s in samples.values()
               if s["mbti"] and s["mbti"].startswith("E")
               and s["drives"]["connection"] is not None]
    assert e_group, "样本里应有 E 组"
    mean_connection = sum(s["drives"]["connection"] for s in e_group) / len(e_group)
    prior = drives_from_mbti("ENFP")["connection"].value
    assert abs(prior - mean_connection) < 0.02, (prior, mean_connection)

    i_group = [s for s in samples.values()
               if s["mbti"] and s["mbti"].startswith("I")
               and s["drives"]["connection"] is not None]
    mean_i = sum(s["drives"]["connection"] for s in i_group) / len(i_group)
    assert abs(drives_from_mbti("INFP")["connection"].value - mean_i) < 0.02


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} calibration tests passed")
