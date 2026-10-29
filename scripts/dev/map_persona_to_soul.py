#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""把 Distilly 的 persona.md 映射成 OpenHer 的 SOUL.md。

对应 docs/p2-30-field-mapping.md：

- Layer 0 硬规则  → 正文「行为规则」首段（R2）
- Layer 1 身份/关系 + Layer 3 情绪逻辑 → 正文「性格」（R1）
- Layer 2 Rhythm → 正文「说话风格」
- Layer 4 Boundaries → 正文「行为规则」末尾
- Correction Log / Memory Signature 不写入正文（无落点）
- General Behavior Principles 追加到「行为规则」（是否保留待 #29）

输出中不得出现 ``Layer N`` 字样：PersonaLoader 只认 4 个固定 H2，其余内容会掉进
raw_content 并在结构化节非空时被静默丢弃，同时还可能作为结构标记泄漏到 prompt。

Usage:
    scripts/dev/map_persona_to_soul.py \
        --persona tests/fixtures/golden/demo-relationship/persona.md \
        --meta tests/fixtures/golden/demo-relationship/meta.json \
        --persona-id xiaolan --out /tmp/xiaolan/SOUL.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LAYER_RE = re.compile(r"^## Layer (\d+): (.+)$")
H3_RE = re.compile(r"^### (.+)$")
H2_RE = re.compile(r"^## (.+)$")


def split_sections(markdown: str) -> dict[str, str]:
    """按 H2 切分，key 用原始标题。"""
    sections: dict[str, str] = {}
    current = None
    lines: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = line[3:].strip()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def split_subsections(body: str) -> dict[str, str]:
    """按 H3 切分，key 小写。"""
    sections: dict[str, str] = {}
    current = None
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("### "):
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = line[4:].strip().lower()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def bullets(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
    return out


def plain(text: str) -> str:
    """去掉引用块、编号、多余空白，保留正文。"""
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if set(stripped) == {"-"} or stripped in {"---", "***"}:
            continue
        stripped = re.sub(r"^>\s*", "", stripped)
        rows.append(stripped)
    return "\n".join(rows)


LAYER_REF_RE = re.compile(r"Layer\s*(\d)|Correction\s+layer", re.IGNORECASE)
LAYER_REF_MAP = {
    "0": "最高优先级规则",
    "1": "身份与关系",
    "2": "说话风格",
    "3": "情绪与判断逻辑",
    "4": "冲突与修复方式",
    "5": "边界与记忆签名",
    "correction": "纠错层",
}


def rewrite_layer_refs(text: str) -> str:
    """把 *Layer N* 之类的结构引用换成中文指代，避免结构标记泄漏到 prompt。"""
    def _sub(match: re.Match) -> str:
        key = match.group(1)
        if key is None:
            return LAYER_REF_MAP["correction"]
        return LAYER_REF_MAP.get(key, match.group(0))
    return LAYER_REF_RE.sub(_sub, text)


def build(persona_path: Path, meta_path: Path | None, persona_id: str, name_override: str | None,
          keep: bool = False) -> tuple[str, dict]:
    markdown = persona_path.read_text(encoding="utf-8")
    sections = split_sections(markdown)

    layers: dict[int, str] = {}
    for title, body in sections.items():
        match = LAYER_RE.match(f"## {title}")
        if match:
            layers[int(match.group(1))] = body
    missing = [n for n in range(6) if n not in layers]
    if missing:
        raise SystemExit(f"ERROR: persona.md 缺少 Layer {missing}")

    meta: dict = {}
    if meta_path and meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    layer1_subs = split_subsections(layers[1])
    layer2_subs = split_subsections(layers[2])
    layer3_subs = split_subsections(layers[3])
    layer4_subs = split_subsections(layers[4])

    name = name_override or meta.get("display_name") or meta.get("name") or persona_id
    relationship = (meta.get("profile") or {}).get("relationship_subtype", "")
    status = (meta.get("profile") or {}).get("current_status", "")
    tags = meta.get("tags") or []
    language = ((meta.get("classification") or {}).get("language") or "zh-CN").split("-")[0]

    # ---- 性格：Layer 1 关系上下文 + Layer 3 情绪逻辑（R1）
    personality_lines = []
    if relationship:
        personality_lines.append(f"你和用户的关系是：{relationship}。")
    if status:
        personality_lines.append(f"当前状态：{status}。")
    connection = bullets(layers[1])
    if connection:
        personality_lines.append("关系的底子：")
        personality_lines.extend(f"- {item}" for item in connection)

    logic_map = [
        ("opens up when", "会让你打开"),
        ("pulls away when", "会让你退开"),
        ("defends themselves by", "你的防御方式是"),
        ("shows care by", "你表达关心的方式是"),
    ]
    logic_lines = []
    for key, label in logic_map:
        value = plain(layer3_subs.get(key, ""))
        if value:
            logic_lines.append(f"{label}：{value}")
    if logic_lines:
        personality_lines.append("")
        personality_lines.extend(logic_lines)

    # ---- 说话风格：Layer 2 Rhythm 直贴（同族模板用 Rhythm；colleague 族为 how you talk）
    speaking = plain(layer2_subs.get("rhythm", "") or layer2_subs.get("how you talk", ""))
    phrasing = bullets(layers[2])

    # ---- 行为规则：Layer 0（R2）+ Layer 4 Boundaries + General Behavior Principles
    rules = ["以下规则优先级最高，任何情况下不得违反：", ""]
    rules.extend(f"- {item}" for item in bullets(layers[0]))
    boundaries = bullets(layer4_subs.get("boundaries", ""))
    if boundaries:
        rules.append("")
        rules.append("边界：")
        rules.extend(f"- {item}" for item in boundaries)
    principles = sections.get("General Behavior Principles", "")
    keep_principles = bool(keep)
    if principles.strip() and keep_principles:
        rules.append("")
        rules.append("每次对话都要遵守：")
        for line in plain(principles).splitlines():
            if line.lower().startswith("in every interaction"):
                continue
            rules.append(f"- {rewrite_layer_refs(line)}")

    front = ["---", f"name: {name}", f"name_zh: {name}", f"lang: {language}"]
    if tags:
        front.append("tags:")
        front.append("  zh: [" + ", ".join(tags) + "]")
    front.append("---")

    doc = []
    doc.extend(front)
    doc.append("")
    doc.append("## 性格")
    doc.append("")
    doc.extend(personality_lines)
    doc.append("")
    doc.append("## 说话风格")
    doc.append("")
    if speaking:
        doc.append(speaking)
    if phrasing:
        doc.append("")
        doc.append("常用说法：")
        doc.extend(f"- {item}" for item in phrasing)
    doc.append("")
    doc.append("## 行为规则")
    doc.append("")
    doc.extend(rules)
    doc.append("")

    text = "\n".join(doc)

    report = {
        "persona_id": persona_id,
        "name": name,
        "sections_used": {
            "Layer 0": "行为规则",
            "Layer 1": "性格",
            "Layer 2": "说话风格",
            "Layer 3": "性格",
            "Layer 4": "行为规则（边界）",
            "Layer 5": "未映射（无落点）",
            "Correction Log": "未映射（无落点）",
            "General Behavior Principles": "行为规则（追加，Layer 引用已改写）" if keep else "未映射（默认丢弃，避免结构标记泄漏；是否保留待 #29）",
        },
        "dropped_sections": [t for t in sections if LAYER_RE.match(f"## {t}") and t.startswith("Layer 5")],
        "layer_leak": bool(re.search(r"\bLayer\s*\d", text)),
        "bytes": len(text.encode("utf-8")),
    }
    return text, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", required=True, type=Path)
    parser.add_argument("--meta", type=Path)
    parser.add_argument("--persona-id", required=True)
    parser.add_argument("--name")
    parser.add_argument("--keep-principles", action="store_true",
                        help="追加 General Behavior Principles（Layer 引用会被改写）")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    text, report = build(args.persona, args.meta, args.persona_id, args.name, args.keep_principles)
    if report["layer_leak"]:
        print("ERROR: 输出中仍含 Layer N 字样", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
