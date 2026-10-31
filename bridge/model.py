# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PersonProfile —— Distilly 产物在我方内部的统一表示（看板 #31）。

设计要点
--------

1. **三态表达**（`Asserted`）：每个字段必须能区分「有材料依据」「推断」「材料不足」。
   #29 发现模型会写出 `(Insufficient source material; inferred: ...)` —— 既承认缺材料又补推断。
   本模型把 `inferred` 与 `missing` 做成互斥的显式状态，并要求 `inferred` 必须给出 `basis`。

2. **六层对齐**（#28）：Layer 1 → `relation`、Layer 2 → `expression`、Layer 3 → `logic`、
   Layer 4 → `conflict`、Layer 5 → `memory_signature`、Layer 0 → `boundaries` + `hard_rules`。
   字段名不沿用 `Layer N`，避免结构标记泄漏到 prompt（#30 实测会泄漏）。

3. **引擎提示与标定分离**：`engine.drive_baseline` / `engine.engine_params` 的值来自人工标定
   （#33 / #34），因此独立成块并允许整体 `missing`，不与文本人格混在一起。

4. **禁字段**：与 `docs/refine-api.md` 一致，任何层级出现 `raw_text` / `messages` / `chat_log` /
   `transcript` / `conversation` 一律校验失败 —— 蒸馏产物不应携带原文。

本模块只做数据模型与校验，不做映射（映射见 #32–#38）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Generic, TypeVar

SCHEMA_VERSION = "2.0"

T = TypeVar("T")

# 来自 OpenHer engine/genome/genome_engine.py
DRIVES = ("connection", "novelty", "expression", "safety", "play")

# 来自 OpenHer engine/genome/drive_metabolism.py + persona/personas/*/SOUL.md
ENGINE_PARAM_KEYS = (
    "baseline_lr", "elasticity", "hebbian_lr", "phase_threshold",
    "connection_hunger_k", "novelty_hunger_k", "frustration_decay",
    "hawking_gamma", "crystal_threshold", "temp_coeff", "temp_floor",
)

FORBIDDEN_KEYS = frozenset({
    "raw_text", "messages", "chat_log", "chat_history",
    "transcript", "conversation",
})

MAX_CORRECTIONS = 50
MAX_HABITS = 20
MAX_MEMORIES = 200
MAX_TEXT = 200


class Provenance(str, Enum):
    """字段的来源状态。"""

    SOURCE = "source"      # 直接来自材料，可引用
    INFERRED = "inferred"  # 推断：必须有 basis
    MISSING = "missing"    # 材料不足：必须有 reason


@dataclass
class Asserted(Generic[T]):
    """带来源标注的值。"""

    value: T | None = None
    provenance: Provenance = Provenance.MISSING
    basis: str | None = None
    reason: str | None = None

    @classmethod
    def source(cls, value: T, basis: str | None = None) -> Asserted[T]:
        return cls(value=value, provenance=Provenance.SOURCE, basis=basis)

    @classmethod
    def inferred(cls, value: T, basis: str) -> Asserted[T]:
        return cls(value=value, provenance=Provenance.INFERRED, basis=basis)

    @classmethod
    def missing(cls, reason: str) -> Asserted[T]:
        return cls(value=None, provenance=Provenance.MISSING, reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "provenance": self.provenance.value,
            "basis": self.basis,
            "reason": self.reason,
        }


@dataclass
class Identity:
    name: Asserted[str]
    lang: str = "zh"
    name_zh: Asserted[str] | None = None
    age: Asserted[int] | None = None
    gender: Asserted[str] | None = None
    mbti: Asserted[str] | None = None


@dataclass
class RelationContext:
    """Layer 1。"""

    label: Asserted[str]
    status: Asserted[str] | None = None
    defining_facts: list[Asserted[str]] = field(default_factory=list)


@dataclass
class Example:
    scene: str
    text: str


@dataclass
class Expression:
    """Layer 2。口头禅无频次来源（#28 §3.2），故不设 weight 字段。"""

    rhythm: Asserted[str] = field(default_factory=lambda: Asserted.missing("材料未提供节奏描述"))
    habits: list[Asserted[str]] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)


@dataclass
class LogicProfile:
    """Layer 3。"""

    opens_up_when: Asserted[str] | None = None
    pulls_away_when: Asserted[str] | None = None
    defends_by: Asserted[str] | None = None
    shows_care_by: Asserted[str] | None = None
    priorities: list[Asserted[str]] = field(default_factory=list)


@dataclass
class ConflictProfile:
    """Layer 4。"""

    style: Asserted[str] | None = None
    silence: Asserted[str] | None = None
    repair: Asserted[str] | None = None
    boundaries: list[Asserted[str]] = field(default_factory=list)


@dataclass
class Correction:
    """纠错层条目。字段名来自 Distilly `prompts/shared/correction_handler.md`。"""

    scene: str
    wrong: str
    correct: str


@dataclass
class EngineHints:
    """引擎数值提示。整块缺失是合法状态（#28 待确认 1/2）。"""

    drive_baseline: dict[str, Asserted[float]] = field(default_factory=dict)
    engine_params: dict[str, Asserted[float]] = field(default_factory=dict)
    calibration: Asserted[str] | None = None


@dataclass
class PersonProfile:
    persona_id: str
    identity: Identity
    relation: RelationContext | None = None
    expression: Expression = field(default_factory=Expression)
    logic: LogicProfile | None = None
    conflict: ConflictProfile | None = None
    memory_signature: list[Asserted[str]] = field(default_factory=list)
    hard_rules: list[Asserted[str]] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    engine: EngineHints = field(default_factory=EngineHints)
    meta: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


def _serialize(value: Any) -> Any:
    if isinstance(value, Asserted):
        return {"kind": "asserted", **value.to_dict()}
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: _serialize(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def _walk_asserted(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, Asserted):
        if value.provenance is Provenance.INFERRED:
            if not (value.basis or "").strip():
                errors.append(f"{path}: inferred 必须给出 basis")
            elif "insufficient" in (value.basis or "").lower():
                # #29 F5：既标材料不足又补推断，读者会误以为推断有依据
                errors.append(f"{path}: basis 不得同时声称 insufficient 与 inferred")
        elif value.provenance is Provenance.MISSING:
            if not (value.reason or "").strip():
                errors.append(f"{path}: missing 必须给出 reason")
            if value.value is not None:
                errors.append(f"{path}: missing 不应携带 value")
        elif value.provenance is Provenance.SOURCE:
            if value.value is None:
                errors.append(f"{path}: source 状态必须有 value")
        return
    if is_dataclass(value):
        for f in fields(value):
            _walk_asserted(getattr(value, f.name), f"{path}.{f.name}", errors)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_KEYS:
                errors.append(f"{path}.{key}: 禁止字段（不得携带原文）")
            _walk_asserted(item, f"{path}.{key}", errors)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk_asserted(item, f"{path}[{index}]", errors)


def validate_profile(profile: PersonProfile) -> list[str]:
    """返回错误列表；空列表表示通过。

    校验规则对应 #28 / #29 / #30 的教训：
    - 三态完整性（inferred 有 basis、missing 有 reason）
    - 不允许 `insufficient + inferred` 并存（#29 F5）
    - 引擎键必须在 OpenHer 白名单内（防止写出 loader 不读的悬空字段）
    - 上限与禁字段（corrections ≤ 50、habits ≤ 20、memories ≤ 200）
    """
    errors: list[str] = []

    if profile.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version: 期望 {SCHEMA_VERSION}，得到 {profile.schema_version}")

    pid = (profile.persona_id or "").strip()
    if not pid:
        errors.append("persona_id: 不能为空")
    elif not pid.replace("-", "").replace("_", "").isalnum() or len(pid) > 64:
        errors.append("persona_id: 只允许字母/数字/连字符/下划线，长度 ≤ 64")
    elif pid != pid.lower():
        errors.append("persona_id: 必须小写")

    if profile.identity.name.provenance is Provenance.MISSING:
        errors.append("identity.name: 必须有来源（source 或 inferred）")

    if len(profile.corrections) > MAX_CORRECTIONS:
        errors.append(f"corrections: {len(profile.corrections)} 条，上限 {MAX_CORRECTIONS}")
    for index, correction in enumerate(profile.corrections):
        for key in ("scene", "wrong", "correct"):
            if not getattr(correction, key).strip():
                errors.append(f"corrections[{index}].{key}: 不能为空")

    if len(profile.expression.habits) > MAX_HABITS:
        errors.append(f"expression.habits: {len(profile.expression.habits)} 条，上限 {MAX_HABITS}")
    if len(profile.memory_signature) > MAX_MEMORIES:
        errors.append(f"memory_signature: {len(profile.memory_signature)} 条，上限 {MAX_MEMORIES}")

    for key in profile.engine.drive_baseline:
        if key not in DRIVES:
            errors.append(f"engine.drive_baseline.{key}: 不是合法的 drive（{', '.join(DRIVES)}）")
    for key in profile.engine.engine_params:
        if key not in ENGINE_PARAM_KEYS:
            errors.append(f"engine.engine_params.{key}: 不在允许的 11 个 engine_params 内")

    _walk_asserted(profile.identity, "identity", errors)
    for attr in ("relation", "expression", "logic", "conflict", "engine"):
        value = getattr(profile, attr)
        if value is not None:
            _walk_asserted(value, attr, errors)
    _walk_asserted(profile.memory_signature, "memory_signature", errors)
    _walk_asserted(profile.hard_rules, "hard_rules", errors)
    _walk_asserted(profile.meta, "meta", errors)

    for path, items in (("expression.habits", profile.expression.habits),
                        ("memory_signature", profile.memory_signature),
                        ("hard_rules", profile.hard_rules)):
        for index, item in enumerate(items):
            if isinstance(item, Asserted) and isinstance(item.value, str) and len(item.value) > MAX_TEXT:
                errors.append(f"{path}[{index}]: 超过 {MAX_TEXT} 字符")

    return errors


# ── 反序列化（#83 起 API 需要把请求体还原为模型） ──────────────────────────

def _asserted_from_dict(data: Any) -> Any:
    """把 {"kind": "asserted", value/provenance/basis/reason} 还原为 Asserted。"""
    if not isinstance(data, dict):
        return data
    if data.get("kind") == "asserted" or ({"provenance"} <= set(data) and "kind" not in data):
        provenance = data.get("provenance")
        try:
            prov = Provenance(provenance)
        except ValueError as exc:
            raise ValueError(f"非法 provenance: {provenance!r}") from exc
        return Asserted(
            value=data.get("value"),
            provenance=prov,
            basis=data.get("basis"),
            reason=data.get("reason"),
        )
    return data


def _build_dataclass(cls, data: dict) -> Any:
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        if value is None:
            kwargs[f.name] = None
            continue
        if isinstance(value, list):
            # 对称性修复：to_dict 把 list[Asserted] 写成 list[dict]，反序列化必须逐项还原。
            kwargs[f.name] = [_asserted_from_dict(item) for item in value]
        else:
            kwargs[f.name] = _asserted_from_dict(value)
    return cls(**kwargs)


def profile_from_dict(data: dict) -> PersonProfile:
    """把 API 请求体还原为 PersonProfile（与 to_dict 对称）。

    只做结构还原，不做校验；调用方随后应执行 `validate_profile()`。
    """
    if not isinstance(data, dict):
        raise ValueError("profile 必须是对象")

    identity_data = data.get("identity")
    if not isinstance(identity_data, dict):
        raise ValueError("identity 必须是对象")
    identity = _build_dataclass(Identity, identity_data)

    relation = _build_dataclass(RelationContext, data["relation"]) if data.get("relation") else None
    logic = _build_dataclass(LogicProfile, data["logic"]) if data.get("logic") else None
    conflict = _build_dataclass(ConflictProfile, data["conflict"]) if data.get("conflict") else None

    expression_data = data.get("expression") or {}
    expression = Expression(
        rhythm=_asserted_from_dict(expression_data.get("rhythm")) or Asserted.missing("未提供节奏"),
        habits=[_asserted_from_dict(item) for item in expression_data.get("habits", [])],
        examples=[Example(**item) for item in expression_data.get("examples", [])],
    )

    engine_data = data.get("engine") or {}
    engine = EngineHints(
        drive_baseline={k: _asserted_from_dict(v) for k, v in (engine_data.get("drive_baseline") or {}).items()},
        engine_params={k: _asserted_from_dict(v) for k, v in (engine_data.get("engine_params") or {}).items()},
        calibration=_asserted_from_dict(engine_data.get("calibration")) or None,
    )

    corrections = [Correction(**item) for item in data.get("corrections", [])]

    return PersonProfile(
        persona_id=data.get("persona_id", ""),
        identity=identity,
        relation=relation,
        expression=expression,
        logic=logic,
        conflict=conflict,
        memory_signature=[_asserted_from_dict(i) for i in data.get("memory_signature", [])],
        hard_rules=[_asserted_from_dict(i) for i in data.get("hard_rules", [])],
        corrections=corrections,
        engine=engine,
        meta=data.get("meta") or {},
        schema_version=data.get("schema_version", SCHEMA_VERSION),
    )


__all__ = [
    "SCHEMA_VERSION", "DRIVES", "ENGINE_PARAM_KEYS", "FORBIDDEN_KEYS",
    "Provenance", "Asserted", "Identity", "RelationContext", "Example",
    "Expression", "LogicProfile", "ConflictProfile", "Correction",
    "EngineHints", "PersonProfile", "validate_profile", "profile_from_dict",
]
