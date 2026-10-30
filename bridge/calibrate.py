# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""特征 → drive_baseline / engine_params 的启发式标定 v1（看板 #33 / #34）。

**依据不是凭感觉，而是 10 个上游人格的真实参数**（`vendor/OpenHer/persona/personas/*/SOUL.md`，
commit ef5b214）。做法：

1. 从这 10 个样本统计 MBTI 二分 → 五维 drive 的组均值，作为先验；
2. 对 11 个 engine_params 分别做「两维 drive」的线性拟合，取 R² 最高的组合；
3. 拟合系数硬编码在 `PARAM_COEFFS`，残差（MAE）作为 v1 精度写在 `FIT_QUALITY`；
4. 输出一律 `Asserted.inferred(..., basis=...)`，并 clamp 到样本观察范围外扩 10%。

样本只有 10 个、自由度 7，系数是**经验拟合**而非理论推导，外推需谨慎 —— 这一点必须随结果一起说明。

注：issue #33 标题写「13 个 engine_params」，OpenHer 实际只有 11 个（见 `bridge.model.ENGINE_PARAM_KEYS`）。
"""

from __future__ import annotations

from typing import Any

from bridge.model import (
    DRIVES,
    ENGINE_PARAM_KEYS,
    Asserted,
    EngineHints,
    PersonProfile,
    Provenance,
)

BASIS_DRIVE = "MBTI 二分组的 10 个上游人格样本均值（n=10）"
BASIS_PARAM = "v1 双变量线性拟合（10 个上游人格样本，见 FIT_QUALITY）"

# MBTI 二分 → drive 的样本组均值（E/I, N/S, F/T, J/P）
MBTI_PRIORS = {
    "E": {"connection": 0.60}, "I": {"connection": 0.41},
    "N": {"novelty": 0.67}, "S": {"novelty": 0.45},
    "F": {"expression": 0.66}, "T": {"expression": 0.50},
    "J": {"safety": 0.73, "play": 0.25}, "P": {"safety": 0.39, "play": 0.63},
}
DEFAULT_DRIVES = {"connection": 0.5, "novelty": 0.5, "expression": 0.5, "safety": 0.5, "play": 0.5}

# 两维线性拟合：(b0, b_f1, f1, b_f2, f2)
PARAM_COEFFS: dict[str, tuple[float, float, str, float, str]] = {
    "baseline_lr":          (0.0133, 0.0085, "connection", -0.0122, "safety"),
    "elasticity":           (-0.0001, 0.0207, "novelty", 0.0770, "safety"),
    "hebbian_lr":           (0.0161, 0.0158, "connection", -0.0081, "safety"),
    "phase_threshold":      (2.3361, -1.8013, "connection", 1.9869, "safety"),
    "connection_hunger_k":  (0.0646, 0.1828, "connection", -0.0799, "novelty"),
    "novelty_hunger_k":     (-0.0438, 0.2274, "novelty", -0.0311, "safety"),
    "frustration_decay":    (0.2076, -0.0912, "novelty", -0.1124, "safety"),
    "hawking_gamma":        (0.0027, -0.0009, "novelty", -0.0022, "safety"),
    "crystal_threshold":    (0.4358, -0.1315, "connection", 0.1567, "safety"),
    "temp_coeff":           (0.0881, 0.1250, "connection", -0.0933, "safety"),
    "temp_floor":           (0.0493, 0.0118, "expression", -0.0562, "safety"),
}

# 拟合质量（MAE 相对均值 / R²），来自 10 样本回代
FIT_QUALITY = {
    "baseline_lr": (0.095, 0.904), "elasticity": (0.097, 0.883), "hebbian_lr": (0.105, 0.773),
    "phase_threshold": (0.051, 0.951), "connection_hunger_k": (0.071, 0.921),
    "novelty_hunger_k": (0.148, 0.823), "frustration_decay": (0.125, 0.709),
    "hawking_gamma": (0.165, 0.815), "crystal_threshold": (0.070, 0.686),
    "temp_coeff": (0.052, 0.964), "temp_floor": (0.072, 0.971),
}

# 样本观察范围（外扩 10% 作为 clamp 边界）
PARAM_BOUNDS = {
    "baseline_lr": (0.0054, 0.0220), "elasticity": (0.0180, 0.0880),
    "hebbian_lr": (0.0108, 0.0330), "phase_threshold": (1.0800, 3.8500),
    "connection_hunger_k": (0.0540, 0.1980), "novelty_hunger_k": (0.0360, 0.1650),
    "frustration_decay": (0.0450, 0.1650), "hawking_gamma": (0.00045, 0.00220),
    "crystal_threshold": (0.3150, 0.6050), "temp_coeff": (0.0450, 0.1980),
    "temp_floor": (0.0090, 0.0550),
}

# 无 MBTI 时的文本线索（经验规则，需人工复核）
TEXT_HINTS = {
    "short_sentences": ("expression", -0.05, "文本以短句为主，表达欲偏中等"),
    "guarded": ("connection", -0.05, "不直接示弱/防御性表达，联结需求略低"),
    "care_actions": ("play", -0.05, "关心通过具体行动表达，玩闹度偏低"),
    "hard_shell": ("safety", +0.15, "嘴硬/边界明确，安全感需求偏高"),
    "curiosity": ("novelty", +0.10, "主动追问，新鲜感需求偏高"),
}


def _clamp(value: float, key: str) -> float:
    low, high = PARAM_BOUNDS[key]
    return max(low, min(high, value))


def drives_from_mbti(mbti: str | None) -> dict[str, Asserted[float]]:
    """MBTI → 五维 drive 的先验（样本组均值）。"""
    if not mbti or not mbti.strip():
        return {d: Asserted.missing("没有 MBTI，且未从文本推断（#33 v1 仅做 MBTI 先验）") for d in DRIVES}
    letters = mbti.strip().upper()
    base = dict(DEFAULT_DRIVES)
    for letter in letters:
        for drive, value in MBTI_PRIORS.get(letter, {}).items():
            base[drive] = value
    return {d: Asserted.inferred(round(base[d], 3), basis=f"{BASIS_DRIVE}；{letters}") for d in DRIVES}


def adjust_drives_by_text(drives: dict[str, Asserted[float]], hints: list[str]) -> dict[str, Asserted[float]]:
    """用文本线索微调 drive（最多 ±0.15），保留 basis 说明。"""
    out = dict(drives)
    for hint in hints:
        rule = TEXT_HINTS.get(hint)
        if not rule:
            continue
        drive, delta, why = rule
        current = out.get(drive)
        if current is None or current.provenance is Provenance.MISSING:
            continue
        base_value = float(current.value if current.value is not None else 0.5)
        new_value = max(0.0, min(1.0, base_value + delta))
        out[drive] = Asserted.inferred(
            round(new_value, 3),
            basis=f"{current.basis}；文本线索：{why}（{delta:+.2f}）",
        )
    return out


def engine_params_from_drives(drives: dict[str, Asserted[float]]) -> dict[str, Asserted[float]]:
    """五维 drive → 11 个 engine_params（v1 线性拟合 + clamp）。"""
    values: dict[str, float | None] = {}
    for drive in DRIVES:
        asserted = drives.get(drive)
        if asserted is None or asserted.provenance is Provenance.MISSING:
            values[drive] = None
        else:
            values[drive] = float(asserted.value)

    out: dict[str, Asserted[float]] = {}
    for key in ENGINE_PARAM_KEYS:
        b0, b1, f1, b2, f2 = PARAM_COEFFS[key]
        v1, v2 = values.get(f1), values.get(f2)
        if v1 is None or v2 is None:
            out[key] = Asserted.missing(
                f"缺少 {f1}/{f2} 的 drive 值，无法拟合（v1 公式依赖这两维）"
            )
            continue
        raw = b0 + b1 * v1 + b2 * v2
        mae, r2 = FIT_QUALITY[key]
        out[key] = Asserted.inferred(
            round(_clamp(raw, key), 5),
            basis=f"{BASIS_PARAM}；{key} 依赖 {f1}+{f2}，MAE {mae:.1%}，R² {r2:.2f}",
        )
    return out


def detect_text_hints(profile: PersonProfile) -> list[str]:
    """从人格文本里找可用线索（保守规则，只取明确特征）。"""
    hints: list[str] = []
    rhythm = profile.expression.rhythm
    if rhythm.provenance is not Provenance.MISSING and isinstance(rhythm.value, str):
        if any(word in rhythm.value for word in ("短句", "停顿", "不连续")):
            hints.append("short_sentences")
    logic = profile.logic
    if logic and logic.defends_by and logic.defends_by.provenance is not Provenance.MISSING:
        text = str(logic.defends_by.value)
        if any(word in text for word in ("反问", "收短", "不直接", "防御")):
            hints.append("guarded")
    if logic and logic.shows_care_by and logic.shows_care_by.provenance is not Provenance.MISSING:
        text = str(logic.shows_care_by.value)
        if any(word in text for word in ("提醒", "安排", "行动", "具体")):
            hints.append("care_actions")
    if profile.conflict and profile.conflict.boundaries:
        active = [b for b in profile.conflict.boundaries if b.provenance is not Provenance.MISSING]
        if len(active) >= 2:
            hints.append("hard_shell")
    return hints


def calibrate(profile: PersonProfile) -> EngineHints:
    """PersonProfile → EngineHints（drive_baseline + engine_params）。"""
    mbti = None
    if profile.identity.mbti and profile.identity.mbti.provenance is not Provenance.MISSING:
        mbti = str(profile.identity.mbti.value or "")

    drives = drives_from_mbti(mbti)
    if all(d.provenance is Provenance.MISSING for d in drives.values()):
        hints = detect_text_hints(profile)
        if hints:
            base = {d: Asserted.inferred(DEFAULT_DRIVES[d], basis="无 MBTI，取五维中性先验 0.5")
                    for d in DRIVES}
            drives = adjust_drives_by_text(base, hints)
        else:
            drives = {d: Asserted.missing("没有 MBTI 且未识别到文本线索，不猜 drive 值") for d in DRIVES}

    params = engine_params_from_drives(drives)
    note = (
        f"v1 启发式：{'MBTI 先验' if mbti else '文本线索'} → drive → engine_params；"
        "拟合样本 n=10（OpenHer ef5b214），外推需复核"
    )
    return EngineHints(
        drive_baseline=drives,
        engine_params=params,
        calibration=Asserted.inferred("标定 v1（脚本生成）", basis=note),
    )


def apply_calibration(profile: PersonProfile) -> PersonProfile:
    """把标定结果写回 profile（原地修改 engine 块）。"""
    profile.engine = calibrate(profile)
    return profile


__all__ = [
    "MBTI_PRIORS", "PARAM_COEFFS", "FIT_QUALITY", "PARAM_BOUNDS", "TEXT_HINTS",
    "drives_from_mbti", "adjust_drives_by_text", "engine_params_from_drives",
    "detect_text_hints", "calibrate", "apply_calibration",
]
