"""#81 真实 HTTP 传输冒烟（需要 httpx，CI 运行）。

本地 Termux 不装 httpx；这个脚本放在 GitHub Actions 里跑，验证：

- 主机名 localhost 被固定到 127.0.0.1，但 Host 头仍是 localhost:port
- 请求能到达本机 mock
- 302 不会被跟随
- 未开 ALPHA_ALLOW_PRIVATE_PROVIDERS 时私网地址被拒绝
- 首次解析公网、二次解析 127.0.0.1 的 rebinding 策略拒绝
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.outbound import (  # noqa: E402
    OutboundPolicyError,
    build_async_client,
    plan_outbound,
)

PUBLIC = "93.184.216.34"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        self.server.seen.append((self.path, self.headers.get("Host", "")))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/v1/models")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b'{"data":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: D401 - 静音
        return


class _SequenceResolver:
    def __init__(self, *answers: list[str]):
        self.answers = list(answers)

    def __call__(self, host: str, port: int) -> list[str]:
        return self.answers.pop(0)


async def _main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.seen = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        target = plan_outbound(
            f"http://localhost:{port}/v1",
            allow_private=True,
            resolver=lambda host, p: ["127.0.0.1"],
        )
        assert target.ip == "127.0.0.1"
        assert target.host_header == f"localhost:{port}"

        async with build_async_client(target, timeout=3.0) as client:
            response = await client.get(f"http://localhost:{port}/v1/models")
            assert response.status_code == 200, response.status_code
            first = server.seen[-1]
            assert first == ("/v1/models", f"localhost:{port}"), first

            redirect = await client.get(f"http://localhost:{port}/redirect")
            assert redirect.status_code == 302, redirect.status_code
            assert len(server.seen) == 2, server.seen

        old = os.environ.pop("ALPHA_ALLOW_PRIVATE_PROVIDERS", None)
        try:
            try:
                plan_outbound(f"http://127.0.0.1:{port}/v1", allow_private=False)
            except OutboundPolicyError:
                pass
            else:
                raise AssertionError("私网地址必须被默认策略拒绝")
        finally:
            if old is not None:
                os.environ["ALPHA_ALLOW_PRIVATE_PROVIDERS"] = old

        resolver = _SequenceResolver([PUBLIC], ["127.0.0.1"])
        plan_outbound(
            "https://rebind.example/v1",
            allow_private=False,
            resolver=resolver,
        )
        try:
            plan_outbound(
                "https://rebind.example/v1",
                allow_private=False,
                resolver=resolver,
            )
        except OutboundPolicyError:
            pass
        else:
            raise AssertionError("二次解析到 127.0.0.1 必须被拒绝")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    asyncio.run(_main())
    print("outbound http ok")
