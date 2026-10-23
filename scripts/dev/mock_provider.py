"""最小 OpenAI 兼容 mock，用于 #70 集成验证。

模型：
- mock-ok    正常返回
- mock-500   固定 500
- mock-cut   流式先吐一个 chunk，然后断连接（测“首 token 后不重试”）
- mock-slow  睡 65 秒（测超时）
"""

import json
import socket
import struct
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HITS = {"models": 0, "chat": 0, "stream": 0}
MODELS = ("mock-ok", "mock-500", "mock-cut", "mock-slow")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            HITS["models"] += 1
            self._json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": m, "object": "model", "created": 0, "owned_by": "mock"}
                        for m in MODELS
                    ],
                },
            )
        elif self.path.endswith("/stats"):
            self._json(200, HITS)
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            return self._json(404, {"error": {"message": "not found"}})

        length = int(self.headers.get("content-length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": {"message": "bad json"}})

        model = body.get("model", "mock-ok")
        stream = bool(body.get("stream"))

        if model == "mock-500":
            HITS["chat"] += 1
            return self._json(
                500, {"error": {"message": "mock boom", "type": "server_error"}}
            )

        if model == "mock-slow":
            time.sleep(65)

        if not stream:
            HITS["chat"] += 1
            return self._json(
                200,
                {
                    "id": "mock",
                    "object": "chat.completion",
                    "created": 0,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "mock reply"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
            )

        HITS["stream"] += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def chunk(delta, finish=None):
            payload = {
                "id": "mock",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()

        chunk({"role": "assistant", "content": "mock "})
        if model == "mock-cut":
            # 强制 RST，确保客户端在收到首块后遇到连接错误
            self.connection.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            self.connection.close()
            return

        chunk({"content": "reply"})
        chunk({}, "stop")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 9999), Handler)
    print("mock listening on 127.0.0.1:9999", flush=True)
    server.serve_forever()
