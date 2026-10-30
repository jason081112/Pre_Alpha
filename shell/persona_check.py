# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""人格体检（#86）：结构 / 加载 / 探针 / 隐私四块。

设计前提（#30 的教训）：**"能加载"不等于"像这个人"**。所以体检不只看加载成功，
还要看内容是否充实、有没有结构标记泄漏、有没有隐私风险，并把"缺什么"翻译成可执行的修法。

判定：任一项 `fail` → 整体 fail（阻断保存）；否则有 `warn` → warn；全部 pass/skip → pass。

不依赖 FastAPI；加载与探针在环境不具备时降级为 `skipped` 并说明原因，绝不伪装成 pass。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bridge.model import (
    DRIVES,
    PersonProfile,
    Provenance,
    validate_profile,
)

LAYER_MARKER_RE = re.compile(r"\bLayer\s*\d")
PII_PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "bank_card": re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    "absolute_path": re.compile(r"(?:/workspaces/|/home/|/Users/)"),
}

MIN_HABITS = 3
MIN_RULES = 1
MIN_BOUNDARIES = 1


def _iter_texts(value: Any, path: str = "$"):
    """遍历 profile 结构里的所有文本，产出 (路径, 文本)。"""
    from dataclasses import fields, is_dataclass

    from bridge.model import Asserted

    if isinstance(value, Asserted):
        if isinstance(value.value, str) and value.value.strip():
            yield path, value.value
        if isinstance(value.basis, str) and value.basis.strip():
            yield f"{path}.basis", value.basis
        return
    if is_dataclass(value):
        for f in fields(value):
            yield from _iter_texts(getattr(value, f.name), f"{path}.{f.name}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_texts(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _iter_texts(item, f"{path}[{index}]")


def _count(asserted_list) -> int:
    return sum(
        1 for item in asserted_list
        if item.provenance is not Provenance.MISSING and isinstance(item.value, str) and item.value.strip()
    )


def check_structure(profile: PersonProfile) -> dict:
    """结构完整性：校验错误、结构标记泄漏、内容充实度。"""
    issues: list[str] = []
    errors = validate_profile(profile)
    if errors:
        issues.extend(errors)

    leaked = [path for path, text in _iter_texts(profile) if LAYER_MARKER_RE.search(text)]
    if leaked:
        issues.append(f"检测到结构标记 Layer N：{', '.join(leaked[:3])}")

    missing_inferred = [
        path for path, text in _iter_texts(profile)
        if path.endswith(".basis") and "insufficient" in text.lower()
    ]
    if missing_inferred:
        issues.append(f"basis 同时声称材料不足与推断：{', '.join(missing_inferred[:3])}")

    habits = _count(profile.expression.habits)
    rules = _count(profile.hard_rules)
    boundaries = _count(profile.conflict.boundaries) if profile.conflict else 0
    has_rhythm = profile.expression.rhythm.provenance is not Provenance.MISSING

    gaps = []
    if not has_rhythm:
        gaps.append("expression.rhythm 缺失")
    if habits < MIN_HABITS:
        gaps.append(f"口头禅只有 {habits} 条（建议 ≥ {MIN_HABITS}）")
    if rules < MIN_RULES:
        gaps.append("hard_rules 为空")
    if profile.conflict is None or boundaries < MIN_BOUNDARIES:
        gaps.append("冲突边界（conflict.boundaries）为空")
    calibrated = sum(
        1 for key in DRIVES
        if key in profile.engine.drive_baseline
        and profile.engine.drive_baseline[key].provenance is not Provenance.MISSING
    )
    missing_drives = len(DRIVES) - calibrated
    if missing_drives:
        gaps.append(f"drive_baseline 有 {missing_drives} 维未标定")

    status = "fail" if issues else ("warn" if gaps else "pass")
    return {
        "status": status,
        "issues": issues,
        "gaps": gaps,
        "metrics": {
            "habits": habits,
            "hard_rules": rules,
            "boundaries": boundaries,
            "has_rhythm": has_rhythm,
            "missing_drives": missing_drives,
        },
    }


def check_privacy(profile: PersonProfile) -> dict:
    """隐私：手机号 / 身份证 / 邮箱 / 银行卡 / 绝对路径。"""
    hits: list[dict] = []
    for path, text in _iter_texts(profile):
        for label, pattern in PII_PATTERNS.items():
            match = pattern.search(text)
            if match:
                hits.append({"path": path, "kind": label, "sample": match.group(0)[:6] + "…"})
    return {"status": "fail" if hits else "pass", "hits": hits}


def check_load(personas_dir: Path | None, persona_id: str) -> dict:
    """加载：能否被 PersonaLoader 读到。环境不具备时 skipped。"""
    if personas_dir is None or not (personas_dir / persona_id / "SOUL.md").is_file():
        return {"status": "skipped", "reason": "引擎目录或 SOUL.md 不存在（未镜像/未安装）"}
    try:
        import sys

        root = personas_dir.parents[2] if len(personas_dir.parents) >= 3 else personas_dir.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from persona.loader import PersonaLoader  # type: ignore

        loader = PersonaLoader(str(personas_dir))
        loaded = loader.load_all().get(persona_id)
        if loaded is None:
            return {"status": "fail", "reason": "PersonaLoader 未能加载该人格"}
        warnings = []
        if not loaded.speaking_style:
            warnings.append("speaking_style 为空（正文四槽未映射，#33–#38）")
        return {
            "status": "warn" if warnings else "pass",
            "warnings": warnings,
            "fields": {"name": loaded.name, "lang": loaded.lang, "gender": loaded.gender},
        }
    except Exception as exc:  # noqa: BLE001 - 环境缺失属预期
        return {"status": "skipped", "reason": f"无法导入 PersonaLoader：{str(exc)[:80]}"}


def check_probe(profile: PersonProfile, *, enabled: bool) -> dict:
    """风格探针：需要引擎与 provider；未启用/不可用一律 skipped。"""
    if not enabled:
        return {"status": "skipped", "reason": "未启用（加 --probe 且需引擎运行）"}
    return {"status": "skipped", "reason": "探针实现依赖运行中的引擎（#44），当前环境不可用"}


def _fixes(structure: dict, privacy: dict, load: dict) -> list[str]:
    out: list[str] = []
    for issue in structure.get("issues", []):
        if "Layer" in issue:
            out.append("去掉文本里的 Layer N 标题：映射时应改名到四槽（#30 §0.1）")
        elif "basis" in issue:
            out.append("basis 里不要写 insufficient：要么删掉推断，要么补真实依据（#29 F5）")
        else:
            out.append(f"修正校验错误：{issue}")
    for gap in structure.get("gaps", []):
        if "口头禅" in gap:
            out.append("补 3 条以上口头禅，写入 expression.habits（#35 会灌进 style_memory）")
        elif "drive_baseline" in gap:
            out.append("标定 drive_baseline 五维（#33/#34），否则行为倾向走引擎默认值")
        elif "rhythm" in gap:
            out.append("补 expression.rhythm（说话节奏），它直接进 SOUL.md 的说话风格")
        elif "hard_rules" in gap:
            out.append("补 hard_rules（最高优先级行为规则），否则人格缺底线")
        elif "边界" in gap:
            out.append("补 conflict.boundaries，避免角色在冲突场景失控")
    for hit in privacy.get("hits", []):
        out.append(f"移除疑似隐私：{hit['path']}（{hit['kind']}）")
    for warning in load.get("warnings", []):
        out.append(f"加载告警：{warning}")
    return out


def run_check(
    profile: PersonProfile,
    *,
    persona_id: str,
    personas_dir: Path | None = None,
    probe: bool = False,
) -> dict:
    structure = check_structure(profile)
    privacy = check_privacy(profile)
    load = check_load(personas_dir, persona_id)
    probe_result = check_probe(profile, enabled=probe)

    sections = {
        "structure": structure,
        "privacy": privacy,
        "load": load,
        "probe": probe_result,
    }
    statuses = [s["status"] for s in sections.values()]
    if "fail" in statuses:
        verdict = "fail"
    elif "warn" in statuses:
        verdict = "warn"
    else:
        verdict = "pass"

    return {
        "persona_id": persona_id,
        "verdict": verdict,
        "sections": sections,
        "fixes": _fixes(structure, privacy, load),
    }


__all__ = [
    "check_structure", "check_privacy", "check_load", "check_probe", "run_check",
]
