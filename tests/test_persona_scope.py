# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#99 单人格收窄的零依赖测试。

    python3 tests/test_persona_scope.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shell.persona_scope import (  # noqa: E402
    DEFAULT_PERSONA,
    ENV_ALLOWLIST,
    allowlist,
    filter_personas,
    parse_allowlist,
    resolve_persona,
)

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}{' — ' + detail if detail else ''}")


ALL = ["ember", "iris", "kai", "kelly", "luna", "mia", "nova", "rex", "sora", "vivian", "xiaolan"]

# ── parse_allowlist ──────────────────────────────────────────────
check("默认人格是 xiaolan", DEFAULT_PERSONA == "xiaolan", DEFAULT_PERSONA)
check("未设置白名单 → None", parse_allowlist(None) is None)
check("空字符串 → None", parse_allowlist("") is None)
check("只有分隔符 → None", parse_allowlist(" , ; ") is None)
check("单个 ID 去空格", parse_allowlist(" xiaolan ") == ("xiaolan",))
check("逗号分隔", parse_allowlist("xiaolan,luna") == ("xiaolan", "luna"))
check("分号/空格也认", parse_allowlist("xiaolan; luna") == ("xiaolan", "luna"))

# ── allowlist（读环境字典） ──────────────────────────────────────
check("环境没有变量 → None", allowlist({}) is None)
check("环境有变量 → 元组", allowlist({ENV_ALLOWLIST: "xiaolan"}) == ("xiaolan",))

# ── filter_personas ─────────────────────────────────────────────
check("不限制时保留全部", filter_personas(ALL, None) == ALL)
check("收窄到 xiaolan", filter_personas(ALL, ("xiaolan",)) == ["xiaolan"])
check("收窄到两个且保持原顺序",
      filter_personas(ALL, ("luna", "xiaolan")) == ["luna", "xiaolan"])
check("白名单无交集时保留原列表（避免配置打错直接 500）",
      filter_personas(ALL, ("nobody",)) == ALL)
check("返回新列表不共享引用", filter_personas(ALL, None) is not ALL)

# ── resolve_persona ─────────────────────────────────────────────
check("不限制时原样返回", resolve_persona("luna", None) == "luna")
check("白名单内原样返回", resolve_persona("xiaolan", ("xiaolan",)) == "xiaolan")
check("白名单外回落到 xiaolan", resolve_persona("luna", ("xiaolan",)) == "xiaolan")
check("默认人格不在白名单 → 取第一个",
      resolve_persona("luna", ("kai", "mia")) == "kai")
check("空请求 + 白名单 → 默认人格",
      resolve_persona("", ("xiaolan",)) == "xiaolan")
check("空请求 + 不限制 → 空（调用方按原逻辑拒绝）",
      resolve_persona("", None) == "")

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(1 if FAIL else 0)
