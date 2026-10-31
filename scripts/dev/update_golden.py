# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#43 golden 快照生成器。

    python3 scripts/dev/update_golden.py            # 重新生成并写 manifest
    python3 scripts/dev/update_golden.py --check    # 只校验，有漂移则退出码 1

快照目录：`tests/golden/soul/<persona_id>/SOUL.md`，manifest 记 sha256 与体检结论。
**改生成器导致快照变化时，必须在这里显式更新并把 diff 一起提交** —— 快照的用处就是让
"生成逻辑变了"这件事无法悄悄发生。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures" / "profiles"
GOLDEN = ROOT / "tests" / "golden"
SOUL_ROOT = GOLDEN / "soul"
MANIFEST = GOLDEN / "manifest.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict:
    from bridge.consistency import verify_persona

    entries = {}
    for soul in sorted(SOUL_ROOT.glob("*/SOUL.md")):
        persona_id = soul.parent.name
        report = verify_persona(SOUL_ROOT, persona_id)
        entries[persona_id] = {
            "sha256": _sha(soul),
            "bytes": soul.stat().st_size,
            "lines": len(soul.read_text(encoding="utf-8").splitlines()),
            "verify_status": report["status"],
            "sections": next(
                (r["detail"].get("sections") for r in report["results"] if r["name"] == "text"),
                {},
            ),
            "fixture": f"tests/fixtures/profiles/{persona_id}.json",
        }
    return {
        "generator": "alpha profile2soul",
        "command": "python3 -m cli.main profile2soul tests/fixtures/profiles "
                   "--out tests/golden/soul --write",
        "count": len(entries),
        "personas": entries,
    }


def regenerate() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "cli.main", "profile2soul", str(FIXTURES),
         "--out", str(SOUL_ROOT), "--write"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"生成失败:\n{proc.stdout}\n{proc.stderr}")
    manifest = build_manifest()
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return manifest


def compare() -> tuple[bool, list[str]]:
    """不写任何文件，只比对当前磁盘快照与 manifest。"""
    if not MANIFEST.is_file():
        return False, ["manifest.json 不存在（先跑 update_golden.py）"]
    recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))
    problems: list[str] = []

    on_disk = {p.parent.name for p in SOUL_ROOT.glob("*/SOUL.md")}
    recorded_ids = set(recorded.get("personas", {}))
    if on_disk != recorded_ids:
        problems.append(f"快照集合不一致：磁盘 {sorted(on_disk)} vs manifest {sorted(recorded_ids)}")

    for persona_id, entry in recorded.get("personas", {}).items():
        path = SOUL_ROOT / persona_id / "SOUL.md"
        if not path.is_file():
            problems.append(f"{persona_id}: 快照文件缺失")
            continue
        actual = _sha(path)
        if actual != entry["sha256"]:
            problems.append(f"{persona_id}: 哈希漂移 {entry['sha256'][:12]} → {actual[:12]}")
    return not problems, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成/校验 golden 快照")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)

    if args.check:
        ok, problems = compare()
        for line in problems:
            print(f"[golden] {line}", file=sys.stderr)
        print("[golden] 一致" if ok else f"[golden] {len(problems)} 处漂移")
        return 0 if ok else 1

    manifest = regenerate()
    print(f"[golden] 已更新 {manifest['count']} 份快照 → {MANIFEST.relative_to(ROOT)}")
    for persona_id, entry in manifest["personas"].items():
        print(f"  {persona_id:<10}{entry['bytes']:>6} B  {entry['lines']:>3} 行  "
              f"{entry['verify_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
