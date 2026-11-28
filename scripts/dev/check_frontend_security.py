#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#82 前端安全基线的静态检查。

零依赖，CI 和本地都能跑：

    python3 scripts/dev/check_frontend_security.py

检查的是「不会因为某次改动悄悄回退」的硬约束，不是替代浏览器里的
XSS / CSP 手工验证。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "shell" / "ui.py"
SW = ROOT / "site" / "static" / "sw.js"
CONSENT = ROOT / "shell" / "consent.py"
WS_STREAM = ROOT / "shell" / "ws_stream.py"
APP = ROOT / "shell" / "app.py"
STORE = ROOT / "shell" / "auth" / "store.py"
PRIVACY_PAGE = ROOT / "site" / "legal" / "privacy.html"

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if ok:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}{' — ' + detail if detail else ''}")
        failures.append(name)


def main() -> int:
    ui = UI.read_text(encoding="utf-8")
    sw = SW.read_text(encoding="utf-8")

    # ── CSP ────────────────────────────────────────────────────
    print("CSP")
    check("禁止 unsafe-inline", "unsafe-inline" not in ui)
    check("禁止 unsafe-eval", "unsafe-eval" not in ui)
    check("启用 Trusted Types 强制", "require-trusted-types-for 'script'" in ui)
    check("Trusted Types 策略白名单", "trusted-types alpha-html alpha-sw" in ui)
    check("connect-src 只放自身", "connect-src 'self'" in ui)
    check("frame-ancestors none", "frame-ancestors 'none'" in ui)

    # ── 危险 DOM/脚本 API ─────────────────────────────────────
    print("DOM 注入面")
    check("无 eval(", not re.search(r"\beval\s*\(", ui))
    check("无 new Function(", "new Function(" not in ui)
    check("无 document.write", "document.write" not in ui)
    check("无 insertAdjacentHTML", "insertAdjacentHTML" not in ui)

    # innerHTML 只允许出现在 setHTML 封装内部（经 Trusted Types 策略）
    bad_inner = [
        (n, line.strip())
        for n, line in enumerate(ui.splitlines(), 1)
        if ".innerHTML" in line and "htmlPolicy" not in line and "等注入点" not in line
    ]
    check("innerHTML 只走 setHTML 封装", not bad_inner, str(bad_inner[:3]))

    # ── Key 不落前端存储 ─────────────────────────────────────
    print("Key 处理")
    storage_key = re.compile(
        r"(?:localStorage|sessionStorage)\.(?:setItem|getItem)\s*\([^)]*"
        r"(?:api_key|apiKey|provider_key|provKey)",
        re.IGNORECASE,
    )
    check("Key 不写 localStorage/sessionStorage", not storage_key.search(ui))
    check("Key 输入框 type=password", 'id="apiKey" type="password"' in ui)
    check("Key 输入框禁自动填充", 'id="apiKey"' in ui and "autocomplete=\"new-password\"" in ui)
    check("Key 不进 URL", "api_key=" not in ui or "api_key=***" in ui)
    check("提交后清空输入框", re.search(r'apiKey"\)\.value = ""', ui) is not None)
    check("错误信息脱敏函数存在", "function redact(" in ui and "Bearer ***" in ui)

    # ── Service Worker ───────────────────────────────────────
    print("Service Worker")
    check("SW 不缓存 API", "/api/" not in sw or "不缓存" in sw)
    check("SW 不缓存 WS", "/ws" not in sw or "不缓存" in sw)

    # ── 隐私同意（#76）───────────────────────────────────────
    print("隐私同意")
    consent_src = CONSENT.read_text(encoding="utf-8")
    page = PRIVACY_PAGE.read_text(encoding="utf-8")
    m_ver = re.search(r'PRIVACY_VERSION\s*=\s*"([^"]+)"', consent_src)
    m_page = re.search(r"版本\s*([0-9]+(?:\.[0-9]+)*)", page)
    check(
        "代码与隐私页版本号一致",
        bool(m_ver and m_page and m_ver.group(1) == m_page.group(1)),
        f"code={m_ver.group(1) if m_ver else '?'} page={m_page.group(1) if m_page else '?'}",
    )
    ws_src = WS_STREAM.read_text(encoding="utf-8")
    check("WS 接受 consent_check", "consent_check" in ws_src)
    check("WS 未同意时拒绝 chat", "consent_required" in ws_src)
    check("app 注入 has_consent", "has_consent" in APP.read_text(encoding="utf-8"))
    check("consent 表存在", "auth_consent" in STORE.read_text(encoding="utf-8"))
    check("前端有同意界面", 'id="consent"' in ui and 'id="consentAgree"' in ui)
    check("前端处理 consent_required", 'consent_required' in ui)

    print(f"\n{checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
