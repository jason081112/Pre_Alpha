# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#41 一致性校验：产物 ↔ 引擎契约。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.consistency import (  # noqa: E402
    check_load_contract,
    check_register_contract,
    check_text_contract,
    split_frontmatter,
    to_store_profile,
    verify_dir,
    verify_persona,
)

FIXTURE = ROOT / "tests" / "fixtures" / "profiles" / "xiaolan.json"
GOOD = """---
name: 小岚
lang: zh
gender: female
age: 26
---

## 性格

- 说话算数

## 说话风格

- 短句

## 背景故事

- 恋人

## 行为规则

- 不主动提分手
"""


def _build_soul_dir(tmp: str) -> Path:
    root = Path(tmp) / "soul"
    root.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "cli.main", "profile2soul", str(FIXTURE.parent),
         "--out", str(root), "--write"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    (root / "xiaolan" / "profile.json").write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return root


# ---------------------------------------------------------------- 文本契约


def test_text_contract_pass() -> None:
    result = check_text_contract(GOOD, persona_id="p")
    assert result.status == "pass", result.reasons
    assert all(result.detail["sections"].values())


def test_text_contract_missing_frontmatter() -> None:
    result = check_text_contract("## 性格\n\n- 好\n")
    assert result.status == "fail"
    assert any("frontmatter" in r for r in result.reasons)


def test_text_contract_layer_marker_warns() -> None:
    text = GOOD.replace("## 性格", "## Layer 0 性格")
    result = check_text_contract(text)
    assert any("Layer" in r for r in result.reasons)


def test_text_contract_shell_marker_warns() -> None:
    result = check_text_contract(GOOD + "\n【内心独白】\n")
    assert any("空壳标记" in r for r in result.reasons)


def test_text_contract_three_core_empty_fails() -> None:
    text = """---
name: 甲
lang: zh
---

## 行为规则

- 只有这一段
"""
    result = check_text_contract(text)
    assert result.status == "fail"
    assert any("核心段全空" in r for r in result.reasons)


def test_text_contract_one_section_empty_warns() -> None:
    text = GOOD.replace("## 说话风格\n\n- 短句\n", "## 说话风格\n")
    result = check_text_contract(text)
    assert result.status == "warn"
    assert any("说话风格" in r for r in result.reasons)


def test_text_contract_bad_gender_and_age() -> None:
    text = GOOD.replace("gender: female", "gender: 女").replace("age: 26", "age: 二十六")
    result = check_text_contract(text)
    reasons = " ".join(result.reasons)
    assert "gender" in reasons and "age" in reasons


def test_text_contract_bad_lang() -> None:
    result = check_text_contract(GOOD.replace("lang: zh", "lang: jp"))
    assert any("lang" in r for r in result.reasons)


def test_english_titles_recognized() -> None:
    text = """---
name: A
lang: en
---

## Personality

- honest

## Speaking Style

- short

## Background

- colleague
"""
    result = check_text_contract(text)
    assert result.detail["sections"]["Personality"] is True
    assert result.status == "warn"  # behavioral_rules 缺失不是 fail


def test_split_frontmatter_handles_no_header() -> None:
    fields, body = split_frontmatter("## 性格\n\n- x\n")
    assert fields == {}
    assert "性格" in body


# ---------------------------------------------------------------- 加载 / 注册


def test_load_contract_on_generated_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _build_soul_dir(tmp)
        result = check_load_contract(root, "xiaolan")
        assert result.status in ("pass", "skipped"), result.reasons
        if result.status == "skipped":
            assert "无法导入" in result.reasons[0]
        else:
            assert all(result.detail["sections"].values())


def test_load_contract_missing_persona() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = check_load_contract(Path(tmp), "nobody")
        assert result.status in ("fail", "skipped")


def test_register_contract_roundtrip() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = check_register_contract(data, soul_text="")
    assert result.status in ("pass", "skipped"), result.reasons
    if result.status != "skipped":
        assert result.detail["roundtrip"] is True


def test_to_store_profile_mapping() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mapped = to_store_profile(data)
    assert mapped["id"] == "xiaolan"
    assert mapped["name"] == "小岚"
    assert mapped["gender"] == "female"
    assert isinstance(mapped["age"], int)


def test_register_contract_rejects_empty_text() -> None:
    data = {"persona_id": "bare", "identity": {"name": {"kind": "asserted", "value": "甲"},
                                               "lang": "zh"}}
    result = check_register_contract(data, soul_text="")
    if result.status != "skipped":
        assert result.status in ("warn", "fail")


# ---------------------------------------------------------------- 汇总


def test_verify_persona_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = verify_persona(Path(tmp), "nope")
        assert report["status"] == "fail"


def test_verify_dir_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _build_soul_dir(tmp)
        report = verify_dir(root)
        assert report["count"] == 1
        assert report["status"] in ("pass", "warn", "skipped")
        names = {r["name"] for r in report["personas"][0]["results"]}
        assert names == {"text", "load", "register"}


def test_verify_dir_empty_is_fail() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert verify_dir(Path(tmp))["status"] == "fail"


def test_skipped_never_reported_as_pass() -> None:
    """vendor 缺失时必须报 skipped 而不是 pass —— 不能把没跑过的检查说成通过。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = _build_soul_dir(tmp)
        result = check_load_contract(Path("/nonexistent/vendor"), "xiaolan")
        assert result.status in ("fail", "skipped")
        assert result.status != "pass"


def test_cli_verify_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _build_soul_dir(tmp)
        proc = subprocess.run(
            [sys.executable, "-m", "cli.main", "persona", "verify", str(root), "--json"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert proc.returncode in (0, 1), proc.stderr
        assert "xiaolan" in proc.stdout
        payload = proc.stdout[proc.stdout.index("{"):]
        report = json.loads(payload)
        assert report["count"] == 1


def test_cli_verify_check_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "soul" / "bad"
        root.mkdir(parents=True)
        (root / "SOUL.md").write_text("## 性格\n\n- 只有段落没有 frontmatter\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "cli.main", "persona", "verify", str(root.parent), "--check"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert proc.returncode == 1


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} consistency tests passed")
