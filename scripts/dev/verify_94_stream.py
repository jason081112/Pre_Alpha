#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#94 验收：真流式 WS 端点的「首字延迟」与「分块到达」。

关键不是「有没有 chunk」，而是 **chunk 是不是分批到达**。
所以 mock 每个分片之间停 500ms；如果实现退化成非流式，
所有 chunk 会在同一时刻涌出来 —— 后者同样能通过「chunk 数 >= 3」，必须靠时间差区分。

    python3 scripts/dev/verify_94_stream.py
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
CHUNK_DELAY = 0.5  # 每个分片之间的间隔（秒）


def record(name: str, ok: bool | None, detail: str = "") -> None:
    RESULTS.append({"check": name, "ok": ok, "detail": detail})
    mark = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── mock LLM：分片之间刻意停 500ms ────────────────────────────────
PIECES = [
    "【内心独白】他好像有点急。\n【最终回复】我在。\n",
    "别急，",
    "慢慢说。",
]


class MockState:
    calls = 0
    lock = threading.Lock()

    @classmethod
    def bump(cls) -> int:
        with cls.lock:
            cls.calls += 1
            return cls.calls


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json(
                {"object": "list", "data": [{"id": "mock-ok", "object": "model", "created": 0}]}
            )
            return
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}
        n = MockState.bump()
        model = payload.get("model") or "mock-ok"

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i, piece in enumerate(PIECES):
                if i:
                    time.sleep(CHUNK_DELAY)  # ← 这里的间隔是本次验收的核心
                event = {
                    "id": f"mock-{n}",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                    ],
                }
                data = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
                self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
            done = b"data: [DONE]\n\n"
            self.wfile.write(f"{len(done):X}\r\n".encode() + done + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return

        self._json(
            {
                "id": f"mock-{n}",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "".join(PIECES)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )


def start_mock() -> tuple[ThreadingHTTPServer, int]:
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def start_app(tmp: Path, mock_port: int) -> tuple[subprocess.Popen, str]:
    providers = tmp / "providers.json"
    providers.write_text(
        json.dumps(
            {
                "providers": {
                    "mock": {
                        "upstream": "openai",
                        "base_url": f"http://127.0.0.1:{mock_port}/v1",
                        "api_key_env": "MOCK_API_KEY",
                        "models": ["mock-ok"],
                        "caps": ["zh", "chat", "json"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    port = free_port()
    env = {
        **os.environ,
        "OPENAI_API_KEY": "sk-dummy-for-startup",
        "MOCK_API_KEY": "sk-mock",
        "ALPHA_DATA_DIR": str(tmp),
        "ALPHA_ROUTING_DB": str(tmp / "routing.db"),
        "ALPHA_AUTH_DB": str(tmp / "auth.db"),
        "ALPHA_LIMITS_DB": str(tmp / "limits.db"),
        "ALPHA_AGENT_EXTRA_DB": str(tmp / "agent.db"),
        "ALPHA_PERSONA_ROOT": str(tmp / "users"),
        "ALPHA_PROVIDERS_FILE": str(providers),
        "ALPHA_DEV_ROUTE": "mock/mock-ok",
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


def register(base: str, email: str) -> str:
    r = httpx.post(
        f"{base}/api/auth/register",
        json={"email": email, "password": "pa55word-123"},
        timeout=20.0,
    )
    if r.status_code == 201:
        return r.json()["access_token"]
    r = httpx.post(
        f"{base}/api/auth/login",
        json={"email": email, "password": "pa55word-123"},
        timeout=20.0,
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def stream_turn(base: str, token: str, burst: int = 1) -> list[tuple[float, dict]]:
    """连 /ws/chat/stream，连发 burst 条，返回 [(到达时刻, 消息)]。"""
    import websockets

    url = base.replace("http://", "ws://") + f"/ws/chat/stream?token={token}"
    events: list[tuple[float, dict]] = []
    t0 = time.monotonic()
    async with websockets.connect(url) as ws:
        for i in range(burst):
            await ws.send(
                json.dumps(
                    {
                        "type": "chat",
                        "content": f"第{i + 1}句",
                        "persona_id": "luna",
                        "client_id": f"c-{int(time.time())}",
                    },
                    ensure_ascii=False,
                )
            )
        # 模拟真实前端：按发送键后输入框已空 → typing false，窗口缩到 2s
        await ws.send(json.dumps({"type": "typing", "active": False}))
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.5)
            except asyncio.TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            events.append((time.monotonic() - t0, msg))
            if msg.get("type") == "chat_end" and burst > 0:
                # 一轮结束：等一个窗口看会不会再来一轮（防抖）
                try:
                    extra = await asyncio.wait_for(ws.recv(), timeout=6.0)
                    events.append((time.monotonic() - t0, json.loads(extra)))
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    pass
                break
    return events


def check_stream(base: str) -> None:
    token = register(base, f"st{int(time.time() * 1000)}@x.test")
    try:
        events = asyncio.run(stream_turn(base, token, burst=1))
    except Exception as exc:  # noqa: BLE001
        record("流式连接", False, f"{type(exc).__name__}: {exc}")
        return

    starts = [e for e in events if e[1].get("type") == "chat_start"]
    chunks = [e for e in events if e[1].get("type") == "chunk"]
    ends = [e for e in events if e[1].get("type") == "chat_end"]

    record("1 收到 chat_start", len(starts) == 1, f"n={len(starts)}")
    record("2 分块数 >= 3", len(chunks) >= 3, f"n={len(chunks)}")

    if not chunks or not ends:
        record("3 首字延迟 < 800ms", False, "没有 chunk")
        record("4 分块分批到达", False, "没有 chunk")
        record("5 chunk 拼接 == chat_end.reply", False, "缺数据")
        return

    # 「首字延迟」要从 chat_start 算：发送到 chat_start 之间是防抖窗口（设计行为），
    # 不是流式能力。流式要证明的是「开始生成 → 第一个字」足够短。
    start_at = starts[0][0] if starts else 0.0
    first_after_start_ms = (chunks[0][0] - start_at) * 1000
    server_first_ms = ends[0][1].get("first_chunk_ms") if ends else None
    record(
        "3a 首字延迟（服务端计时）< 800ms",
        isinstance(server_first_ms, int) and server_first_ms < 800,
        f"first_chunk_ms={server_first_ms}ms（mock 全文约 1000ms）",
    )
    record(
        "3b 首字延迟（客户端，从 chat_start 起）< 800ms",
        first_after_start_ms < 800,
        f"{first_after_start_ms:.0f}ms；防抖窗口 {(start_at * 1000):.0f}ms（设计行为）",
    )

    # 关键判据：最后一个 chunk 比第一个晚 >= 400ms
    span_ms = (chunks[-1][0] - chunks[0][0]) * 1000
    record(
        "4 分块分批到达（非一次性）",
        span_ms >= 400,
        f"首尾跨度 {span_ms:.0f}ms",
    )

    joined = "".join(c[1].get("content", "") for c in chunks)
    reply = ends[0][1].get("reply", "")
    norm = lambda s: "".join(s.split())  # noqa: E731
    record(
        "5 chunk 拼接 == chat_end.reply",
        norm(joined) == norm(reply),
        f"chunks={joined!r} reply={reply!r}",
    )
    record(
        "6 chat_end 带延迟指标",
        isinstance(ends[0][1].get("first_chunk_ms"), int),
        f"first_chunk_ms={ends[0][1].get('first_chunk_ms')}, chunks={ends[0][1].get('chunks')}",
    )


def check_debounce(base: str) -> None:
    token = register(base, f"db{int(time.time() * 1000)}@x.test")
    try:
        events = asyncio.run(stream_turn(base, token, burst=3))
    except Exception as exc:  # noqa: BLE001
        record("7 防抖", False, f"{type(exc).__name__}: {exc}")
        return
    ends = [e for e in events if e[1].get("type") == "chat_end"]
    record("7 防抖：3 条 → 1 轮", len(ends) == 1, f"chat_end={len(ends)}")


def check_auth(base: str) -> None:
    import websockets

    async def go() -> int:
        url = base.replace("http://", "ws://") + "/ws/chat/stream"
        try:
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps({"type": "chat", "content": "hi", "persona_id": "luna"}))
                await asyncio.wait_for(ws.recv(), timeout=5)
                return 0
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", None)
            return code if isinstance(code, int) else -1

    code = asyncio.run(go())
    record("8 无 token → 1008 关闭", code == 1008, f"close code={code}")


def main() -> int:
    tmp = Path(f"/tmp/verify94-{int(time.time())}")
    tmp.mkdir(parents=True, exist_ok=True)
    mock, mock_port = start_mock()
    proc, base = start_app(tmp, mock_port)
    print(f"临时目录: {tmp}\n被测服务: {base}\nmock: 分片间隔 {CHUNK_DELAY}s\n")
    try:
        check_stream(base)
        check_debounce(base)
        check_auth(base)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        mock.shutdown()

    passed = sum(1 for r in RESULTS if r["ok"] is True)
    skipped = sum(1 for r in RESULTS if r["ok"] is None)
    total = len(RESULTS) - skipped
    print(f"\n{passed}/{total} passed" + (f"，{skipped} skipped" if skipped else ""))
    out = Path("/tmp/verify-94.json")
    out.write_text(
        json.dumps({"results": RESULTS, "mock_calls": MockState.calls}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"结果: {out}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
