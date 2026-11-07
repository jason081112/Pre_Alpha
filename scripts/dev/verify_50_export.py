#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#50 验收：对话导出（Markdown / JSON）与用户隔离。

重点验隔离：B 用户即使拿到 A 的 client_id，也导不出 A 的对话。
（靠 ScopedChatLog 把 client_id 映射成 `<user前缀>:<client_id>`。）

    python3 scripts/dev/verify_50_export.py
"""

from __future__ import annotations

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
        text = "【内心独白】嗯。\n【最终回复】我在。\n快去把鞋换了。"
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for piece in ["【内心独白】嗯。\n", "【最终回复】我在。\n", "快去把鞋换了。"]:
                ev = {"id": "m", "object": "chat.completion.chunk", "created": 0,
                      "model": "mock-ok",
                      "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                data = f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
                self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
                time.sleep(0.05)
            done = b"data: [DONE]\n\n"
            self.wfile.write(f"{len(done):X}\r\n".encode() + done + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        self._json({
            "id": "m", "object": "chat.completion", "created": 0, "model": "mock-ok",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        })


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


def say(base: str, token: str, text: str, client_id: str) -> bool:
    r = httpx.post(base + "/api/chat",
                   headers={"Authorization": f"Bearer {token}"},
                   json={"message": text, "persona_id": "luna", "user_name": "u",
                         "client_id": client_id},
                   timeout=180)
    return r.status_code == 200


def main() -> int:
    tmp = Path(f"/tmp/verify50-{int(time.time())}")
    tmp.mkdir(parents=True, exist_ok=True)
    mock, mport = start_mock()
    proc, base = start_app(tmp, mport)
    print(f"临时目录: {tmp}\n被测服务: {base}\n")

    try:
        # 未认证
        r = httpx.get(base + "/api/export/chat/luna?client_id=x", timeout=10)
        record("1 未认证 → 401", r.status_code == 401, f"status={r.status_code}")

        t_a = register(base, f"a{int(time.time())}@x.test")
        t_b = register(base, f"b{int(time.time())}@x.test")
        cid = "shared-client"

        ok = say(base, t_a, "今天加班到现在", cid)
        record("2 用户 A 发出一条消息", ok)

        h = {"Authorization": f"Bearer {t_a}"}
        r = httpx.get(f"{base}/api/export/chat/luna?client_id={cid}&format=md",
                      headers=h, timeout=20)
        md = r.text
        record("3 A 导出 Markdown 200", r.status_code == 200, f"status={r.status_code}")
        record("4 带 Content-Disposition", "attachment" in r.headers.get("content-disposition", ""),
               r.headers.get("content-disposition", "")[:60])
        record("5 md 含对话内容", "今天加班到现在" in md and "对话记录" in md)
        record("6 消息计数头", r.headers.get("x-alpha-messages") == "2",
               f"x-alpha-messages={r.headers.get('x-alpha-messages')}")

        r = httpx.get(f"{base}/api/export/chat/luna?client_id={cid}&format=json",
                      headers=h, timeout=20)
        data = r.json()
        record("7 JSON schema", data.get("schema") == "alpha.export.chat/v1", str(data.get("schema")))
        record("8 JSON 条数", data.get("count") == 2, f"count={data.get('count')}")

        # 隔离：B 用同一个 client_id
        r = httpx.get(f"{base}/api/export/chat/luna?client_id={cid}&format=json",
                      headers={"Authorization": f"Bearer {t_b}"}, timeout=20)
        d_b = r.json()
        record("9 隔离：B 拿 A 的 client_id 导出为空",
               r.status_code == 200 and d_b.get("count") == 0,
               f"status={r.status_code} count={d_b.get('count')}")

        # 空历史
        r = httpx.get(f"{base}/api/export/chat/luna?client_id=nobody&format=md",
                      headers=h, timeout=20)
        record("10 空历史返回合法文档", r.status_code == 200 and "还没有任何消息" in r.text)

        # 参数校验
        r = httpx.get(f"{base}/api/export/chat/luna?client_id={cid}&format=xml", headers=h, timeout=10)
        record("11 非法 format → 422", r.status_code == 422, f"status={r.status_code}")
        r = httpx.get(f"{base}/api/export/chat?client_id={cid}", headers=h, timeout=10)
        record("12 缺 persona_id → 400", r.status_code == 400, f"status={r.status_code}")
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
    Path("/tmp/verify-50.json").write_text(
        json.dumps({"results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
