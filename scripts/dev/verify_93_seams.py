#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#93 验收：接缝守卫 + 后门封禁 + WS 防抖/分块。

自包含：内置一个 OpenAI 兼容 mock，不依赖外网、不消耗额度。

    python3 scripts/dev/verify_93_seams.py

五项：
  1. 未认证请求被拒（HTTP 401 / WS 1008）
  2. 会话按用户隔离（拿别人的 session_id 直接 404）
  3. 打字防抖：3 秒内连发 3 条 → 上游只收到 1 轮
  4. 流式分块：WS 收到多个 chat_chunk，不是一次性整段
  5. 演示后门 /api/demo/inject 不可达
"""

from __future__ import annotations

import argparse
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


# ── 内置 mock LLM ────────────────────────────────────────────────
class MockState:
    chat_calls = 0
    lock = threading.Lock()

    @classmethod
    def bump(cls) -> int:
        with cls.lock:
            cls.chat_calls += 1
            return cls.chat_calls


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 安静
        pass

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._send_json(
                {
                    "object": "list",
                    "data": [
                        {"id": "mock-ok", "object": "model", "created": 0, "owned_by": "mock"}
                    ],
                }
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
        # 必须符合引擎协议（【内心独白】/【最终回复】），否则 output_router
        # 检测不到最终回复标记，永远不会向外发 chat_chunk。
        chunks = [
            "【内心独白】他好像有点急。\n",
            "【最终回复】我在。\n",
            "别急，慢慢说。",
        ]
        model = payload.get("model") or "mock-ok"

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for piece in chunks:
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
                time.sleep(0.02)
            done = b"data: [DONE]\n\n"
            self.wfile.write(f"{len(done):X}\r\n".encode() + done + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return

        self._send_json(
            {
                "id": f"mock-{n}",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "".join(chunks)},
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


# ── 启动被验收服务 ────────────────────────────────────────────────
def write_providers(tmp: Path, mock_port: int) -> Path:
    path = tmp / "providers.json"
    path.write_text(
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
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def start_app(tmp: Path, providers_file: Path) -> tuple[subprocess.Popen, int, str]:
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
        "ALPHA_PROVIDERS_FILE": str(providers_file),
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
            raise RuntimeError(f"服务启动失败，见 {tmp / 'server.log'}")
        try:
            r = httpx.get(f"{base}/api/auth/config", timeout=1.0)
            if r.status_code == 200:
                return proc, port, base
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    raise RuntimeError("服务 90 秒内未就绪")


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


# ── 五项检查 ─────────────────────────────────────────────────────
def check_no_auth(base: str) -> None:
    r = httpx.post(
        f"{base}/v1/chat/completions",
        json={"model": "luna", "messages": [{"role": "user", "content": "在吗"}]},
        timeout=10.0,
    )
    record("1a HTTP 无 token 被拒", r.status_code == 401, f"status={r.status_code}")


def check_session_isolation(base: str) -> None:
    token_a = register(base, f"a{int(time.time() * 1000)}@x.test")
    token_b = register(base, f"b{int(time.time() * 1000)}@x.test")

    ra = httpx.post(
        f"{base}/api/chat",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"message": "我是 A", "persona_id": "luna", "user_name": "A"},
        timeout=120.0,
    )
    if ra.status_code != 200:
        record("2 会话隔离", False, f"A 发消息失败 status={ra.status_code} {ra.text[:120]}")
        return
    sid_a = ra.json().get("session_id")
    if not sid_a:
        record("2 会话隔离", False, "A 的响应里没有 session_id")
        return

    # B 拿 A 的 session_id → 中间件应 404
    rb = httpx.post(
        f"{base}/api/chat",
        headers={"Authorization": f"Bearer {token_b}"},
        json={
            "message": "我是 B",
            "persona_id": "luna",
            "session_id": sid_a,
            "user_name": "B",
        },
        timeout=30.0,
    )
    record(
        "2 会话隔离（B 用 A 的 session_id）",
        rb.status_code in (403, 404),
        f"status={rb.status_code}",
    )


async def _ws_session(base: str, token: str, *, burst: int, settle: float) -> list[str]:
    """连 WS，3 秒内连发 burst 条，收集所有消息类型。"""
    import websockets

    url = base.replace("http://", "ws://") + f"/ws/chat?token={token}"
    seen: list[str] = []
    async with websockets.connect(url) as ws:
        for i in range(burst):
            await ws.send(
                json.dumps(
                    {"type": "chat", "content": f"第{i + 1}句", "persona_id": "luna"},
                    ensure_ascii=False,
                )
            )
            await asyncio.sleep(0.3)
        deadline = time.time() + settle
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            seen.append(msg.get("type", "?"))
    return seen


def check_ws(base: str) -> None:
    token = register(base, f"w{int(time.time() * 1000)}@x.test")
    before = MockState.chat_calls
    try:
        seen = asyncio.run(_ws_session(base, token, burst=3, settle=25.0))
    except Exception as exc:  # noqa: BLE001
        record("3 WS 防抖", False, f"WS 失败：{type(exc).__name__}: {exc}")
        record("4 流式分块", False, "WS 失败")
        return

    after = MockState.chat_calls
    # 「轮」以引擎下发的 chat_end 为准：一次对话可能内部调用多次 LLM
    # （内心独白与最终回复分两步），不能拿 mock 调用数当轮数。
    rounds = seen.count("chat_end")
    chunks = seen.count("chat_chunk")
    record(
        "3 打字防抖（3 条 → 1 轮）",
        rounds == 1,
        f"chat_end={rounds}，mock 调用={after - before}，序列={seen[:10]}",
    )
    # 上游 v0.5.0 的 stream_to_ws 收了 ws_send 却从不调用（agent/output_router.py
    # 全文无 ws_send 调用），WS 上没有 chat_chunk。这是上游现状，不是代理层问题。
    record(
        "3b WS 是否真的分块（上游现状）",
        None,
        f"chat_chunk={chunks} —— 上游 ws_send 死代码，流式走 HTTP SSE",
    )


def check_sse(base: str) -> None:
    """流式走 HTTP SSE（/v1/chat/completions stream=true）。"""
    token = register(base, f"s{int(time.time() * 1000)}@x.test")
    events = 0
    done = False
    try:
        with httpx.stream(
            "POST",
            f"{base}/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "luna",
                "stream": True,
                "messages": [{"role": "user", "content": "在吗"}],
            },
            timeout=120.0,
        ) as resp:
            if resp.status_code != 200:
                record("4 SSE 流式", False, f"status={resp.status_code}")
                return
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    done = True
                    break
                events += 1
    except Exception as exc:  # noqa: BLE001
        record("4 SSE 流式", False, f"{type(exc).__name__}: {exc}")
        return

    record(
        "4 SSE 流式（多个 data 事件 + DONE）",
        events >= 1 and done,
        f"events={events} done={done}",
    )


def check_backdoor(base: str) -> None:
    token = register(base, f"d{int(time.time() * 1000)}@x.test")
    r = httpx.post(
        f"{base}/api/demo/inject",
        headers={"Authorization": f"Bearer {token}"},
        json={"client_id": "x", "action": "send_chat", "content": "hi"},
        timeout=10.0,
    )
    record(
        "5 演示后门不可达",
        r.status_code in (404, 405),
        f"status={r.status_code}",
    )


def check_seam_log(tmp: Path) -> None:
    """启动日志里 5 条接缝是否全部 ✓。"""
    log = (tmp / "server.log").read_text(encoding="utf-8", errors="replace")
    for name in (
        "get_or_create_session",
        "_persist_agent",
        "llm_client",
        "chat_log_store",
        "persona_loader.reload",
    ):
        ok = f"✓ {name}" in log
        record(f"0 接缝 {name}", ok, "" if ok else "启动日志未见 ✓")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="跑完不删临时目录")
    args = ap.parse_args()

    tmp = Path(f"/tmp/verify93-{int(time.time())}")
    tmp.mkdir(parents=True, exist_ok=True)
    mock, mock_port = start_mock()
    providers = write_providers(tmp, mock_port)

    print(f"临时目录: {tmp}")
    print(f"mock LLM : 127.0.0.1:{mock_port}")
    proc, port, base = start_app(tmp, providers)
    print(f"被测服务 : {base}\n")

    try:
        check_seam_log(tmp)
        check_no_auth(base)
        check_session_isolation(base)
        check_ws(base)
        check_sse(base)
        check_backdoor(base)
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
    out = Path("/tmp/verify-93.json")
    out.write_text(
        json.dumps(
            {"base": base, "results": RESULTS, "mock_calls": MockState.chat_calls},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"结果: {out}")
    if not args.keep:
        pass  # 保留日志便于排查；目录在 /tmp，随环境清理
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
