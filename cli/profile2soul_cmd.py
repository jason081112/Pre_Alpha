# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""`alpha profile2soul` —— PersonProfile 目录 → SOUL.md 一键生成 + dry-run diff。

设计约束（来自 #28 / #30 源码事实）：

* OpenHer `PersonaLoader` 只精确匹配 4 个 H2：性格 / 说话风格 / 背景故事 / 行为规则；
  `## Layer N` 不匹配。
* `raw_content` 仅在 personality / speaking_style / background **全空**时兜底，
  所以段落标题写错会让人格**静默丢失**而加载仍返回成功。
* 材料缺失时不得编造（#29 F5），只跳过该行并给 warning。
"""

from __future__ import annotations

import json
import sys
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.model import PersonProfile, profile_from_dict  # noqa: E402
from bridge.profile2soul import profile_to_soul  # noqa: E402

TITLES = {
    "zh": {
        "personality": "性格",
        "speaking_style": "说话风格",
        "background": "背景故事",
        "behavioral_rules": "行为规则",
    },
    "en": {
        "personality": "Personality",
        "speaking_style": "Speaking Style",
        "background": "Background",
        "behavioral_rules": "Behavioral Rules",
    },
}

LABELS = {
    "zh": {
        "opens_up_when": "愿意开口的时候",
        "pulls_away_when": "会退开的时候",
        "defends_by": "防御方式",
        "shows_care_by": "表达在意的方式",
        "priorities": "在意的事",
        "rhythm": "说话节奏",
        "habits": "语言习惯",
        "examples": "例句",
        "relation": "与用户的关系",
        "facts": "共同经历",
        "memory": "记得的事",
        "hard_rules": "硬规则",
        "conflict_style": "冲突时的反应",
        "silence": "沉默的样子",
        "repair": "和好的方式",
        "boundaries": "边界",
    },
    "en": {
        "opens_up_when": "Opens up when",
        "pulls_away_when": "Pulls away when",
        "defends_by": "Defends by",
        "shows_care_by": "Shows care by",
        "priorities": "Priorities",
        "rhythm": "Rhythm",
        "habits": "Language habits",
        "examples": "Examples",
        "relation": "Relation to user",
        "facts": "Shared facts",
        "memory": "Remembers",
        "hard_rules": "Hard rules",
        "conflict_style": "In conflict",
        "silence": "Silence",
        "repair": "Repair",
        "boundaries": "Boundaries",
    },
}


def _value(asserted: Any) -> Any:
    """取 Asserted.value；裸值原样返回。"""
    if asserted is None:
        return None
    if isinstance(asserted, dict):
        return asserted.get("value")
    if hasattr(asserted, "value"):
        return asserted.value
    return asserted


def _bullets(items: list[Any]) -> list[str]:
    out = []
    for item in items:
        text = _value(item)
        if text:
            out.append(str(text))
    return out


def body_from_profile(profile: PersonProfile) -> tuple[dict[str, str], list[str]]:
    """PersonProfile → 4 个 H2 段的正文。缺失的段落跳过并记 warning，绝不编造。"""
    lang = "en" if str(getattr(profile.identity, "lang", "zh")).lower().startswith("en") else "zh"
    titles, labels = TITLES[lang], LABELS[lang]
    # 分隔符随语言切换：中文用全角，英文用半角 —— 否则英文正文里会混进「：」「、」
    COLON = "：" if lang == "zh" else ": "
    LIST = "、" if lang == "zh" else ", "
    SEMI = "；" if lang == "zh" else "; "
    body: dict[str, str] = {}
    warnings: list[str] = []

    # 性格 ← logic
    lines: list[str] = []
    if profile.logic is not None:
        for key in ("opens_up_when", "pulls_away_when", "defends_by", "shows_care_by"):
            text = _value(getattr(profile.logic, key))
            if text:
                lines.append(f"- {labels[key]}{COLON}{text}")
        priorities = _bullets(profile.logic.priorities)
        if priorities:
            lines.append(f"- {labels['priorities']}{COLON}{LIST.join(priorities)}")
    if lines:
        body["personality"] = "\n".join(lines)
    else:
        warnings.append("personality 段无材料（logic 为空，加载后该段缺失）")

    # 说话风格 ← expression
    lines = []
    rhythm = _value(profile.expression.rhythm)
    if rhythm:
        lines.append(f"- {labels['rhythm']}{COLON}{rhythm}")
    habits = _bullets(profile.expression.habits)
    if habits:
        lines.append(f"- {labels['habits']}{COLON}{LIST.join(habits)}")
    if profile.expression.examples:
        lines.append(f"- {labels['examples']}{COLON}".rstrip())
        for ex in profile.expression.examples:
            scene = (ex.scene or "").strip()
            text = (ex.text or "").strip()
            if text:
                lines.append(f"  - {scene}{COLON}“{text}”" if scene else f"  - “{text}”")
    if lines:
        body["speaking_style"] = "\n".join(lines)
    else:
        warnings.append("speaking_style 段无材料（expression 为空，加载后该段缺失）")

    # 背景故事 ← relation + memory_signature
    lines = []
    if profile.relation is not None:
        label = _value(profile.relation.label)
        status = _value(profile.relation.status)
        if label:
            text = f"- {labels['relation']}{COLON}{label}"
            if status:
                text += f"（{status}）"
            lines.append(text)
        facts = _bullets(profile.relation.defining_facts)
        if facts:
            lines.append(f"- {labels['facts']}{COLON}{SEMI.join(facts)}")
    memory = _bullets(profile.memory_signature)
    if memory:
        lines.append(f"- {labels['memory']}{COLON}{SEMI.join(memory)}")
    if lines:
        body["background"] = "\n".join(lines)
    else:
        warnings.append("background 段无材料（relation / memory_signature 为空）")

    # 行为规则 ← hard_rules + conflict
    lines = []
    rules = _bullets(profile.hard_rules)
    if rules:
        lines.append(f"- {labels['hard_rules']}{COLON}{SEMI.join(rules)}")
    if profile.conflict is not None:
        for key, label in (
            ("style", "conflict_style"),
            ("silence", "silence"),
            ("repair", "repair"),
        ):
            text = _value(getattr(profile.conflict, key))
            if text:
                lines.append(f"- {labels[label]}{COLON}{text}")
        bounds = _bullets(profile.conflict.boundaries)
        if bounds:
            lines.append(f"- {labels['boundaries']}{COLON}{SEMI.join(bounds)}")
    if lines:
        body["behavioral_rules"] = "\n".join(lines)
    else:
        warnings.append("behavioral_rules 段无材料（hard_rules / conflict 为空）")

    return body, warnings


def render_soul(profile: PersonProfile, *, strict: bool = False) -> dict[str, Any]:
    """生成完整 SOUL.md：frontmatter（#32）+ 正文（#42）。"""
    result = profile_to_soul(profile, strict=strict)
    body, body_warnings = body_from_profile(profile)
    lang = "en" if str(getattr(profile.identity, "lang", "zh")).lower().startswith("en") else "zh"
    titles = TITLES[lang]

    blocks = [result["markdown"].rstrip()]
    for section in ("personality", "speaking_style", "background", "behavioral_rules"):
        content = body.get(section)
        if content:
            blocks.append(f"## {titles[section]}\n\n{content}")
    markdown = "\n\n".join(blocks).rstrip() + "\n"

    empty = [s for s in ("personality", "speaking_style", "background") if not body.get(s)]
    if len(empty) == 3:
        body_warnings.append(
            "三个核心段全空 → loader 会退到 raw_content，人格实际不会进 prompt"
        )

    return {
        "frontmatter": result["frontmatter"],
        "body": body,
        "warnings": result["warnings"] + body_warnings,
        "markdown": markdown,
        "missing_sections": empty,
    }


# ---------------------------------------------------------------- 目录与 diff


def find_profiles(target: Path) -> list[tuple[str, PersonProfile]]:
    """<dir> 下所有 *.json 逐个解析；也接受直接传单个 .json。"""
    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = sorted(p for p in target.glob("*.json") if p.is_file())
    else:
        raise FileNotFoundError(f"路径不存在: {target}")

    found: list[tuple[str, PersonProfile]] = []
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        record = raw.get("profile", raw) if isinstance(raw, dict) else raw
        profile = profile_from_dict(record)
        found.append((path.name, profile))
    return found


def soul_path_for(out_dir: Path, profile: PersonProfile) -> Path:
    return out_dir / profile.persona_id / "SOUL.md"


def unified_diff(old: str, new: str, name: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            n=3,
        )
    )


__all__ = [
    "TITLES", "LABELS", "body_from_profile", "render_soul",
    "find_profiles", "soul_path_for", "unified_diff",
]


# ---------------------------------------------------------------- CLI


def run(args: Any) -> int:
    target = Path(args.path).expanduser()
    out_root = Path(args.out).expanduser() if args.out else (ROOT / ".data" / "soul")

    try:
        profiles = find_profiles(target)
    except FileNotFoundError as exc:
        print(f"[alpha] {exc}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"[alpha] profile 解析失败: {exc}", file=sys.stderr)
        return 2

    if not profiles:
        print(f"[alpha] {target} 下没有 *.json", file=sys.stderr)
        return 2

    print(f"[alpha] 找到 {len(profiles)} 个 profile，输出根 {out_root}"
          f"{'（dry-run，不写入）' if not args.write else ''}")

    changed = 0
    problems = 0
    report: list[dict[str, Any]] = []

    for filename, profile in profiles:
        try:
            result = render_soul(profile, strict=args.strict)
        except ValueError as exc:
            print(f"[alpha] {profile.persona_id}: 生成失败 —— {exc}", file=sys.stderr)
            problems += 1
            continue

        dest = soul_path_for(out_root, profile)
        old = dest.read_text(encoding="utf-8") if dest.exists() else ""
        new = result["markdown"]
        diff = unified_diff(old, new, f"{profile.persona_id}/SOUL.md")
        warnings = result["warnings"]

        print(f"\n=== {profile.persona_id}  ({filename}) ===")
        print(f"  段落: {'/'.join(result['body']) or '（无）'}")
        if not old:
            preview = new.rstrip().splitlines()
            print(f"  目标不存在 → 新增 SOUL.md（{len(preview)} 行）")
            for line in preview:
                print(f"    | {line}")
        elif not diff:
            print("  无变化")
        else:
            print("  有变化：")
            print("\n".join("    " + line for line in diff.rstrip().splitlines()))
        for warn in warnings:
            print(f"  ⚠ {warn}")

        if diff or not old:
            changed += 1
        if warnings:
            problems += 1

        if args.write and (diff or not old):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(new, encoding="utf-8")
            print(f"  已写入 {dest}")

        report.append({
            "persona_id": profile.persona_id,
            "source": filename,
            "dest": str(dest),
            "changed": bool(diff or not old),
            "missing_sections": result["missing_sections"],
            "warnings": warnings,
            "bytes": len(new.encode("utf-8")),
        })

    print(f"\n[alpha] 汇总：{len(profiles)} 个 profile，{changed} 个有变更，"
          f"{problems} 个有告警{'，已写入' if args.write else '，未写入（加 --write）'}")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.check:
        return 1 if (changed or problems) else 0
    return 1 if problems and args.strict else 0
