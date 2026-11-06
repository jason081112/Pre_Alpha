#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#96 验收：流式多段逐条推送。

核心不是「有没有 segment_end」，而是**段之间真的停了**：
mock 分片间隔 500ms；若实现退化成一口气给，段间间隔会接近 0。

    python3 scripts/dev/verify_96_segments.py
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
CHUNK_DELAY = 0.5

# 三段回复，空行分隔（引擎协议要求）
MULTI = [
    "【内心独白】他累了。\n【最终回复】我在。\n",
    "\n快去把鞋换了。\n",
    "\n先去洗个热水澡。",
]
SINGLE = ["【内心独白】嗯。\n【最终回复】我在。\n别急。"]


def record(name: str, ok: bool | None, detail: str = "") -> None:
    RESULTS.append({"check": name, "ok": ok, "detail": detail})
    mark = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
    pieces = MULTI

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
        pieces = self.pieces

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i, piece in enumerate(pieces):
                if i:
                    time.sleep(CHUNK_DELAY)
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
                        "message": {"role": "assistant", "content": "".join(pieces)},
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
                        "client_id": f"c{int(time.time() * 1000)}",
                    },
                    ensure_ascii=False,
                )
            )
            await asyncio.sleep(0.3)
        await ws.send(json.dumps({"type": "typing", "active": False}))
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                break
            msg = json.loads(raw)
            events.append((time.monotonic() - t0, msg))
            if msg.get("type") == "chat_end":
                break
    return events


def check_multi(base: str) -> None:
    MockHandler.pieces = MULTI
    token = register(base, f"m{int(time.time() * 1000)}@x.test")
    try:
        events = asyncio.run(stream_turn(base, token))
    except Exception as exc:  # noqa: BLE001
        record("多段连接", False, f"{type(exc).__name__}: {exc}")
        return

    chunks = [e for e in events if e[1].get("type") == "chunk"]
    ends = [e for e in events if e[1].get("type") == "segment_end"]
    finals = [e for e in events if e[1].get("type") == "chat_end"]

    record("1 三段 → 3 个 segment_end", len(ends) == 3, f"n={len(ends)}")
    record(
        "2 segment 递增 0/1/2",
        [e[1].get("segment") for e in ends] == [0, 1, 2],
        f"{[e[1].get('segment') for e in ends]}",
    )

    if len(ends) >= 2:
        gaps = [
            (ends[i + 1][0] - ends[i][0]) * 1000 for i in range(len(ends) - 1)
        ]
        record("3a 段间停顿 >= 250ms", all(g >= 250 for g in gaps), f"gaps={[round(g) for g in gaps]}")

    # index 每段从 0 开始
    per_seg: dict[int, list[int]] = {}
    for _, m in chunks:
        per_seg.setdefault(m.get("segment", -1), []).append(m.get("index"))
    ok_idx = all(
        idxs == list(range(len(idxs))) for idxs in per_seg.values()
    ) and bool(per_seg)
    record("4 index 每段从 0 连续", ok_idx, f"{per_seg}")

    if finals:
        fe = finals[0][1]
        pushed = fe.get("segments") or []
        joined = "".join(c[1].get("content", "") for c in chunks)
        norm = lambda s: "".join(s.split())  # noqa: E731
        record(
            "5 chat_end.segments 与 chunk 拼接一致",
            norm("".join(pushed)) == norm(joined),
            f"pushed={pushed}",
        )
        record(
            "6 chat_end 附引擎 segments 字段",
            "engine_segments" in fe,
            f"engine_segments={fe.get('engine_segments')}",
        )
        record(
            "7 段间 chat_start 重开打字指示",
            sum(1 for e in events if e[1].get("type") == "chat_start") >= 3,
            f"chat_start={sum(1 for e in events if e[1].get('type') == 'chat_start')}",
        )


def check_single_regression(base: str) -> None:
    """单段回复行为必须与 #94 一致。"""
    MockHandler.pieces = SINGLE
    token = register(base, f"s{int(time.time() * 1000)}@x.test")
    try:
        events = asyncio.run(stream_turn(base, token))
    except Exception as exc:  # noqa: BLE001
        record("单段回归", False, f"{type(exc).__name__}: {exc}")
        return
    ends = [e for e in events if e[1].get("type") == "segment_end"]
    finals = [e for e in events if e[1].get("type") == "chat_end"]
    record("8 单段只 1 个 segment_end", len(ends) == 1, f"n={len(ends)}")
    if finals:
        record(
            "9 单段 chat_end.segments 长度为 1",
            len(finals[0][1].get("segments") or []) == 1,
            f"{finals[0][1].get('segments')}",
        )


def check_debounce(base: str) -> None:
    MockHandler.pieces = MULTI
    token = register(base, f"d{int(time.time() * 1000)}@x.test")
    try:
        events = asyncio.run(stream_turn(base, token, burst=3))
    except Exception as exc:  # noqa: BLE001
        record("10 防抖", False, f"{type(exc).__name__}: {exc}")
        return
    finals = [e for e in events if e[1].get("type") == "chat_end"]
    record("10 防抖：3 条 → 1 轮", len(finals) == 1, f"chat_end={len(finals)}")


def main() -> int:
    tmp = Path(f"/tmp/verify96-{int(time.time())}")
    tmp.mkdir(parents=True, exist_ok=True)
    mock, mock_port = start_mock()
    proc, base = start_app(tmp, mock_port)
    print(f"临时目录: {tmp}\n被测服务: {base}\nmock: 分片间隔 {CHUNK_DELAY}s\n")
    try:
        check_multi(base)
        check_single_regression(base)
        check_debounce(base)
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
    Path("/tmp/verify-96.json").write_text(
        json.dumps({"results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
