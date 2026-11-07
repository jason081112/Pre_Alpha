#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#98 P0 验收（后端侧）：同一 WS 换人格必须换 agent；断线后那一轮仍要落库。

背景两个 bug：

1. **换人格不换人**：上游 `get_or_create_session` 只按 `session_id` 复用，不校验 persona_id，
   同一连接里切人格会把旧 agent 原样返回 → 回复还是旧人格的口吻。
   修法：`shell/ws_stream.py` 记录 `session_persona`，变了就把 session_id 置空。

2. **断线丢消息**：消息发出后连接断了，服务端那轮仍会跑完并写 chat_log，
   但 chunk 发在断掉的连接上。前端必须重连后补历史 —— 本脚本验证「服务端确实落库了」
   这个前提成立（前端补历史由 tests/ui_p0_smoke.mjs 验）。

    python3 scripts/dev/verify_98a_p0.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, p: dict) -> None:
        b = json.dumps(p).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json({"object": "list", "data": [{"id": "mock-ok", "object": "model", "created": 0}]})
            return
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}
        # 慢一点，好让「断线丢消息」的场景真实发生
        time.sleep(1.0)
        body = "【内心独白】嗯。\n【最终回复】我在。\n收到了。"
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for piece in ["【内心独白】嗯。\n", "【最终回复】我在。\n", "收到了。"]:
                ev = {"id": "m", "object": "chat.completion.chunk", "created": 0, "model": "mock-ok",
                      "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                data = f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
                self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
                time.sleep(0.4)
            done = b"data: [DONE]\n\n"
            self.wfile.write(f"{len(done):X}\r\n".encode() + done + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        self._json({"id": "m", "object": "chat.completion", "created": 0, "model": "mock-ok",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": body},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})


def start_mock() -> tuple[ThreadingHTTPServer, int]:
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def start_app(tmp: Path, mock_port: int) -> tuple[subprocess.Popen, str]:
    providers = tmp / "providers.json"
    providers.write_text(json.dumps({"providers": {"mock": {
        "upstream": "openai", "base_url": f"http://127.0.0.1:{mock_port}/v1",
        "api_key_env": "MOCK_API_KEY", "models": ["mock-ok"], "caps": ["zh", "chat"]}}}),
        encoding="utf-8")
    port = free_port()
    env = {
        **os.environ,
        "OPENAI_API_KEY": "sk-dummy-for-startup",
        "MOCK_API_KEY": "sk-mock",
        "ALPHA_DATA_DIR": str(tmp),
        "ALPHA_ROUTING_DB": str(tmp / "r.db"),
        "ALPHA_AUTH_DB": str(tmp / "a.db"),
        "ALPHA_LIMITS_DB": str(tmp / "l.db"),
        "ALPHA_AGENT_EXTRA_DB": str(tmp / "e.db"),
        "ALPHA_PERSONA_ROOT": str(tmp / "users"),
        "ALPHA_PROVIDERS_FILE": str(providers),
        "ALPHA_DEV_ROUTE": "mock/mock-ok",
        "ALPHA_REQUIRE_INVITE": "0",
        "ALPHA_PORT": str(port),
        "ALPHA_HOST": "127.0.0.1",
        "PYTHONUNBUFFERED": "1",
    }
    env.pop("ALPHA_AUTH_DISABLED", None)
    proc = subprocess.Popen([sys.executable, "-u", "-m", "shell.app"], cwd=str(ROOT), env=env,
                            stdout=(tmp / "log").open("w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        time.sleep(1)
        if proc.poll() is not None:
            raise RuntimeError(f"启动失败，见 {tmp / 'log'}")
        try:
            if httpx.get(base + "/api/auth/config", timeout=1.0).status_code == 200:
                return proc, base
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError("120 秒未就绪")


def register(base: str, email: str) -> str:
    r = httpx.post(base + "/api/auth/register",
                   json={"email": email, "password": "pa55word-123"}, timeout=20)
    if r.status_code == 201:
        return r.json()["access_token"]
    r = httpx.post(base + "/api/auth/login",
                   json={"email": email, "password": "pa55word-123"}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


async def two_personas_one_ws(base: str, token: str) -> tuple[dict, dict]:
    """同一个 WS 里先用 luna，再用 kai。返回两次 chat_end。"""
    import websockets

    url = base.replace("http://", "ws://") + f"/ws/chat/stream?token={token}"

    async def wait_end(ws) -> dict:
        deadline = time.time() + 90
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=20)
            m = json.loads(raw)
            if m.get("type") == "chat_end":
                return m
        raise RuntimeError("没等到 chat_end")

    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "chat", "content": "在吗", "persona_id": "luna",
                                  "client_id": "c1"}, ensure_ascii=False))
        await ws.send(json.dumps({"type": "typing", "active": False}))
        first = await wait_end(ws)

        await ws.send(json.dumps({"type": "chat", "content": "在吗", "persona_id": "kai",
                                  "client_id": "c1"}, ensure_ascii=False))
        await ws.send(json.dumps({"type": "typing", "active": False}))
        second = await wait_end(ws)
    return first, second


async def drop_midturn(base: str, token: str, client_id: str) -> None:
    """发完消息 0.5 秒就断开，模拟网络中断。"""
    import websockets

    url = base.replace("http://", "ws://") + f"/ws/chat/stream?token={token}"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "chat", "content": "断线测试", "persona_id": "luna",
                                  "client_id": client_id}, ensure_ascii=False))
        await ws.send(json.dumps({"type": "typing", "active": False}))
        await asyncio.sleep(0.5)
        await ws.close()  # 主动断，服务端那轮继续跑


def main() -> int:
    tmp = Path(f"/tmp/verify98a-{int(time.time())}")
    tmp.mkdir(parents=True, exist_ok=True)
    mock, mport = start_mock()
    proc, base = start_app(tmp, mport)
    print(f"临时目录: {tmp}\n被测服务: {base}\n")

    try:
        token = register(base, f"p0{int(time.time())}@x.test")

        try:
            first, second = asyncio.run(two_personas_one_ws(base, token))
            p1 = first.get("persona")
            p2 = second.get("persona")
            record("1 第一次 chat_end 人格 = Luna", p1 == "Luna", f"persona={p1}")
            record(
                "2 同一 WS 换人格 → chat_end 人格 = Kai（核心 bug）",
                p2 == "Kai",
                f"persona={p2}（修复前会是 Luna）",
            )
        except Exception as exc:  # noqa: BLE001
            record("1-2 换人格", False, f"{type(exc).__name__}: {exc}")

        cid = f"drop{int(time.time())}"
        try:
            asyncio.run(drop_midturn(base, token, cid))
            record("3 断线测试：消息已发出后断开", True)
        except Exception as exc:  # noqa: BLE001
            record("3 断线测试", False, f"{type(exc).__name__}: {exc}")

        # 等那一轮跑完（mock 每片 0.4s × 3 + 1s 预处理）
        time.sleep(12)
        r = httpx.get(f"{base}/api/chat/history/luna?client_id={cid}&limit=20",
                      headers={"Authorization": f"Bearer {token}"}, timeout=20)
        msgs = r.json().get("messages", []) if r.status_code == 200 else []
        has_user = any(m["role"] == "user" and "断线测试" in m["content"] for m in msgs)
        has_reply = any(m["role"] == "assistant" for m in msgs)
        record("4 断线后用户消息已落库", has_user, f"n={len(msgs)}")
        record("5 断线后服务端仍生成并落库回复", has_reply, f"n={len(msgs)}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        mock.shutdown()

    passed = sum(1 for r in RESULTS if r["ok"] is True)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    Path("/tmp/verify-98a.json").write_text(
        json.dumps({"results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
