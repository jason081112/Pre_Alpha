# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SOUL.md → PersonProfile 的最小导入（#85 的「内置复制」用）。

内置人格只有 `SOUL.md`（无 `profile.json`），要让用户"以此为模板"编辑，
必须先把文本人格读成结构化 profile。本模块只做**可确定的部分**：

- frontmatter：`name` / `name_zh` / `age` / `gender` / `mbti` / `lang`
- 正文四槽：`说话风格` → `expression.rhythm`，`行为规则` → `hard_rules`
- 口头禅：从「说话风格」里的引号短句提取（`“嗯。”` 之类）

其余一律标 `missing` 并写明原因 —— 不猜关系、不猜冲突模式、不造驱动参数。

解析器刻意不依赖 PyYAML：frontmatter 只支持标量与一层列表（内置 persona 即如此）。
"""

from __future__ import annotations

import re
from typing import Any

from bridge.model import (
    Asserted,
    Expression,
    Identity,
    PersonProfile,
    RelationContext,
)

SECTION_TITLES = {
    "性格": "personality",
    "personality": "personality",
    "说话风格": "speaking_style",
    "speaking style": "speaking_style",
    "背景故事": "background",
    "background": "background",
    "背景": "background",
    "行为规则": "behavioral_rules",
    "行为规范": "behavioral_rules",
    "behavioral rules": "behavioral_rules",
    "rules": "behavioral_rules",
}

QUOTE_RE = re.compile(r"[“\"]([^”\"]{1,40})[”\"]")
BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """拆分 frontmatter 与正文。frontmatter 只支持标量与一层列表。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    raw, body = parts[1], parts[2]

    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            stripped = line.strip()
            if current_list_key and stripped.startswith("- "):
                meta.setdefault(current_list_key, []).append(stripped[2:].strip())
            elif current_list_key and ":" in stripped:
                sub_key, sub_value = stripped.split(":", 1)
                meta[f"{current_list_key}.{sub_key.strip()}"] = _scalar(sub_value.strip())
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if value == "":
            current_list_key = key
            meta.setdefault(key, [])
        else:
            current_list_key = None
            meta[key] = _scalar(value)
    return meta, body


def _scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_scalar(item.strip()) for item in inner.split(",")]
    if raw in {">", "|", ">-", "|-"}:
        return ""
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def split_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        if line.startswith("## "):
            key = line[3:].strip().lower()
            current = SECTION_TITLES.get(key, key)
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _asserted_text(meta: dict[str, Any], key: str, reason: str) -> Asserted[str] | None:
    value = meta.get(key)
    if isinstance(value, str) and value.strip():
        return Asserted.source(value.strip())
    if value is None:
        return Asserted.missing(reason)
    return None


def profile_from_soul(soul_text: str, *, persona_id: str, source: str) -> PersonProfile:
    meta, body = split_frontmatter(soul_text)
    sections = split_sections(body)

    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("SOUL.md 缺少 name")

    identity = Identity(
        name=Asserted.source(name.strip()),
        lang=str(meta.get("lang") or "zh"),
        name_zh=_asserted_text(meta, "name_zh", "SOUL.md 未提供 name_zh"),
        age=(
            Asserted.source(int(meta["age"]))
            if isinstance(meta.get("age"), int)
            else Asserted.missing("SOUL.md 未提供 age")
        ),
        gender=_asserted_text(meta, "gender", "SOUL.md 未提供 gender"),
        mbti=_asserted_text(meta, "mbti", "SOUL.md 未提供 mbti"),
    )

    speaking = sections.get("speaking_style", "")
    habits: list[Asserted[str]] = []
    for match in QUOTE_RE.finditer(speaking):
        phrase = match.group(1).strip()
        if 0 < len(phrase) <= 40 and phrase not in [h.value for h in habits]:
            habits.append(Asserted.source(phrase))
        if len(habits) >= 20:
            break

    rules = [
        Asserted.source(match.group(1).strip())
        for line in sections.get("behavioral_rules", "").splitlines()
        if (match := BULLET_RE.match(line))
    ]

    expression = Expression(
        rhythm=(
            Asserted.source(speaking.split("\n")[0].strip())
            if speaking.strip()
            else Asserted.missing("SOUL.md 未提供说话风格")
        ),
        habits=habits,
    )

    # 关系、情绪逻辑、冲突模式都没有可靠来源，明确标缺失
    relation = RelationContext(
        label=Asserted.missing("SOUL.md 未描述与用户的关系"),
        status=Asserted.missing("SOUL.md 未提供关系状态"),
    )

    return PersonProfile(
        persona_id=persona_id,
        identity=identity,
        relation=relation,
        expression=expression,
        hard_rules=rules,
        meta={"imported_from": source, "import_note": "由 SOUL.md 文本导入，仅含可确定字段"},
    )


__all__ = ["split_frontmatter", "split_sections", "profile_from_soul"]
