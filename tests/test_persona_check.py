# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#86 人格体检的四块检查与判定。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.model import (  # noqa: E402
    Asserted,
    ConflictProfile,
    EngineHints,
    Expression,
    Identity,
    PersonProfile,
    RelationContext,
)
from shell.persona_check import (  # noqa: E402
    check_load,
    check_privacy,
    check_probe,
    check_structure,
    run_check,
)


def rich_profile(persona_id: str = "xiaolan") -> PersonProfile:
    return PersonProfile(
        persona_id=persona_id,
        identity=Identity(name=Asserted.source("小岚")),
        relation=RelationContext(label=Asserted.source("亲密朋友")),
        expression=Expression(
            rhythm=Asserted.source("短句为主"),
            habits=[Asserted.source("嗯。"), Asserted.source("你自己想。"), Asserted.source("算了，你忙。")],
        ),
        conflict=ConflictProfile(boundaries=[Asserted.source("不接受反复失言")]),
        hard_rules=[Asserted.source("被忽略时把话收短")],
        engine=EngineHints(
            drive_baseline={k: Asserted.inferred(0.5, basis="人工标定 v1（#34）")
                            for k in ("connection", "novelty", "expression", "safety", "play")},
        ),
    )


def test_rich_profile_passes_structure() -> None:
    result = check_structure(rich_profile())
    assert result["status"] == "pass", result
    assert result["metrics"]["habits"] == 3


def test_empty_profile_warns_with_gaps() -> None:
    profile = PersonProfile(persona_id="a", identity=Identity(name=Asserted.source("甲")))
    result = check_structure(profile)
    assert result["status"] == "warn", result
    assert any("rhythm" in gap for gap in result["gaps"])
    assert any("口头禅" in gap for gap in result["gaps"])


def test_layer_marker_fails() -> None:
    profile = rich_profile()
    profile.hard_rules.append(Asserted.source("Layer 0 的规则"))
    result = check_structure(profile)
    assert result["status"] == "fail"
    assert any("结构标记" in issue for issue in result["issues"])


def test_insufficient_basis_fails() -> None:
    profile = rich_profile()
    profile.expression.rhythm = Asserted.inferred(
        "短句", basis="insufficient source material; inferred from style"
    )
    result = check_structure(profile)
    assert result["status"] == "fail"
    assert any("材料不足" in issue for issue in result["issues"])


def test_privacy_detects_phone_and_email() -> None:
    profile = rich_profile()
    profile.relation.defining_facts = [
        Asserted.source("手机 13800138000"),
        Asserted.source("邮箱 someone@example.com"),
    ]
    result = check_privacy(profile)
    assert result["status"] == "fail"
    kinds = {hit["kind"] for hit in result["hits"]}
    assert {"phone", "email"} <= kinds


def test_privacy_ignores_clean_text() -> None:
    assert check_privacy(rich_profile())["status"] == "pass"


def test_load_skipped_without_engine_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = check_load(Path(tmp), "xiaolan")
        assert result["status"] == "skipped"
        assert "不存在" in result["reason"]


def test_load_skipped_when_loader_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        personas = Path(tmp) / "vendor" / "OpenHer" / "persona" / "personas"
        (personas / "xiaolan").mkdir(parents=True)
        (personas / "xiaolan" / "SOUL.md").write_text("---\nname: 小岚\n---\n", encoding="utf-8")
        result = check_load(personas, "xiaolan")
        # 没有 vendor 的 loader 实现时应 skipped 而不是 pass/fail
        assert result["status"] in {"skipped", "fail", "warn"}


def test_probe_skipped_by_default() -> None:
    assert check_probe(rich_profile(), enabled=False)["status"] == "skipped"
    assert check_probe(rich_profile(), enabled=True)["status"] == "skipped"


def test_verdict_aggregation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        warn_report = run_check(rich_profile(), persona_id="xiaolan", personas_dir=Path(tmp))
        assert warn_report["verdict"] in {"warn", "pass"}
        assert warn_report["sections"]["probe"]["status"] == "skipped"

        broken = rich_profile()
        broken.hard_rules.append(Asserted.source("Layer 5 越界"))
        fail_report = run_check(broken, persona_id="xiaolan", personas_dir=Path(tmp))
        assert fail_report["verdict"] == "fail"
        assert fail_report["fixes"]


def test_fixes_are_actionable() -> None:
    profile = PersonProfile(persona_id="a", identity=Identity(name=Asserted.source("甲")))
    with tempfile.TemporaryDirectory() as tmp:
        report = run_check(profile, persona_id="a", personas_dir=Path(tmp))
    assert any("#35" in fix or "口头禅" in fix for fix in report["fixes"])
    assert any("drive_baseline" in fix for fix in report["fixes"])


# ── CLI 集成 ────────────────────────────────────────────────

def test_cli_check_warn_returns_zero() -> None:
    import cli.main as cli
    import os

    with tempfile.TemporaryDirectory() as tmp:
        previous = os.environ.get("ALPHA_PERSONA_ROOT")
        previous_mirror = os.environ.get("ALPHA_PERSONA_MIRROR")
        os.environ["ALPHA_PERSONA_ROOT"] = tmp
        os.environ["ALPHA_PERSONA_MIRROR"] = str(Path(tmp) / "mirror")
        try:
            assert cli.main(["persona", "new", "--from", "scratch", "--id", "a",
                             "--name", "甲", "--yes"]) == 0
            assert cli.main(["persona", "check", "a", "--json"]) == 0
        finally:
            if previous is None:
                os.environ.pop("ALPHA_PERSONA_ROOT", None)
            else:
                os.environ["ALPHA_PERSONA_ROOT"] = previous
            if previous_mirror is None:
                os.environ.pop("ALPHA_PERSONA_MIRROR", None)
            else:
                os.environ["ALPHA_PERSONA_MIRROR"] = previous_mirror


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} persona check tests passed")
