# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#42 `alpha profile2soul`：body 渲染 + CLI 行为。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.model import Asserted, Identity, PersonProfile, profile_from_dict  # noqa: E402
from cli.profile2soul_cmd import (  # noqa: E402
    TITLES,
    body_from_profile,
    find_profiles,
    render_soul,
    soul_path_for,
    unified_diff,
)

FIXTURE = ROOT / "tests" / "fixtures" / "profiles" / "xiaolan.json"


def _single(tmp: str) -> Path:
    """只含 xiaolan 的输入目录 —— 其他 fixture 带告警会干扰退出码断言。"""
    inbox = Path(tmp) / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "xiaolan.json").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return inbox
LOADER_TITLES = {"性格", "说话风格", "背景故事", "行为规则"}


def _load(path: Path = FIXTURE) -> PersonProfile:
    return profile_from_dict(json.loads(path.read_text(encoding="utf-8")))


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=ROOT, capture_output=True, text=True,
    )


def test_body_has_four_sections() -> None:
    body, warnings = body_from_profile(_load())
    assert set(body) == {"personality", "speaking_style", "background", "behavioral_rules"}
    assert warnings == []


def test_body_only_uses_loader_titles() -> None:
    result = render_soul(_load())
    headings = set(re.findall(r"^## (.+)$", result["markdown"], flags=re.M))
    assert headings <= LOADER_TITLES, headings
    assert "性格" in headings and "说话风格" in headings


def test_no_layer_marker_leaks() -> None:
    result = render_soul(_load())
    assert not re.search(r"\bLayer\s*\d", result["markdown"])


def test_list_fields_render_as_text_not_dict() -> None:
    result = render_soul(_load())
    assert "'provenance'" not in result["markdown"]
    assert "说话算数" in result["markdown"]


def test_missing_sections_warned() -> None:
    bare = PersonProfile(persona_id="bare", identity=Identity(name=Asserted.source("阿七")))
    result = render_soul(bare)
    assert set(result["missing_sections"]) == {"personality", "speaking_style", "background"}
    assert any("raw_content" in w for w in result["warnings"])
    assert "## 性格" not in result["markdown"]


def test_partial_profile_keeps_present_sections() -> None:
    profile = PersonProfile(persona_id="p", identity=Identity(name=Asserted.source("甲")))
    profile.hard_rules = [Asserted.source("不主动联系")]
    result = render_soul(profile)
    assert "behavioral_rules" in result["body"]
    assert "不主动联系" in result["markdown"]
    assert result["missing_sections"] == ["personality", "speaking_style", "background"]


def test_english_titles() -> None:
    profile = PersonProfile(persona_id="p", identity=Identity(name=Asserted.source("A"), lang="en"))
    profile.hard_rules = [Asserted.source("never lie")]
    result = render_soul(profile)
    assert "## Behavioral Rules" in result["markdown"]
    assert TITLES["en"]["behavioral_rules"] == "Behavioral Rules"


def test_strict_mode_rejects_fallback() -> None:
    profile = PersonProfile(persona_id="p", identity=Identity(name=Asserted.source("甲")))
    try:
        render_soul(profile, strict=True)
    except ValueError as exc:
        assert "回落" in str(exc)
    else:
        raise AssertionError("strict 模式应因 gender/lang 回落失败")


def test_find_profiles_dir_and_file() -> None:
    from_dir = find_profiles(FIXTURE.parent)
    assert any(p.persona_id == "xiaolan" for _, p in from_dir)
    from_file = find_profiles(FIXTURE)
    assert len(from_file) == 1 and from_file[0][1].persona_id == "xiaolan"


def test_soul_path_by_persona_id() -> None:
    path = soul_path_for(Path("/tmp/x"), _load())
    assert path == Path("/tmp/x/xiaolan/SOUL.md")


def test_unified_diff_shape() -> None:
    diff = unified_diff("a\nb\n", "a\nc\n", "p/SOUL.md")
    assert "-b" in diff and "+c" in diff


def test_cli_dry_run_does_not_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run(["profile2soul", str(_single(tmp)), "--out", str(Path(tmp) / "soul")])
        assert proc.returncode == 0, proc.stderr
        assert not list(Path(tmp).rglob("SOUL.md"))


def test_cli_write_then_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        first = _run(["profile2soul", str(_single(tmp)), "--out", tmp, "--write"])
        assert first.returncode == 0, first.stderr
        dest = Path(tmp) / "xiaolan" / "SOUL.md"
        assert dest.exists()

        second = _run(["profile2soul", str(_single(tmp)), "--out", tmp, "--check"])
        assert second.returncode == 0, second.stdout
        assert "无变化" in second.stdout


def test_cli_check_fails_when_changed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert _run(["profile2soul", str(_single(tmp)), "--out", tmp,
                     "--check"]).returncode == 1


def test_cli_rejects_missing_path() -> None:
    proc = _run(["profile2soul", "/nonexistent/path"])
    assert proc.returncode == 2


def test_list_deserialization_is_symmetric() -> None:
    profile = _load()
    assert all(isinstance(i, Asserted) for i in profile.logic.priorities)
    assert all(isinstance(i, Asserted) for i in profile.conflict.boundaries)
    assert all(isinstance(i, Asserted) for i in profile.relation.defining_facts)


def test_loader_accepts_output_when_vendor_present() -> None:
    vendor = ROOT / "vendor" / "OpenHer"
    loader_file = vendor / "persona" / "loader.py"
    if not loader_file.exists():
        return  # CI 无 vendor 时跳过；#41 会在 vendor 存在时做完整校验

    with tempfile.TemporaryDirectory() as tmp:
        assert _run(["profile2soul", str(_single(tmp)), "--out", tmp,
                     "--write"]).returncode == 0
        sys.path.insert(0, str(vendor))
        try:
            from persona.loader import PersonaLoader  # type: ignore

            loaded = PersonaLoader(str(tmp)).get("xiaolan")
            assert loaded is not None, "loader 未找到 xiaolan"
        finally:
            sys.path.remove(str(vendor))
        # 三个核心段必须非空，否则说明只落到了 raw_content
        assert getattr(loaded, "personality", None), "personality 未解析"
        assert getattr(loaded, "speaking_style", None), "speaking_style 未解析"
        assert getattr(loaded, "background", None), "background 未解析"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} profile2soul tests passed")
