# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#43 golden 快照回归：3 份 fixture → SOUL.md 逐字节锁定。

快照的意义：生成逻辑一旦变化（模板、分隔符、字段顺序、段落标题），这里立刻红。
改生成器时必须显式跑 `python3 scripts/dev/update_golden.py` 并把 diff 一起提交。

注：issue 标题里的 SHELL 部分已作废 —— #40 复核确认 `SHELL.md` 不进对话 prompt
（只作 TTS voice description），TTS 已搁置，故不存在 SHELL 快照。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.consistency import verify_persona  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
SOUL_ROOT = GOLDEN / "soul"
MANIFEST = GOLDEN / "manifest.json"
FIXTURES = ROOT / "tests" / "fixtures" / "profiles"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_exists_and_counts_match() -> None:
    data = _manifest()
    on_disk = {p.parent.name for p in SOUL_ROOT.glob("*/SOUL.md")}
    assert set(data["personas"]) == on_disk
    assert data["count"] == len(on_disk)


def test_fixture_set_matches_snapshot_set() -> None:
    fixtures = {p.stem for p in FIXTURES.glob("*.json")}
    assert fixtures == set(_manifest()["personas"])


def test_regeneration_is_byte_identical() -> None:
    """核心回归：现在重新生成，必须和入库快照一模一样。"""
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [sys.executable, "-m", "cli.main", "profile2soul", str(FIXTURES),
             "--out", str(Path(tmp) / "soul"), "--write"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr

        fresh = sorted(Path(tmp).rglob("*/SOUL.md"))
        assert fresh, "未生成任何 SOUL.md"
        for path in fresh:
            persona_id = path.parent.name
            golden = SOUL_ROOT / persona_id / "SOUL.md"
            assert golden.is_file(), f"{persona_id}: 快照缺失"
            assert path.read_text(encoding="utf-8") == golden.read_text(encoding="utf-8"), (
                f"{persona_id}: 生成结果与快照不一致 —— 若是有意的模板改动，"
                f"请跑 scripts/dev/update_golden.py 并提交 diff"
            )


def test_manifest_hashes_match_disk() -> None:
    import hashlib

    for persona_id, entry in _manifest()["personas"].items():
        path = SOUL_ROOT / persona_id / "SOUL.md"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == entry["sha256"], f"{persona_id}: 哈希漂移"
        assert path.stat().st_size == entry["bytes"]
        assert len(path.read_text(encoding="utf-8").splitlines()) == entry["lines"]


def test_each_snapshot_passes_its_recorded_verify_status() -> None:
    for persona_id, entry in _manifest()["personas"].items():
        report = verify_persona(SOUL_ROOT, persona_id)
        assert report["status"] == entry["verify_status"], (
            f"{persona_id}: 体检结论变化 {entry['verify_status']} → {report['status']}"
        )


def test_snapshots_cover_pass_and_warn_paths() -> None:
    statuses = {e["verify_status"] for e in _manifest()["personas"].values()}
    assert "pass" in statuses, "缺少正常路径的样本"
    assert "warn" in statuses, "缺少有缺口路径的样本（aria 故意缺 logic）"


def test_aria_warn_is_locked() -> None:
    """aria 缺 logic → 无 Personality 段，这条退化路径必须被快照锁住。"""
    text = (SOUL_ROOT / "aria" / "SOUL.md").read_text(encoding="utf-8")
    assert "## Personality" not in text
    assert "## Speaking Style" in text
    report = verify_persona(SOUL_ROOT, "aria")
    assert report["status"] == "warn"


def test_all_snapshots_use_loader_titles_only() -> None:
    import re

    allowed = {"性格", "说话风格", "背景故事", "行为规则",
               "Personality", "Speaking Style", "Background", "Behavioral Rules"}
    for path in SOUL_ROOT.glob("*/SOUL.md"):
        headings = set(re.findall(r"^## (.+)$", path.read_text(encoding="utf-8"), flags=re.M))
        assert headings <= allowed, f"{path.parent.name}: 出现非法标题 {headings - allowed}"


def test_no_layer_or_shell_markers() -> None:
    import re

    for path in SOUL_ROOT.glob("*/SOUL.md"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bLayer\s*\d", text), f"{path.parent.name}: 含 Layer N"
        assert "【内心独白】" not in text
        assert "【最终回复】" not in text


def test_no_shell_md_generated() -> None:
    """SHELL.md 不进 prompt（#40 已 not planned），不应出现在产物里。"""
    assert not list(GOLDEN.rglob("SHELL.md"))


def test_update_script_check_mode_passes() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/dev/update_golden.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} golden tests passed")
