# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#38 情绪触发 / 冲突 / 修复 → critic 输入特征 + phase_threshold 校准。

三样产物：

1. **`critic_persona_section()`** —— 注入 critic prompt 的 `persona_section`。
   这是 #38 最直接的落点：critic 每轮都会拿到它，用来判断"同一句话对这个角色的
   需求满足感 / 挫败增量"。内容只写触发点、冲突反应、修复方式、边界。
2. **`signal_priors()`** —— 8 个用户行为信号（`signal_buckets.yaml` 的口径）的先验期望值。
   用途：与 critic 当场判出的信号对比，偏差过大时视为角色失真信号（#86 探针的输入）。
3. **`phase_threshold()`** —— 在 #33 的 base 值上按"表达延迟 / 爆发方式 / 修复难度"再校准。
   语义见 `docs/p0-04`：累积挫败超过阈值触发相位转移，阈值越低越容易突变。

**所有数值都是启发式推断，逐条带 basis。没有依据的信号返回 `Asserted.missing`，
不填默认值** —— 宁可空着，也不给引擎一个假的性格。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from bridge.model import Asserted, PersonProfile

SIGNALS = (
    "directness", "vulnerability", "playfulness", "initiative",
    "depth", "warmth", "defiance", "curiosity",
)
SIGNAL_LABELS = {
    "directness": ("直接度", "directness"),
    "vulnerability": ("坦露度", "vulnerability"),
    "playfulness": ("玩闹度", "playfulness"),
    "initiative": ("主动度", "initiative"),
    "depth": ("深度", "depth"),
    "warmth": ("温暖度", "warmth"),
    "defiance": ("倔强度", "defiance"),
    "curiosity": ("好奇度", "curiosity"),
}

PHASE_BASE = 2.0  # genome_engine.py 的默认值
PHASE_MIN, PHASE_MAX = 0.8, 4.0


@dataclass(frozen=True)
class Rule:
    """一条可解释的启发式：命中关键词 → 调整某信号 / 相位阈值。"""

    key: str
    pattern: str
    delta: float
    reason: str


# ---------------------------------------------------------------- 规则表（唯一的"知识"来源）

# 注意：这里**不再**用自由文本关键词猜性格维度。词表匹配的信噪比太低（#29 的教训：
# 65 个幻觉候选无一为真），给引擎一个假的信号先验比不给更糟。
# signal_priors() 只采信「可数的结构事实」：句长、条数、问句比例、字段有无。
# 关键词仅用于 phase_threshold（那里有明确语义：压抑 / 当场外发 / 修复难），
# 且每条规则的触发条件在 basis 里逐条写明。

PHASE_RULES: tuple[Rule, ...] = (
    # 压抑型：忍得久 → 阈值升高（不轻易突变，但一旦到了更剧烈）
    Rule("phase", r"收短|不说|憋|忍着|沉默|用.{0,6}结束一轮|不主动升级|silence|swallow|hold it in", 0.35, "冲突时压抑不外发，挫败积累更久才相变"),
    # 当场外发型：阈值降低（挫败一出现就转化）
    Rule("phase", r"当场|立刻|马上|直接指出|不软化|immediately|on the spot|stop the conversation", -0.4, "冲突时当场外发，不需要长时间累积"),
    # 修复难：一次和好不够 → 阈值略升（旧账残留，需要更多轮才归零）
    Rule("phase", r"看.{0,6}动作|不轻信|还记着|别扯上次|看行动|not just words|still remembers", 0.2, "修复需要实际动作，单次和解不足以清零"),
    # 修复快：阈值略降（容易翻篇）
    Rule("phase", r"翻篇|不记仇|很快就好|forgive quickly|moves on", -0.2, "容易翻篇，挫败衰减更快"),
)


def _text_of(profile: PersonProfile) -> str:
    """把 profile 里所有可读文本拼成一段，供关键词匹配。"""
    chunks: list[str] = []

    def add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, Asserted):
            if value.value:
                chunks.append(str(value.value))
        elif isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                add(item)

    add(profile.expression.rhythm)
    add(profile.expression.habits)
    for ex in profile.expression.examples:
        add(ex.scene)
        add(ex.text)
    if profile.logic:
        for key in ("opens_up_when", "pulls_away_when", "defends_by", "shows_care_by"):
            add(getattr(profile.logic, key))
        add(profile.logic.priorities)
    if profile.conflict:
        for key in ("style", "silence", "repair"):
            add(getattr(profile.conflict, key))
        add(profile.conflict.boundaries)
    add(profile.hard_rules)
    add(profile.memory_signature)
    if profile.relation:
        add(profile.relation.label)
        add(profile.relation.status)
        add(profile.relation.defining_facts)
    return "\n".join(chunks).lower()


def _avg_chars(texts: list[str]) -> float | None:
    values = [len(t.strip()) for t in texts if t and t.strip()]
    return sum(values) / len(values) if values else None


def _has_question(texts: list[str]) -> float:
    if not texts:
        return 0.0
    hits = sum(1 for t in texts if any(q in t for q in ("？", "?", "吗")))
    return hits / len(texts)


def signal_priors(profile: PersonProfile) -> dict[str, Asserted[float]]:
    """8 个行为信号的先验期望（0..1）。

    只用可数的结构事实，不做词表猜测；测不出来的信号返回 `missing`，
    让引擎按中性处理（而不是吃一个编造出来的值）。
    """
    priors: dict[str, Asserted[float]] = {}

    # 直接度 ← 例句平均句长（短句 = 更直接）
    examples = [ex.text for ex in profile.expression.examples]
    avg = _avg_chars(examples)
    if avg is None:
        priors["directness"] = Asserted.missing("没有例句，无法测句长")
    elif avg <= 12:
        priors["directness"] = Asserted.inferred(0.72, basis=f"例句平均 {avg:.1f} 字，短句倾向明显")
    elif avg <= 20:
        priors["directness"] = Asserted.inferred(0.55, basis=f"例句平均 {avg:.1f} 字，中等")
    else:
        priors["directness"] = Asserted.inferred(0.35, basis=f"例句平均 {avg:.1f} 字，偏展开")

    # 坦露度 ← 是否写明「什么时候愿意开口」
    opens = getattr(profile.logic, "opens_up_when", None) if profile.logic else None
    if isinstance(opens, Asserted) and opens.value:
        priors["vulnerability"] = Asserted.inferred(0.6, basis="写明 opens_up_when（开口条件）")
    else:
        priors["vulnerability"] = Asserted.missing("未描述何时愿意开口")

    # 玩闹度 ← 仅当 profile 显式声明（无法从结构测）
    declared = (profile.meta or {}).get("playfulness")
    if isinstance(declared, (int, float)):
        priors["playfulness"] = Asserted.inferred(
            round(min(1.0, max(0.0, float(declared))), 3), basis="meta.playfulness 显式给定")
    else:
        priors["playfulness"] = Asserted.missing("未显式声明 playfulness，结构上测不出")

    # 主动度 ← 是否有 defends_by / shows_care_by（有具体主动行为）
    acts = 0
    if profile.logic:
        acts = sum(1 for key in ("defends_by", "shows_care_by")
                   if isinstance(getattr(profile.logic, key, None), Asserted)
                   and getattr(profile.logic, key).value)
    if not profile.logic:
        priors["initiative"] = Asserted.missing("无 logic 段")
    elif acts:
        priors["initiative"] = Asserted.inferred(0.65, basis=f"logic 中 {acts} 个主动行为字段非空")
    else:
        priors["initiative"] = Asserted.missing("logic 中未给出主动行为")

    # 深度 ← priorities 条数（在意的事越多越愿意往下谈）
    prios = [p for p in (profile.logic.priorities if profile.logic else [])
             if isinstance(p, Asserted) and p.value]
    if prios:
        priors["depth"] = Asserted.inferred(
            round(min(0.8, 0.45 + 0.1 * len(prios)), 3), basis=f"priorities {len(prios)} 条")
    else:
        priors["depth"] = Asserted.missing("没有 priorities")

    # 温暖度 ← shows_care_by 是否存在
    care = getattr(profile.logic, "shows_care_by", None) if profile.logic else None
    if isinstance(care, Asserted) and care.value:
        priors["warmth"] = Asserted.inferred(0.72, basis="写明 shows_care_by（表达在意的方式）")
    else:
        priors["warmth"] = Asserted.missing("未描述如何表达在意")

    # 倔强度 ← 边界条数
    bounds = [b for b in (profile.conflict.boundaries if profile.conflict else [])
              if isinstance(b, Asserted) and b.value]
    hard = [h for h in profile.hard_rules if isinstance(h, Asserted) and h.value]
    if bounds or hard:
        priors["defiance"] = Asserted.inferred(
            round(min(0.8, 0.45 + 0.1 * (len(bounds) + len(hard))), 3),
            basis=f"boundaries {len(bounds)} 条 + hard_rules {len(hard)} 条")
    else:
        priors["defiance"] = Asserted.missing("没有边界或硬规则")

    # 好奇度 ← 例句中问句比例
    if not examples:
        priors["curiosity"] = Asserted.missing("没有例句")
    else:
        ratio = _has_question(examples)
        if ratio >= 0.34:
            priors["curiosity"] = Asserted.inferred(0.7, basis=f"例句问句比例 {ratio:.0%}")
        elif ratio > 0:
            priors["curiosity"] = Asserted.inferred(0.55, basis=f"例句问句比例 {ratio:.0%}")
        else:
            priors["curiosity"] = Asserted.inferred(0.4, basis="例句无问句")

    return priors


def phase_threshold(profile: PersonProfile, base: float = PHASE_BASE) -> Asserted[float]:
    """在引擎默认值上按冲突表达方式校准。低于 base = 更易相变。"""
    text = _text_of(profile)
    value = base
    hits: list[str] = []
    for rule in PHASE_RULES:
        if re.search(rule.pattern, text, flags=re.I):
            value += rule.delta
            hits.append(f"{rule.reason}（{rule.delta:+.2f}）")

    if not hits:
        return Asserted.missing("profile 未描述冲突反应，保持引擎默认值 2.0")

    clamped = round(min(PHASE_MAX, max(PHASE_MIN, value)), 3)
    note = f"base {base} → {clamped}"
    if clamped != round(value, 3):
        note += f"（原始 {round(value, 3)} 已截断至 [{PHASE_MIN}, {PHASE_MAX}]）"
    return Asserted.inferred(clamped, basis=f"{note}；" + "；".join(hits))


def drive_sensitivity(profile: PersonProfile) -> dict[str, Asserted[float]]:
    """哪些驱力被挫败时反应最强烈（0..1）。优先用 #33 的 drive_baseline。"""
    from bridge.calibrate import calibrate

    hints = profile.engine
    baseline = hints.drive_baseline if hints and hints.drive_baseline else {}
    if not baseline:
        baseline = calibrate(profile).drive_baseline

    out: dict[str, Asserted[float]] = {}
    for drive, asserted in baseline.items():
        value = asserted.value if isinstance(asserted, Asserted) else None
        if value is None:
            out[drive] = Asserted.missing("drive_baseline 缺失且无法推断")
        else:
            # 驱力基线越高 = 越在意 = 被挫败时反应越强
            out[drive] = Asserted.inferred(
                round(min(1.0, value), 3),
                basis=(asserted.basis or "drive_baseline") + "；基线与挫败敏感度按同向处理",
            )
    return out


# ---------------------------------------------------------------- critic 注入


def critic_persona_section(profile: PersonProfile, *, lang: str | None = None) -> str:
    """生成 critic prompt 的 `persona_section`。只写有依据的内容，缺的段落留空不编。"""
    language = (lang or getattr(profile.identity, "lang", "zh") or "zh").lower()
    zh = language.startswith("zh")

    def val(node: Any) -> str:
        return str(node.value) if isinstance(node, Asserted) and node.value else ""

    lines: list[str] = []
    name = val(profile.identity.name)
    if not name:
        return ""

    lines.append(f"角色：{name}" if zh else f"Persona: {name}")

    triggers = []
    if profile.logic:
        if val(profile.logic.pulls_away_when):
            triggers.append(("会退开" if zh else "pulls away") + "：" + val(profile.logic.pulls_away_when))
        if val(profile.logic.opens_up_when):
            triggers.append(("会开口" if zh else "opens up") + "：" + val(profile.logic.opens_up_when))
    if profile.conflict and val(profile.conflict.style):
        triggers.append(("冲突时" if zh else "in conflict") + "：" + val(profile.conflict.style))
    if triggers:
        lines.append(("触发点：" if zh else "Triggers: ") + "；".join(triggers))

    if profile.conflict:
        repair = val(profile.conflict.repair)
        silence = val(profile.conflict.silence)
        parts = [p for p in (silence, repair) if p]
        if parts:
            label = "沉默/修复：" if zh else "Silence / repair: "
            lines.append(label + "；".join(parts))

    bounds = [str(b.value) for b in (profile.conflict.boundaries if profile.conflict else [])
              if isinstance(b, Asserted) and b.value]
    if bounds:
        lines.append(("边界：" if zh else "Boundaries: ") + "；".join(bounds))

    insufficient = not triggers and not (
        profile.conflict and (val(profile.conflict.repair) or val(profile.conflict.silence))
    )
    if insufficient:
        # 没有任何冲突/修复描述：注入通用句等于给 critic 一条无用提示，显式标注不足。
        lines.append(
            "（材料不足：未提供冲突与修复描述，不做角色特化判断）"
            if zh else
            "(Insufficient source material: no conflict/repair description; no persona-specific judging)"
        )

    if insufficient:
        lines.append(
            "按通用判断处理，不要为该角色做特化推断。"
            if zh else
            "Use generic judgement; do not infer persona-specific reactions."
        )
    else:
        lines.append(
            "判断该用户消息的需求满足感与挫败增量时，以上述反应模式为准；"
            "与此不符的推断降低置信度。"
            if zh else
            "Judge drive satisfaction and frustration deltas through the reaction pattern above; "
            "lower confidence for inferences that contradict it."
        )
    return "\n".join(lines)


def analyze(profile: PersonProfile) -> dict[str, Any]:
    """一次性产出 #38 的三项结果 + 注入文本。"""
    priors = signal_priors(profile)
    phase = phase_threshold(profile)
    drives = drive_sensitivity(profile)
    section = critic_persona_section(profile)

    missing = [s for s, v in priors.items() if v.provenance.value == "missing"]
    usable = bool(
        (profile.conflict is not None)
        or (profile.logic is not None and (profile.logic.pulls_away_when or profile.logic.opens_up_when))
    )
    return {
        "persona_id": profile.persona_id,
        "usable": usable,
        "signal_priors": {
            key: {"value": v.value, "provenance": v.provenance.value, "basis": v.basis}
            for key, v in priors.items()
        },
        "phase_threshold": {
            "value": phase.value, "provenance": phase.provenance.value, "basis": phase.basis,
            "base": PHASE_BASE, "range": [PHASE_MIN, PHASE_MAX],
        },
        "drive_sensitivity": {
            key: {"value": v.value, "provenance": v.provenance.value, "basis": v.basis}
            for key, v in drives.items()
        },
        "critic_persona_section": section,
        "coverage": {
            "signals_with_evidence": len(SIGNALS) - len(missing),
            "signals_total": len(SIGNALS),
            "missing": missing,
        },
    }


__all__ = [
    "SIGNALS", "SIGNAL_LABELS", "SIGNAL_RULES", "PHASE_RULES", "PHASE_BASE",
    "signal_priors", "phase_threshold", "drive_sensitivity",
    "critic_persona_section", "analyze",
]
