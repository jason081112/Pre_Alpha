# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#85 A 部分：`alpha persona` CLI 的行为测试。

不 spawn 子进程，直接调 `cli.main.main(argv)`，便于捕获退出码与落盘结果。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli.main import main  # noqa: E402

BUILTIN = ROOT / "vendor" / "OpenHer" / "persona" / "personas"


@contextlib.contextmanager
def sandbox():
    """临时 PERSONA_ROOT + 捕获 stdout。"""
    with tempfile.TemporaryDirectory() as tmp:
        previous = os.environ.get("ALPHA_PERSONA_ROOT")
        os.environ["ALPHA_PERSONA_ROOT"] = tmp
        # 不写进 vendor，避免污染工作区
        previous_mirror = os.environ.get("ALPHA_PERSONA_MIRROR")
        os.environ["ALPHA_PERSONA_MIRROR"] = str(Path(tmp) / "mirror" / "personas")
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                yield Path(tmp), buffer
        finally:
            if previous is None:
                os.environ.pop("ALPHA_PERSONA_ROOT", None)
            else:
                os.environ["ALPHA_PERSONA_ROOT"] = previous
            if previous_mirror is None:
                os.environ.pop("ALPHA_PERSONA_MIRROR", None)
            else:
                os.environ["ALPHA_PERSONA_MIRROR"] = previous_mirror


def test_new_dry_run_writes_nothing() -> None:
    with sandbox() as (tmp, out):
        code = main(["persona", "new", "--from", "scratch", "--id", "xiaolan", "--name", "小岚"])
        assert code == 0
        assert "dry-run" in out.getvalue()
        assert not (tmp / "default" / "personas" / "xiaolan").exists()


def test_new_yes_creates_and_lists() -> None:
    with sandbox() as (tmp, out):
        code = main(["persona", "new", "--from", "scratch", "--id", "xiaolan",
                     "--name", "小岚", "--yes"])
        assert code == 0, out.getvalue()
        assert (tmp / "default" / "personas" / "xiaolan" / "profile.json").is_file()
        assert (tmp / "default" / "personas" / "xiaolan" / "SOUL.md").is_file()

        out.truncate(0)
        out.seek(0)
        main(["persona", "list"])
        listing = out.getvalue()
        assert "xiaolan" in listing and "小岚" in listing


def test_new_requires_id() -> None:
    with sandbox() as (tmp, out):
        code = main(["persona", "new", "--from", "scratch", "--name", "小岚"])
        assert code == 2
        assert "--id" in out.getvalue()


def test_gender_missing_warning_is_shown() -> None:
    with sandbox() as (_, out):
        main(["persona", "new", "--from", "scratch", "--id", "a", "--name", "甲"])
        text = out.getvalue()
        assert "female" in text and "回落" in text


def test_edit_sets_field() -> None:
    with sandbox() as (tmp, out):
        main(["persona", "new", "--from", "scratch", "--id", "a", "--name", "甲", "--yes"])
        out.truncate(0)
        out.seek(0)
        code = main(["persona", "edit", "a", "--set", "identity.mbti=INTJ"])
        assert code == 0 and "dry-run" in out.getvalue()

        out.truncate(0)
        out.seek(0)
        code = main(["persona", "edit", "a", "--set", "identity.mbti=INTJ", "--yes"])
        assert code == 0 and "v2" in out.getvalue()

        profile = json.loads((tmp / "default" / "personas" / "a" / "profile.json").read_text(encoding="utf-8"))
        assert profile["identity"]["mbti"]["value"] == "INTJ"


def test_edit_rejects_unknown_field() -> None:
    with sandbox() as (_, out):
        main(["persona", "new", "--from", "scratch", "--id", "a", "--name", "甲", "--yes"])
        out.truncate(0)
        out.seek(0)
        code = main(["persona", "edit", "a", "--set", "engine.drive_baseline.play=0.9"])
        assert code == 2
        assert "不支持修改" in out.getvalue()


def test_edit_rejects_bad_int() -> None:
    with sandbox() as (_, out):
        main(["persona", "new", "--from", "scratch", "--id", "a", "--name", "甲", "--yes"])
        out.truncate(0)
        out.seek(0)
        code = main(["persona", "edit", "a", "--set", "identity.age=三十"])
        assert code == 2
        assert "整数" in out.getvalue()


def test_preview_prints_soul() -> None:
    with sandbox() as (_, out):
        main(["persona", "new", "--from", "scratch", "--id", "a", "--name", "甲", "--yes"])
        out.truncate(0)
        out.seek(0)
        code = main(["persona", "preview", "a"])
        assert code == 0
        text = out.getvalue()
        # 已存在 SOUL.md 时输出 diff（无改动则为「无差异」），否则输出全文
        assert ("无差异" in text) or ("name:" in text) or ("SOUL" in text)


def test_delete_requires_yes() -> None:
    with sandbox() as (tmp, out):
        main(["persona", "new", "--from", "scratch", "--id", "a", "--name", "甲", "--yes"])
        out.truncate(0)
        out.seek(0)
        assert main(["persona", "delete", "a"]) == 0
        assert "dry-run" in out.getvalue()
        assert main(["persona", "list"]) == 0
        assert (tmp / "default" / "personas" / "a").exists()

        out.truncate(0)
        out.seek(0)
        assert main(["persona", "delete", "a", "--yes"]) == 0
        out.truncate(0)
        out.seek(0)
        main(["persona", "list"])
        assert "没有可编辑的人格" in out.getvalue()


def test_builtin_import_when_vendor_present() -> None:
    if not (BUILTIN / "luna" / "SOUL.md").is_file():
        print("SKIP: vendor/OpenHer 未就绪")
        return
    with sandbox() as (tmp, out):
        code = main(["persona", "new", "--from", "builtin:luna", "--id", "luna-copy",
                     "--name", "露娜", "--yes"])
        assert code == 0, out.getvalue()
        profile = json.loads(
            (tmp / "default" / "personas" / "luna-copy" / "profile.json").read_text(encoding="utf-8")
        )
        assert profile["identity"]["name"]["value"] == "露娜"
        assert profile["identity"]["name_zh"]["value"] == "陆暖"
        assert profile["identity"]["age"]["value"] == 22
        # 关系没有来源，必须标 missing 而不是编
        assert profile["relation"]["label"]["provenance"] == "missing"


def test_builtin_unknown_id_is_reported() -> None:
    if not BUILTIN.is_dir():
        print("SKIP: vendor/OpenHer 未就绪")
        return
    with sandbox() as (_, out):
        code = main(["persona", "new", "--from", "builtin:nope", "--id", "x", "--name", "X"])
        assert code == 2
        assert "找不到内置人格" in out.getvalue()


def test_empty_list_hint() -> None:
    with sandbox() as (_, out):
        assert main(["persona", "list"]) == 0
        assert "没有可编辑的人格" in out.getvalue()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} cli persona tests passed")
