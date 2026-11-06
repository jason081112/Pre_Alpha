#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#46 验收：聊天页可由浏览器加载，且 CSP / 端点契约成立。

浏览器渲染本身测不了（沙箱无 Chromium），这里验证「页面能安全地跑起来」的必要条件：
- 首页由 shell 接管（上游演示前端已摘）
- CSP 带 nonce，HTML 里的 nonce 与之匹配，且无内联事件属性
- 页面依赖的四个端点都通：登录、人格、历史、WS
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS: list[dict] = []


def record(name: str, ok: bool | None, detail: str = "") -> None:
    RESULTS.append({"check": name, "ok": ok, "detail": detail})
    mark = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_app(tmp: Path) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = {
        **os.environ,
        "OPENAI_API_KEY": "sk-dummy-for-startup",
        "ALPHA_DATA_DIR": str(tmp),
        "ALPHA_ROUTING_DB": str(tmp / "routing.db"),
        "ALPHA_AUTH_DB": str(tmp / "auth.db"),
        "ALPHA_LIMITS_DB": str(tmp / "limits.db"),
        "ALPHA_AGENT_EXTRA_DB": str(tmp / "agent.db"),
        "ALPHA_PERSONA_ROOT": str(tmp / "users"),
        "ALPHA_REQUIRE_INVITE": "0",
        "ALPHA_PORT": str(port),
        "ALPHA_HOST": "127.0.0.1",
        "PYTHONUNBUFFERED": "1",
    }
    env.pop("ALPHA_AUTH_DISABLED", None)
    log = (tmp / "server.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "shell.app"],
        cwd=str(ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"启动失败，见 {tmp / 'server.log'}")
        try:
            if httpx.get(f"{base}/api/auth/config", timeout=1.0).status_code == 200:
                return proc, base
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    raise RuntimeError("90 秒未就绪")


def check_page(base: str) -> tuple[str, str]:
    r = httpx.get(f"{base}/", timeout=10.0)
    record("1 首页 200", r.status_code == 200, f"status={r.status_code}")
    ctype = r.headers.get("content-type", "")
    record("2 返回 HTML", "text/html" in ctype, ctype)
    html = r.text

    csp = r.headers.get("content-security-policy", "")
    record("3 有 CSP", bool(csp), csp[:60] + ("…" if len(csp) > 60 else ""))

    m = re.search(r"script-src 'nonce-([^']+)'", csp)
    nonce = m.group(1) if m else ""
    record("4 CSP 含 script nonce", bool(nonce))
    used = re.findall(r'<script nonce="([^"]+)"', html) + re.findall(
        r'<style nonce="([^"]+)"', html
    )
    record(
        "5 HTML nonce 与 header 一致",
        bool(used) and all(u == nonce for u in used),
        f"出现 {len(used)} 处",
    )
    record(
        "6 无未替换占位符",
        "__CSP_NONCE__" not in html,
        "有残留" if "__CSP_NONCE__" in html else "",
    )

    inline_handlers = re.findall(
        r"\s(on(?:click|load|change|submit|input|error)\s*=)", html, re.I
    )
    record("7 无内联事件属性", not inline_handlers, f"{inline_handlers[:3]}")

    for el in ("id=\"login\"", "id=\"log\"", "id=\"text\"", "id=\"send\""):
        record(f"8 DOM 含 {el}", el in html)

    record("9 页面连 /ws/chat/stream", "/ws/chat/stream" in html)
    record("10 页面用 /api/chat/history", "/api/chat/history/" in html)
    return html, nonce


def check_endpoints(base: str) -> None:
    email = f"ui{int(time.time() * 1000)}@x.test"
    r = httpx.post(
        f"{base}/api/auth/register",
        json={"email": email, "password": "pa55word-123"},
        timeout=20.0,
    )
    token = r.json().get("access_token", "") if r.status_code == 201 else ""
    record("11 注册可用（页面登录链路）", bool(token), f"status={r.status_code}")

    if token:
        h = {"Authorization": f"Bearer {token}"}
        r = httpx.get(f"{base}/v1/models", headers=h, timeout=10.0)
        ids = [m["id"] for m in (r.json().get("data") or [])] if r.status_code == 200 else []
        record("12 /v1/models 返回人格", len(ids) >= 1, f"{ids[:4]}")

        r = httpx.get(
            f"{base}/api/chat/history/luna?client_id=abc&limit=10",
            headers=h,
            timeout=10.0,
        )
        record(
            "13 历史端点可用",
            r.status_code == 200 and "messages" in r.json(),
            f"status={r.status_code}",
        )

        import websockets

        async def ws_ok() -> int:
            url = base.replace("http://", "ws://") + f"/ws/chat/stream?token={token}"
            try:
                async with websockets.connect(url) as ws:
                    await ws.send(json.dumps({"type": "ping"}))
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    return 1000 if json.loads(raw).get("type") == "pong" else -2
            except Exception:  # noqa: BLE001
                return -1

        record("14 WS 可连（ping/pong）", asyncio.run(ws_ok()) == 1000)


def main() -> int:
    tmp = Path(f"/tmp/verify46-{int(time.time())}")
    tmp.mkdir(parents=True, exist_ok=True)
    proc, base = start_app(tmp)
    print(f"临时目录: {tmp}\n被测服务: {base}\n")
    try:
        check_page(base)
        check_endpoints(base)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    passed = sum(1 for r in RESULTS if r["ok"] is True)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    Path("/tmp/verify-46.json").write_text(
        json.dumps({"results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
