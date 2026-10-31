# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#39 纠错层：运行期可写约束。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.model import Correction, profile_from_dict  # noqa: E402
from shell.corrections import (  # noqa: E402
    MAX_SECTION_ITEMS,
    CorrectionError,
    CorrectionStore,
    validate_correction,
)

PROFILE = ROOT / "tests" / "fixtures" / "profiles" / "xiaolan.json"


def _store() -> CorrectionStore:
    return CorrectionStore(":memory:")


def _cli(db: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, ALPHA_CORRECTIONS_DB=str(db))
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )


# ---------------------------------------------------------------- 校验


def test_validate_accepts_normal_entry() -> None:
    assert validate_correction("对方道歉", "没关系", "嗯，先这样。") == []


def test_validate_rejects_empty() -> None:
    errors = validate_correction("", "", "")
    assert any("wrong" in e for e in errors)
    assert any("correct" in e for e in errors)


def test_validate_rejects_identical() -> None:
    assert any("相同" in e for e in validate_correction("", "算了", "算了"))


def test_validate_rejects_too_long() -> None:
    long_text = "很长的句子" * 60
    assert any("过长" in e for e in validate_correction("", long_text, "好"))


def test_validate_rejects_action_markers() -> None:
    for text in ("（叹气）算了", "*低头*算了", "【旁白】算了", "[thought]算了"):
        errors = validate_correction("", text, "好")
        assert any("动作" in e for e in errors), text


def test_validate_rejects_privacy() -> None:
    assert any("隐私" in e for e in validate_correction("", "打 13812345678", "好"))
    assert any("隐私" in e for e in validate_correction("", "发 a@b.com", "好"))


# ---------------------------------------------------------------- 写读


def test_add_and_list() -> None:
    store = _store()
    record = store.add("xiaolan", "没关系", "嗯，先这样。", scene="对方道歉")
    assert record.active is True and record.source == "runtime"
    listed = store.list("xiaolan")
    assert len(listed) == 1 and listed[0].wrong == "没关系"


def test_repeated_correction_overwrites_instead_of_accumulating() -> None:
    store = _store()
    store.add("xiaolan", "没关系", "嗯，先这样。", scene="对方道歉")
    store.add("xiaolan", "没关系", "知道了。", scene="对方道歉")
    listed = store.list("xiaolan")
    assert len(listed) == 1
    assert listed[0].correct == "知道了。"


def test_disable_and_enable() -> None:
    store = _store()
    record = store.add("xiaolan", "没关系", "嗯。")
    store.set_active(record.id, False)
    assert store.list("xiaolan") == []
    assert len(store.list("xiaolan", include_inactive=True)) == 1
    store.set_active(record.id, True)
    assert len(store.list("xiaolan")) == 1


def test_disable_missing_raises() -> None:
    store = _store()
    try:
        store.set_active(999, False)
    except CorrectionError as exc:
        assert "不存在" in str(exc)
    else:
        raise AssertionError("应抛 CorrectionError")


def test_delete() -> None:
    store = _store()
    record = store.add("xiaolan", "没关系", "嗯。")
    assert store.delete(record.id) is True
    assert store.delete(record.id) is False


def test_personas_are_isolated() -> None:
    store = _store()
    store.add("xiaolan", "a", "b")
    store.add("linzhou", "c", "d")
    assert [x.wrong for x in store.list("xiaolan")] == ["a"]
    assert [x.wrong for x in store.list("linzhou")] == ["c"]


def test_users_are_isolated() -> None:
    store = _store()
    store.add("xiaolan", "a", "b", user_id="alice")
    assert store.list("xiaolan", user_id="bob") == []


def test_stats() -> None:
    store = _store()
    store.add("xiaolan", "a", "b")
    second = store.add("xiaolan", "c", "d")
    store.set_active(second.id, False)
    stats = store.stats("xiaolan")
    assert stats == {"total": 2, "active": 1, "runtime": 2}


# ---------------------------------------------------------------- 静态导入


def test_seed_from_profile_is_idempotent() -> None:
    store = _store()
    profile = profile_from_dict(json.loads(PROFILE.read_text(encoding="utf-8")))
    first = store.seed_from_profile("xiaolan", profile.corrections)
    second = store.seed_from_profile("xiaolan", profile.corrections)
    assert first == len(profile.corrections)
    assert second == 0
    assert store.stats("xiaolan")["total"] == len(profile.corrections)


def test_seed_does_not_override_runtime_edit() -> None:
    store = _store()
    profile = profile_from_dict(json.loads(PROFILE.read_text(encoding="utf-8")))
    item = profile.corrections[0]
    store.add("xiaolan", item.wrong, "我自己的说法", scene=item.scene)
    store.seed_from_profile("xiaolan", profile.corrections)
    listed = store.list("xiaolan")
    assert len(listed) == 1
    assert listed[0].correct == "我自己的说法"


def test_seed_skips_empty_entries() -> None:
    store = _store()
    added = store.seed_from_profile("p", [Correction("s", "", ""), Correction("s", "a", "b")])
    assert added == 1


# ---------------------------------------------------------------- 注入文本


def test_render_empty_section_is_empty_string() -> None:
    assert _store().render_section("xiaolan") == ""


def test_render_section_format() -> None:
    store = _store()
    store.add("xiaolan", "没关系", "嗯，先这样。", scene="对方道歉")
    text = store.render_section("xiaolan")
    assert text.startswith("【纠错规则】")
    assert "不要说「没关系」" in text
    assert "改说「嗯，先这样。」" in text
    assert "[对方道歉]" in text


def test_render_section_english() -> None:
    store = _store()
    store.add("aria", "I am fine", "Depends.", scene="asked how she is")
    text = store.render_section("aria", lang="en")
    assert "Corrections" in text
    assert "I am fine" in text


def test_render_section_respects_limit() -> None:
    store = _store()
    for index in range(MAX_SECTION_ITEMS + 4):
        store.add("p", f"wrong{index}", f"right{index}")
    lines = store.render_section("p").splitlines()
    assert len(lines) == 1 + MAX_SECTION_ITEMS


def test_runtime_entries_rank_before_distilled() -> None:
    store = _store()
    store.seed_from_profile("p", [Correction("s", "静态", "静态改")])
    store.add("p", "运行期", "运行期改")
    assert store.list("p")[0].source == "runtime"


def test_render_section_has_no_shell_markers() -> None:
    store = _store()
    store.add("p", "算了", "先这样。")
    text = store.render_section("p")
    assert "【内心独白】" not in text and "【最终回复】" not in text


def test_corrections_never_touch_soul_md(tmp_path: Path) -> None:
    """纠错层不能写 SOUL.md —— 否则 golden 快照每次用户纠正都漂移。"""
    soul = ROOT / "tests" / "golden" / "soul" / "xiaolan" / "SOUL.md"
    before = soul.read_bytes()
    store = CorrectionStore(tmp_path / "c.db")
    store.add("xiaolan", "没关系", "嗯，先这样。")
    store.render_section("xiaolan")
    assert soul.read_bytes() == before


# ---------------------------------------------------------------- CLI


def test_cli_full_cycle(tmp_path: Path) -> None:
    db = tmp_path / "corr.db"
    added = _cli(db, "persona", "corrections", "xiaolan", "add",
                 "--scene", "对方道歉", "--wrong", "没关系", "--correct", "嗯，先这样。")
    assert added.returncode == 0, added.stderr

    listed = _cli(db, "persona", "corrections", "xiaolan", "list")
    assert "没关系" in listed.stdout and "1 条生效" in listed.stdout

    section = _cli(db, "persona", "corrections", "xiaolan", "section")
    assert "【纠错规则】" in section.stdout

    disabled = _cli(db, "persona", "corrections", "xiaolan", "disable", "--id", "1")
    assert disabled.returncode == 0
    empty = _cli(db, "persona", "corrections", "xiaolan", "section")
    assert empty.stdout.strip() == "（无生效条目，不产生注入段）"


def test_cli_rejects_invalid_entry(tmp_path: Path) -> None:
    proc = _cli(tmp_path / "c.db", "persona", "corrections", "xiaolan", "add",
                "--wrong", "（叹气）算了", "--correct", "好")
    assert proc.returncode == 2
    assert "动作" in proc.stderr


def test_cli_remove_requires_confirmation(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    _cli(db, "persona", "corrections", "xiaolan", "add", "--wrong", "a", "--correct", "b")
    dry = _cli(db, "persona", "corrections", "xiaolan", "remove", "--id", "1")
    assert dry.returncode == 0 and "--yes" in dry.stdout
    real = _cli(db, "persona", "corrections", "xiaolan", "remove", "--id", "1", "--yes")
    assert "已删除" in real.stdout


def test_cli_seed_from_profile(tmp_path: Path) -> None:
    proc = _cli(tmp_path / "c.db", "persona", "corrections", "xiaolan",
                "seed", "--file", str(PROFILE))
    assert proc.returncode == 0, proc.stderr
    assert "导入 1 条" in proc.stdout


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        if "tmp_path" in test.__code__.co_varnames[:test.__code__.co_argcount]:
            with tempfile.TemporaryDirectory() as tmp:
                test(Path(tmp))
        else:
            test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} correction tests passed")
