# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#76 隐私同意记录的零依赖测试。

    python3 tests/test_consent.py

只测存储与门禁，不启动 HTTP（端点由 CI 的 fastapi 环境覆盖）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.auth.store import AuthStore  # noqa: E402
from shell.consent import (  # noqa: E402
    CONSENT_KIND,
    PRIVACY_VERSION,
    has_consent,
)


def main() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")

    with tempfile.TemporaryDirectory() as tmp:
        store = AuthStore(Path(tmp) / "auth.db")

        check("没有记录时未同意", not has_consent(store, "u1"))
        check("开发模式（local）直接放行", has_consent(store, "local"))

        store.record_consent("u1", CONSENT_KIND, PRIVACY_VERSION, now=1000.0)
        rec = store.get_consent("u1", CONSENT_KIND)
        check("记录版本与时间", bool(rec) and rec["version"] == PRIVACY_VERSION and rec["agreed_at"] == 1000.0,
              str(rec))
        check("同意后门禁放行", has_consent(store, "u1"))

        # 旧版本记录不算数
        store.record_consent("u2", CONSENT_KIND, "1.0", now=900.0)
        check("旧版本记录不放行", not has_consent(store, "u2"))

        # 重复同意刷新时间，不产生重复行
        store.record_consent("u1", CONSENT_KIND, PRIVACY_VERSION, now=2000.0)
        history = store.consent_history("u1", CONSENT_KIND)
        check("重复同意刷新时间且只有一条", len(history) == 1 and history[0]["agreed_at"] == 2000.0,
              str(history))

        # 版本升级后可追溯多条
        store.record_consent("u1", CONSENT_KIND, "1.3", now=3000.0)
        history = store.consent_history("u1", CONSENT_KIND)
        check("换版本后保留历史（可审计）", len(history) == 2 and history[0]["version"] == "1.3",
              str(history))
        check("最新版本不是当前版本时仍要求重新同意", not has_consent(store, "u1"))

        # 用户之间隔离
        check("用户之间隔离", store.get_consent("u3", CONSENT_KIND) is None)

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
