# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""profile2soul —— 把 PersonProfile 翻译成 OpenHer 可消费的 SOUL.md。

本阶段（#32）只实现**身份字段 → frontmatter**；正文四槽的映射属 #33–#38。

关键约束（全部来自 P2 的实测）：

1. `PersonaLoader` 只读取固定键：`name` / `name_zh` / `age` / `gender` / `lang` /
   `mbti` / `tags` / `bio` / `genome_seed`。写别的键既不生效也不报错，等于静默丢弃。
2. `gender` 缺失时 loader 会回落 `"female"`（#30 §7.2 实测），所以缺失必须在
   `warnings` 里显式报告，不能假装写成功。
3. 三态里的 `basis` **不写入 frontmatter**：loader 不读，且推断说明会污染人格文本。
4. 输出中不得出现 `Layer N` 字样（#30 实测会作为结构标记泄漏）。
"""

from __future__ import annotations

import re
from typing import Any

from bridge.model import Asserted, PersonProfile, Provenance

# PersonaLoader 实际读取的 frontmatter 键
LOADER_KEYS = (
    "name", "name_zh", "age", "gender", "lang", "mbti", "tags", "bio", "genome_seed",
)

# loader 对这些键有默认值；缺失会造成"静默的错误值"
LOADER_DEFAULTS = {"gender": "female", "lang": "zh"}

BODY_SECTIONS = ("personality", "speaking_style", "background", "behavioral_rules")

_NEEDS_QUOTE = re.compile(r"[:\#\[\]\{\}\,\&\*\!\|\>\'\"%@`]|^\s|\s$")


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or _NEEDS_QUOTE.search(text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def identity_to_frontmatter(profile: PersonProfile) -> tuple[dict[str, Any], list[str]]:
    """身份字段 → frontmatter 字典。

    返回 `(fields, warnings)`。三态规则：

    - `source` / `inferred`：写入值（`basis` 不写入）；
    - `missing`：不写该键；若该键有 loader 默认值，追加一条 warning。
    """
    warnings: list[str] = []
    fields: dict[str, Any] = {}

    def put(key: str, asserted: Asserted[Any] | None, *, transform=None) -> None:
        if asserted is None:
            # 字段整个没给（None）同样是隐性缺失，不能静默放过 —— 否则 loader 会
            # 悄悄用上默认值，而 warnings 里看不出任何痕迹（#32 遗留漏洞）。
            if key in LOADER_DEFAULTS:
                warnings.append(
                    f"{key} 未提供：不写入 frontmatter，但 PersonaLoader 会回落为 "
                    f"'{LOADER_DEFAULTS[key]}'（#30 §7.2 实测损耗）"
                )
            return
        if asserted.provenance is Provenance.MISSING:
            if key in LOADER_DEFAULTS:
                warnings.append(
                    f"{key} 缺失：不写入 frontmatter，但 PersonaLoader 会回落为 "
                    f"'{LOADER_DEFAULTS[key]}'（#30 §7.2 实测损耗）"
                )
            return
        value = asserted.value if transform is None else transform(asserted.value)
        if value is None or (isinstance(value, str) and not value.strip()):
            warnings.append(f"{key} 声明为 {asserted.provenance.value} 但值为空，已跳过")
            return
        fields[key] = value

    identity = profile.identity
    put("name", identity.name)
    put("name_zh", identity.name_zh)
    put("age", identity.age, transform=lambda v: int(v))
    put("gender", identity.gender)
    put("mbti", identity.mbti)
    fields["lang"] = identity.lang or "zh"

    # tags / bio 不在 #31 模型里，允许通过 meta 透传（来源仍是"外部给定"）
    tags = profile.meta.get("tags")
    if isinstance(tags, dict) and tags:
        fields["tags"] = {k: list(v) for k, v in tags.items() if isinstance(v, (list, tuple))}
    elif isinstance(tags, (list, tuple)) and tags:
        fields["tags"] = {"zh": list(tags)}

    bio = profile.meta.get("bio")
    if isinstance(bio, (dict, str)) and bio:
        fields["bio"] = bio

    return fields, warnings


def render_frontmatter(fields: dict[str, Any]) -> str:
    """把字典渲染成 YAML frontmatter（只支持标量、列表、一层嵌套的字典）。"""
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, (list, tuple)):
                    rendered = ", ".join(_yaml_scalar(item) for item in sub_value)
                    lines.append(f"  {sub_key}: [{rendered}]")
                else:
                    lines.append(f"  {sub_key}: {_yaml_scalar(sub_value)}")
        elif isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(item)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def profile_to_soul(profile: PersonProfile, *, strict: bool = False) -> dict[str, Any]:
    """PersonProfile → SOUL.md 结构。

    返回::

        {"frontmatter": {...}, "body": {...}, "warnings": [...], "markdown": "..."}

    `body` 目前为空字典：正文映射属 #33–#38（`#32` 只交付身份字段）。
    `strict=True` 时，出现 `gender`/`lang` 回落警告会抛 `ValueError`。
    """
    fields, warnings = identity_to_frontmatter(profile)
    if strict:
        blocking = [w for w in warnings if "回落" in w]
        if blocking:
            raise ValueError("; ".join(blocking))

    body: dict[str, str] = {}
    markdown = render_frontmatter(fields)
    if body:
        blocks = [markdown]
        for section in BODY_SECTIONS:
            content = body.get(section)
            if content:
                blocks.append(f"## {_TITLES[section]}\n\n{content.strip()}")
        markdown = "\n\n".join(blocks) + "\n"

    if re.search(r"\bLayer\s*\d", markdown):
        raise ValueError("输出含 Layer N 字样，会被作为结构标记泄漏")

    return {
        "frontmatter": fields,
        "body": body,
        "warnings": warnings,
        "markdown": markdown,
    }


_TITLES = {
    "personality": "性格",
    "speaking_style": "说话风格",
    "background": "背景故事",
    "behavioral_rules": "行为规则",
}

__all__ = [
    "LOADER_KEYS", "LOADER_DEFAULTS", "BODY_SECTIONS",
    "identity_to_frontmatter", "render_frontmatter", "profile_to_soul",
]
