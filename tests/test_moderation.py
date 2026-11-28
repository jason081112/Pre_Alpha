# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#100 违规处置（举报 / 封禁 / 审计 / 额度清零）的零依赖测试。

    python3 tests/test_moderation.py

只测存储与处置语义，不启动 HTTP（端点由 CI 的 fastapi 环境覆盖）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.limits import LimitsService  # noqa: E402
from shell.moderation import REPORT_CATEGORIES, ModerationStore  # noqa: E402


def main() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")

    with tempfile.TemporaryDirectory() as tmp:
        store = ModerationStore(Path(tmp) / "moderation.db")
        limits = LimitsService(Path(tmp) / "limits.db")

        # ── 举报 ────────────────────────────────────────────────
        rid = store.add_report("u1", "self_harm", detail="他说了鼓励自伤的话", target="msg-7")
        rows = store.reports()
        check("举报入库", len(rows) == 1 and rows[0]["id"] == rid)
        check("举报默认 open", rows[0]["status"] == "open" and rows[0]["category"] == "self_harm")
        check("举报保留上下文", rows[0]["detail"].startswith("他说了") and rows[0]["target"] == "msg-7")

        unknown = store.add_report("u1", "not-a-category")
        check("未知分类归到 other",
              store.reports()[0]["id"] == unknown and store.reports()[0]["category"] == "other")
        check("分类白名单包含 AUP 六类",
              {"sexual", "violence", "hate", "csam", "impersonation", "copyright"} <= set(REPORT_CATEGORIES))

        check("状态流转", store.set_report_status(rid, "resolved") and store.reports(status="resolved")[0]["id"] == rid)
        check("未知举报 id 返回 False", not store.set_report_status(99999, "resolved"))
        check("按状态过滤", store.reports(status="open")[0]["id"] == unknown)

        # ── 封禁 / 解封 ──────────────────────────────────────────
        check("未封禁用户放行", not store.is_banned("u1"))
        check("空用户 / local 放行（开发模式）", not store.is_banned("") and not store.is_banned("local") and not store.is_banned(None))

        store.ban("u1", reason="鼓励自伤", actor="admin:root")
        check("封禁生效", store.is_banned("u1"))
        store.ban("u1", reason="二次确认", actor="admin:root")
        check("重复封禁不产生第二行",
              len(store._conn.execute("SELECT 1 FROM moderation_ban WHERE user_id='u1'").fetchall()) == 1)
        check("解封生效", store.unban("u1", reason="申诉通过") == store.actions(user_id="u1")[0]["id"])
        check("解封后放行", not store.is_banned("u1"))

        # ── 额度清零 ────────────────────────────────────────────
        limits.usage.record(user_id="u1", provider="dashscope", model="qwen-plus-latest",
                            kind="platform", usage={"total_tokens": 1200})
        limits.usage.record(user_id="u2", provider="dashscope", model="qwen-plus-latest",
                            kind="platform", usage={"total_tokens": 300})
        check("清零前有用量", limits.usage.daily_tokens("u1") == 1200)
        cleared = limits.reset_user_usage("u1")
        check("清零只删目标用户", cleared == 1 and limits.usage.daily_tokens("u1") == 0)
        check("其他用户用量不受影响", limits.usage.daily_tokens("u2") == 300)

        # ── 审计 ────────────────────────────────────────────────
        store.record_action("u1", "guard_block", reason="self_harm")
        acts = store.actions(user_id="u1")
        kinds = [a["action"] for a in acts]
        check("处置审计含 ban/unban", "ban" in kinds and "unban" in kinds, str(kinds))
        check("护栏命中可审计", "guard_block" in kinds)
        check("审计带操作人", any(a["actor"] == "admin:root" for a in acts))

        store.close()
        limits.close()

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
