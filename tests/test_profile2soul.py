# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#32 身份字段 → SOUL.md frontmatter 的回归校验。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.model import Asserted, Identity, PersonProfile, Provenance  # noqa: E402
from bridge.profile2soul import (  # noqa: E402
    LOADER_KEYS,
    identity_to_frontmatter,
    profile_to_soul,
    render_frontmatter,
)


def base_profile(**identity_kwargs) -> PersonProfile:
    return PersonProfile(
        persona_id="xiaolan",
        identity=Identity(**identity_kwargs),
    )


def xiaolan() -> PersonProfile:
    """对应 demo-relationship：age/gender/mbti 都是『未提供』。"""
    return base_profile(
        name=Asserted.source("小岚"),
        name_zh=Asserted.source("小岚"),
        lang="zh",
        age=Asserted.missing("meta.profile.age_range 为『未提供』"),
        gender=Asserted.missing("meta.profile.gender 为『未提供』"),
        mbti=Asserted.missing("六层不含 MBTI"),
    )


def test_source_fields_written() -> None:
    fields, warnings = identity_to_frontmatter(xiaolan())
    assert fields["name"] == "小岚"
    assert fields["name_zh"] == "小岚"
    assert fields["lang"] == "zh"
    assert warnings  # gender 缺失应被报告


def test_missing_fields_are_omitted() -> None:
    fields, _ = identity_to_frontmatter(xiaolan())
    for key in ("age", "gender", "mbti"):
        assert key not in fields, key


def test_gender_fallback_is_reported() -> None:
    _, warnings = identity_to_frontmatter(xiaolan())
    assert any("gender" in w and "female" in w for w in warnings), warnings


def test_strict_mode_raises_on_fallback() -> None:
    try:
        profile_to_soul(xiaolan(), strict=True)
    except ValueError as exc:
        assert "female" in str(exc)
        return
    raise AssertionError("strict=True 时 gender 缺失应抛 ValueError")


def test_inferred_value_written_but_basis_not_leaked() -> None:
    profile = base_profile(
        name=Asserted.source("林舟"),
        mbti=Asserted.inferred("INTJ", basis="从『先问影响』的判断习惯推断"),
    )
    fields, _ = identity_to_frontmatter(profile)
    assert fields["mbti"] == "INTJ"
    markdown = render_frontmatter(fields)
    assert "先问影响" not in markdown
    assert "basis" not in markdown


def test_only_loader_keys_are_emitted() -> None:
    profile = xiaolan()
    profile.meta["unknown_field"] = "x"
    fields, _ = identity_to_frontmatter(profile)
    for key in fields:
        assert key in LOADER_KEYS, key


def test_markdown_shape() -> None:
    result = profile_to_soul(xiaolan())
    markdown = result["markdown"]
    assert markdown.startswith("---")
    lines = markdown.splitlines()
    assert lines[0] == "---"
    assert "---" in lines[1:]
    assert "Layer" not in markdown
    assert result["body"] == {}


def test_quote_escaping() -> None:
    profile = base_profile(name=Asserted.source("名字: 带冒号"))
    markdown = profile_to_soul(profile)["markdown"]
    assert '"名字: 带冒号"' in markdown


def test_tags_passthrough() -> None:
    profile = xiaolan()
    profile.meta["tags"] = {"zh": ["短句", "不直接示弱"]}
    fields, _ = identity_to_frontmatter(profile)
    assert fields["tags"] == {"zh": ["短句", "不直接示弱"]}


def test_yaml_roundtrip_when_available() -> None:
    try:
        import yaml
    except ImportError:
        print("SKIP: pyyaml 未安装")
        return
    result = profile_to_soul(xiaolan())
    body = result["markdown"].split("---")[1]
    parsed = yaml.safe_load(body)
    assert parsed["name"] == "小岚"
    assert parsed["lang"] == "zh"
    assert "age" not in parsed


def test_layer_marker_is_rejected() -> None:
    profile = xiaolan()
    profile.identity.name = Asserted.source("Layer 0 的主人")
    try:
        profile_to_soul(profile)
    except ValueError as exc:
        assert "Layer" in str(exc)
        return
    raise AssertionError("名称含 Layer N 应被拒绝")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} profile2soul tests passed")
