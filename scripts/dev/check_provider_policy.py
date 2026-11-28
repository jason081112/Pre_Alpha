# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#77 校验：providers.json 里每个 provider 都在训练政策文档里有核对小节。

    python3 scripts/dev/check_provider_policy.py

只保证「不漏一家」，不保证内容真实 —— 真实性靠人工核对与条款归档。
新增 provider 却忘了写核对记录时，CI 会失败。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = ROOT / "shell" / "providers.json"
DOC = ROOT / "docs" / "provider-training-policy.md"

VALID_STATUS = ("待人工核对", "已确认不训练", "无法关闭")


def main() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")

    try:
        providers = list((json.loads(PROVIDERS.read_text(encoding="utf-8")).get("providers") or {}))
    except (OSError, ValueError) as exc:
        print(f"✗ 读不到 provider 注册表：{exc}")
        return 1
    try:
        doc = DOC.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"✗ 读不到 {DOC.relative_to(ROOT)}：{exc}")
        return 1

    check("注册表非空", bool(providers), f"{len(providers)} 个 provider")

    headings = set(re.findall(r"^### ([a-z0-9-]+)\s*$", doc, re.M))
    missing = [p for p in providers if p not in headings]
    check("每个 provider 都有核对小节", not missing, ", ".join(missing))

    # 每节至少要有三个必答项
    sections = re.split(r"^### ", doc, flags=re.M)[1:]
    incomplete = []
    for block in sections:
        name = block.split("\n", 1)[0].strip()
        for field in ("是否用 API 数据训练", "数据保留期", "关闭 / opt-out 方式"):
            if field not in block:
                incomplete.append(f"{name}:{field}")
    check("每节三个必答项齐全", not incomplete, ", ".join(incomplete[:5]))

    statuses = set(re.findall(r"\|\s*(待人工核对|已确认不训练|无法关闭)\s*\|", doc))
    check("状态取值受控", bool(statuses) and statuses <= set(VALID_STATUS), str(sorted(statuses)))

    # 声称「已确认不训练」必须带快照与日期，否则不许写死
    claims = re.findall(r"\|\s*已确认不训练\s*\|\s*([^|]*)\|", doc)
    bad_claims = [c.strip() for c in claims if "待归档" in c or not c.strip()]
    check("「已确认不训练」都带条款快照", not bad_claims, ", ".join(bad_claims))

    check("有新增 provider 上线清单", "新增 provider 上线清单" in doc)
    check("有复核触发条件", "复核触发条件" in doc)

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
