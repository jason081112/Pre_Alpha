#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#68 真实 HTTP 冒烟：OpenAI 兼容端点、官方 SDK、流式、错误码。

依赖（只在 CI/开发机安装）：
    pip install fastapi uvicorn httpx pydantic openai

运行：
    python scripts/dev/verify_openai_http.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import httpx
    import uvicorn
    from fastapi import FastAPI
    from openai import OpenAI
except ModuleNotFoundError as exc:  # pragma: no cover - 本地默认不装依赖
    print(f"SKIP: 缺少运行时依赖 ({exc.name})，请在 CI/开发机安装")
    raise SystemExit(2)

from shell.auth.middleware import AuthMiddleware  # noqa: E402
from shell.limits import (  # noqa: E402
    LimitConfig,
    LimitsMiddleware,
    LimitsService,
)
from shell.openai_api import (  # noqa: E402
    build_openai_router,
    install_openai_error_handlers,
)
from shell.providers import ProviderSpec, Registry, Route  # noqa: E402

TOKENS = {
    "alice-key": "alice",
    "rate-key": "rate-user",
}
PERSONAS = ("luna", "vivian")
TEST_ROUTE = Route("mock", "mock-persona")


class FakeUser:
    def __init__(self, user_id: str):
        self.id = user_id


class FakeAuthService:
    def authenticate(self, token: str | None) -> FakeUser | None:
        user_id = TOKENS.get((token or "").strip())
        return FakeUser(user_id) if user_id else None


class FakePersonaLoader:
    def list_ids(self) -> list[str]:
        return list(PERSONAS)


class FakeChatLog:
    def __init__(self) -> None:
        self.turns: list[dict] = []

    def save_turn(self, **kwargs) -> None:
        self.turns.append(kwargs)

    def load_messages(self, **_kwargs) -> list[dict]:
        return []


class FakeAgent:
    def __init__(self, persona_id: str, user_name: str, client_id: str):
        self.persona_id = persona_id
        self.user_name = user_name
        self.client_id = client_id
        self._last_modality = "文字"

    async def chat(self, user_message: str) -> dict:
        if "boom" in user_message:
            raise RuntimeError("fake provider boom")
        return {
            "reply": f"{self.persona_id}:{user_message}",
            "modality": "文字",
        }

    async def chat_stream(self, user_message: str):
        if "boom" in user_message:
            raise RuntimeError("fake provider boom")
        yield "__FEEL_DONE__"
        raw = (
            "【内心独白】\n这段是内部推理，不能给客户端\n"
            f"【最终回复】\n{self.persona_id}流式：{user_message}\n"
            "【表达方式】文字"
        )
        for index in range(0, len(raw), 3):
            yield raw[index : index + 3]
        self._last_modality = "文字"


class FakeUpstream:
    def __init__(self) -> None:
        self.persona_loader = FakePersonaLoader()
        self.chat_log_store = FakeChatLog()
        self.persisted: list[str] = []
        self._counter = 0

    def get_or_create_session(
        self,
        session_id,
        persona_id,
        user_name=None,
        client_id=None,
    ):
        if persona_id not in PERSONAS:
            raise ValueError(f"角色 {persona_id!r} 不存在")
        self._counter += 1
        return (
            f"sid-{self._counter}",
            FakeAgent(persona_id, user_name or "local", client_id or ""),
        )

    def _persist_agent(self, agent) -> None:
        self.persisted.append(getattr(agent, "persona_id", ""))


class FakeRecord:
    def __init__(self, route: Route, fallback=()):
        self.route = route
        self.fallback = tuple(fallback)


class FakeStore:
    def __init__(self) -> None:
        self.records: dict[str, FakeRecord] = {}

    def put(self, user: str, route: Route, fallback=()) -> None:
        self.records[user] = FakeRecord(route, fallback)

    def get(self, user: str):
        return self.records.get(user)

    def close(self) -> None:
        return None


def build_app() -> tuple[FastAPI, FakeUpstream, LimitsService, FakeStore]:
    upstream = FakeUpstream()
    store = FakeStore()
    store.put("alice", TEST_ROUTE)
    store.put("rate-user", TEST_ROUTE)
    registry = Registry(
        {
            "mock": ProviderSpec(
                id="mock",
                upstream="openai",
                base_url="http://127.0.0.1:9999/v1",
                api_key_env="",
                models=("mock-persona",),
                caps=("zh",),
            )
        }
    )
    limits = LimitsService(
        Path(tempfile.mkdtemp(prefix="alpha-openai-smoke-")) / "limits.db",
        defaults=LimitConfig(
            user_rpm=0,
            user_rpd=0,
            ip_rpm=0,
            ip_rpd=0,
            platform_daily_tokens=0,
        ),
    )

    app = FastAPI()
    app.include_router(
        build_openai_router(
            upstream,
            store=store,  # type: ignore[arg-type]
            registry=registry,
            custom_store=None,
            dev_route=None,
            limits=limits,
        )
    )
    install_openai_error_handlers(app)
    app.add_middleware(
        LimitsMiddleware,
        limits=limits,
        route_lookup=lambda user: (
            (store.get(user).route,) if store.get(user) is not None else ()
        ),
    )
    app.add_middleware(
        AuthMiddleware,
        service=FakeAuthService(),  # type: ignore[arg-type]
        required=True,
    )
    return app, upstream, limits, store


def wait_started(server: uvicorn.Server, timeout: float = 10.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.started:
            sockets = getattr(server.servers[0], "sockets", None) or []
            if sockets:
                return int(sockets[0].getsockname()[1])
        time.sleep(0.05)
    raise RuntimeError("uvicorn 未能启动")


def expect(response: httpx.Response, status: int, label: str) -> httpx.Response:
    if response.status_code != status:
        raise AssertionError(
            f"{label}: 期望 {status}，实际 {response.status_code}: {response.text}"
        )
    return response


def openai_error(response: httpx.Response, code: str) -> dict:
    payload = response.json()
    assert payload["error"]["code"] == code, payload
    assert payload["error"]["message"], payload
    return payload


def main() -> int:
    app, upstream, limits, store = build_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    port = wait_started(server)
    base_url = f"http://127.0.0.1:{port}"
    passed: list[str] = []
    auth = {"Authorization": "Bearer alice-key"}

    try:
        with httpx.Client(base_url=base_url, timeout=10.0) as client:
            # 1) 模型目录
            models = expect(
                client.get("/v1/models", headers=auth),
                200,
                "模型目录",
            ).json()
            ids = {item["id"] for item in models["data"]}
            assert {"alpha", "luna", "vivian"} <= ids, ids
            passed.append("GET /v1/models")

            # 2) curl 等价的一轮非流式对话
            completion = expect(
                client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "model": "luna",
                        "user": "curl-user",
                        "messages": [
                            {"role": "system", "content": "ignored"},
                            {"role": "user", "content": "你好"},
                        ],
                    },
                ),
                200,
                "curl 非流式",
            ).json()
            assert completion["object"] == "chat.completion"
            assert completion["choices"][0]["message"]["content"] == "luna:你好"
            passed.append("curl 等价非流式对话")

            # 3) 官方 openai-python SDK 非流式
            sdk = OpenAI(
                base_url=base_url + "/v1",
                api_key="alice-key",
                timeout=10.0,
                max_retries=0,
            )
            sdk_completion = sdk.chat.completions.create(
                model="luna",
                user="sdk-user",
                messages=[{"role": "user", "content": "SDK 你好"}],
            )
            assert sdk_completion.choices[0].message.content == "luna:SDK 你好"
            passed.append("openai-python SDK 非流式")

            # 4) 官方 SDK 流式；只应看到最终回复
            stream = sdk.chat.completions.create(
                model="vivian",
                user="sdk-stream-user",
                messages=[{"role": "user", "content": "流式你好"}],
                stream=True,
            )
            chunks = list(stream)
            text = "".join(
                choice.delta.content or ""
                for chunk in chunks
                for choice in chunk.choices
            )
            finishes = [
                choice.finish_reason
                for chunk in chunks
                for choice in chunk.choices
                if choice.finish_reason
            ]
            assert text.strip() == "vivian流式：流式你好", repr(text)
            assert "内部推理" not in text
            assert "stop" in finishes, finishes
            passed.append("openai-python SDK 流式（标记跨 chunk）")

            # 4.5) 原始 SSE：不依赖 SDK，模拟通用流式客户端
            with client.stream(
                "POST",
                "/v1/chat/completions",
                headers=auth,
                json={
                    "model": "luna",
                    "user": "sse-user",
                    "stream": True,
                    "messages": [{"role": "user", "content": "原始SSE"}],
                },
            ) as response:
                assert response.status_code == 200, response.read().decode()
                assert response.headers["content-type"].startswith(
                    "text/event-stream"
                ), response.headers
                raw_sse = "".join(response.iter_text())
            assert "data: [DONE]" in raw_sse
            assert "【最终回复】" not in raw_sse
            events = [
                line[6:]
                for line in raw_sse.splitlines()
                if line.startswith("data: ") and line != "data: [DONE]"
            ]
            sse_text = "".join(
                choice.get("delta", {}).get("content", "")
                for event in events
                for choice in json.loads(event).get("choices", [])
            )
            assert sse_text.strip() == "luna流式：原始SSE", repr(sse_text)
            passed.append("原始 SSE 标准流式消费")

            # 5) 显式 provider/model 路由
            routed = expect(
                client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "model": "mock/mock-persona",
                        "user": "route-user",
                        "messages": [{"role": "user", "content": "路由"}],
                    },
                ),
                200,
                "显式路由",
            ).json()
            assert routed["choices"][0]["message"]["content"] == "luna:路由"
            passed.append("model=provider/model 路由")

            # 6) 聊天记录落盘（流式与非流式都写）
            saved = upstream.chat_log_store.turns
            assert any(turn["client_id"] == "curl-user" for turn in saved), saved
            assert any(turn["client_id"] == "sdk-stream-user" for turn in saved), saved
            assert all("内部推理" not in turn["agent_reply"] for turn in saved), saved
            assert upstream.persisted
            passed.append("聊天记录与 agent 落盘")

            # 7) 401 错误结构与 WWW-Authenticate
            unauthorized = expect(
                client.post(
                    "/v1/chat/completions",
                    json={"model": "luna", "messages": [{"role": "user", "content": "x"}]},
                ),
                401,
                "未认证",
            )
            openai_error(unauthorized, "invalid_api_key")
            assert "bearer" in unauthorized.headers.get("www-authenticate", "").lower()
            passed.append("401 OpenAI 错误结构")

            # 8) 500 错误结构
            failed = expect(
                client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "model": "luna",
                        "user": "boom-user",
                        "messages": [{"role": "user", "content": "boom"}],
                    },
                ),
                500,
                "上游失败",
            )
            openai_error(failed, "provider_unavailable")
            passed.append("500 OpenAI 错误结构")

            # 9) 400 输入错误结构
            bad = expect(
                client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={"model": "luna", "messages": []},
                ),
                400,
                "空消息",
            )
            openai_error(bad, "invalid_messages")
            passed.append("400 OpenAI 错误结构")

            # 10) 429 限流；/v1 中间件只做 RPM，配额由端点检查
            limits.update_config(
                {
                    "user_rpm": 1,
                    "user_rpd": 0,
                    "ip_rpm": 0,
                    "ip_rpd": 0,
                    "platform_daily_tokens": 0,
                }
            )
            rate_headers = {"Authorization": "Bearer rate-key"}
            expect(
                client.post(
                    "/v1/chat/completions",
                    headers=rate_headers,
                    json={
                        "model": "luna",
                        "messages": [{"role": "user", "content": "第一次"}],
                    },
                ),
                200,
                "限流前",
            )
            limited = expect(
                client.post(
                    "/v1/chat/completions",
                    headers=rate_headers,
                    json={
                        "model": "luna",
                        "messages": [{"role": "user", "content": "第二次"}],
                    },
                ),
                429,
                "限流",
            )
            openai_error(limited, "rate_limited")
            assert limited.headers.get("retry-after")
            passed.append("429 + Retry-After")

            # 11) 平台配额按本次实际主路由检查，端点 429
            limits.update_config(
                {
                    "user_rpm": 0,
                    "user_rpd": 0,
                    "ip_rpm": 0,
                    "ip_rpd": 0,
                    "platform_daily_tokens": 1,
                }
            )
            limits.record_usage(
                "alice",
                TEST_ROUTE,
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )
            quota = expect(
                client.post(
                    "/v1/chat/completions",
                    headers=auth,
                    json={
                        "model": "luna",
                        "messages": [{"role": "user", "content": "配额"}],
                    },
                ),
                429,
                "平台配额",
            )
            openai_error(quota, "quota_exceeded")
            assert quota.headers.get("retry-after")
            passed.append("平台配额按实际主路由检查")

        print(
            json.dumps(
                {
                    "ok": True,
                    "checks": passed,
                    "chat_log_turns": len(upstream.chat_log_store.turns),
                    "persisted_agents": len(upstream.persisted),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        limits.close()
        store.close()
        server.should_exit = True
        thread.join(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
