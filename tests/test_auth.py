# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#66 认证与隔离的最小零依赖测试。

不依赖 FastAPI；HTTP/WS 中间件用假 ASGI 调用验证。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.auth.middleware import AuthMiddleware  # noqa: E402
from shell.auth.scope import (  # noqa: E402
    ScopedChatLog,
    current_auth_scope_enabled,
    current_auth_user,
    new_session_id,
    prepare_session_id,
    scoped_client_id,
    session_prefix,
    validate_session_id,
)
from shell.auth.service import (  # noqa: E402
    AuthError,
    AuthService,
    hash_password,
    verify_password,
)
from shell.auth.store import AuthStore  # noqa: E402


def _service(store: AuthStore | None = None, **kwargs) -> AuthService:
    store = store or AuthStore(":memory:")
    params = {
        "access_ttl": 60,
        "refresh_ttl": 3600,
        "require_invite": True,
    }
    params.update(kwargs)
    return AuthService(store, **params)


def _register(service: AuthService, email: str, invite: str = "INVITE-1"):
    return service.register(email, "password123", invite)


def _expect_error(code: str, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except AuthError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}"
        return
    raise AssertionError(f"expected AuthError {code}")


def test_password_hash_roundtrip() -> None:
    encoded = hash_password("correct horse battery staple")
    assert "correct horse" not in encoded
    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("x", "not-a-valid-hash")


def test_register_login_refresh_logout() -> None:
    store = AuthStore(":memory:")
    store.add_invite("INVITE-1", label="test", max_uses=2)
    service = _service(store)

    bundle = _register(service, "A@Example.com ")
    assert bundle.user.email == "a@example.com"
    assert service.authenticate(bundle.access_token).id == bundle.user.id
    assert store.get_invite("INVITE-1").used_count == 1

    _expect_error("email_taken", _register, service, "a@example.com")
    _expect_error("invalid_credentials", service.login, "a@example.com", "bad-password")

    login = service.login("A@example.com", "password123")
    refreshed = service.refresh(login.refresh_token)
    assert refreshed.user.id == bundle.user.id
    assert service.authenticate(refreshed.access_token).id == bundle.user.id
    # refresh 轮换后旧 refresh 不能重放
    _expect_error("invalid_refresh", service.refresh, login.refresh_token)

    service.logout(
        access_token=refreshed.access_token,
        refresh_token=refreshed.refresh_token,
    )
    assert service.authenticate(refreshed.access_token) is None
    _expect_error("invalid_refresh", service.refresh, refreshed.refresh_token)


def test_invite_required_and_limits() -> None:
    store = AuthStore(":memory:")
    service = _service(store)
    _expect_error("invite_required", _register, service, "a@example.com", "")
    _expect_error("invite_invalid", _register, service, "a@example.com", "NOPE")

    store.add_invite("ONE-USE", max_uses=1)
    _register(service, "a@example.com", "ONE-USE")
    _expect_error("invite_invalid", _register, service, "b@example.com", "ONE-USE")

    store.add_invite("REVOKED")
    store.revoke_invite("REVOKED")
    _expect_error("invite_invalid", _register, service, "c@example.com", "REVOKED")


def test_expiry_and_clock() -> None:
    now = [1000.0]
    store = AuthStore(":memory:")
    store.add_invite("INVITE-1")
    service = _service(
        store,
        access_ttl=10,
        refresh_ttl=20,
        clock=lambda: now[0],
    )
    bundle = _register(service, "a@example.com")
    assert service.authenticate(bundle.access_token) is not None

    now[0] = 1011
    assert service.authenticate(bundle.access_token) is None
    refreshed = service.refresh(bundle.refresh_token)
    assert service.authenticate(refreshed.access_token) is not None

    now[0] = 1032
    assert service.authenticate(refreshed.access_token) is None
    _expect_error("invalid_refresh", service.refresh, refreshed.refresh_token)


def test_identity_layer_ready_for_phone() -> None:
    store = AuthStore(":memory:")
    user = store.create_user_with_identity(
        kind="phone",
        identifier="+8613800000000",
        normalized="+8613800000000",
        password_hash=hash_password("password123"),
    )
    assert user.phone == "+8613800000000"
    assert user.email is None
    identity = store.get_identity("phone", "+8613800000000")
    assert identity is not None and identity.user.id == user.id
    assert verify_password("password123", identity.password_hash)


def test_two_users_scope_isolation() -> None:
    store = AuthStore(":memory:")
    store.add_invite("INVITE-1", max_uses=2)
    service = _service(store)
    a = _register(service, "a@example.com")
    b = _register(service, "b@example.com")

    assert a.user.id != b.user.id
    assert scoped_client_id(a.user.id, "same-client") != scoped_client_id(
        b.user.id, "same-client"
    )

    sid_a = prepare_session_id(None, a.user.id)
    sid_b = prepare_session_id(None, b.user.id)
    assert sid_a.startswith(session_prefix(a.user.id) + ".")
    assert sid_b.startswith(session_prefix(b.user.id) + ".")
    assert validate_session_id(sid_a, a.user.id) == sid_a
    try:
        validate_session_id(sid_a, b.user.id)
    except ValueError:
        pass
    else:
        raise AssertionError("B 不应能用 A 的 session_id")

    class FakeLog:
        def __init__(self):
            self.rows: dict[str, list[str]] = {}

        def save_turn(
            self,
            client_id,
            persona_id,
            user_msg,
            agent_reply,
            modality="文字",
            image_url=None,
        ):
            self.rows.setdefault(client_id, []).extend([user_msg, agent_reply])

        def save_message(self, **kwargs):
            raise AssertionError("not used")

        def load_messages(self, client_id, persona_id, limit=50, before_id=None):
            return self.rows.get(client_id, [])

        def count_messages(self, client_id, persona_id):
            return len(self.rows.get(client_id, []))

        def close(self):
            pass

    inner = FakeLog()
    proxy = ScopedChatLog(inner)
    token = current_auth_user.set(a.user.id)
    try:
        proxy.save_turn("same-client", "luna", "A 的秘密", "收到")
        assert inner.rows[scoped_client_id(a.user.id, "same-client")] == [
            "A 的秘密",
            "收到",
        ]
        assert proxy.load_messages("same-client", "luna") == ["A 的秘密", "收到"]
    finally:
        current_auth_user.reset(token)

    token = current_auth_user.set(b.user.id)
    try:
        assert proxy.load_messages("same-client", "luna") == []
        proxy.save_turn("same-client", "luna", "B 的消息", "收到")
        assert proxy.load_messages("same-client", "luna") == ["B 的消息", "收到"]
        assert "A 的秘密" not in str(inner.rows[scoped_client_id(b.user.id, "same-client")])
    finally:
        current_auth_user.reset(token)


class _Sink:
    def __init__(self):
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


def _http_scope(path: str, headers: dict[str, str]) -> dict:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in headers.items()
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "state": {},
    }


def test_middleware_http_401_and_identity() -> None:
    store = AuthStore(":memory:")
    store.add_invite("INVITE-1")
    service = _service(store)
    bundle = _register(service, "a@example.com")

    async def run() -> None:
        seen: list[dict] = []

        async def app(scope, receive, send):
            seen.append(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = AuthMiddleware(app, service, required=True)

        sink = _Sink()
        await middleware(_http_scope("/api/chat", {}), None, sink)
        assert sink.messages[0]["status"] == 401
        body = json.loads(sink.messages[1]["body"])
        assert body["error"] == "unauthorized"

        sink = _Sink()
        await middleware(
            _http_scope(
                "/api/chat",
                {"Authorization": f"Bearer {bundle.access_token}"},
            ),
            None,
            sink,
        )
        assert sink.messages[0]["status"] == 200
        assert seen[-1]["state"]["user_id"] == bundle.user.id
        assert any(
            key == b"x-alpha-user" and value.decode() == bundle.user.id
            for key, value in seen[-1]["headers"]
        )

        # 公共路径不能信任客户端伪造的 X-Alpha-User
        sink = _Sink()
        await middleware(
            _http_scope("/api/personas", {"X-Alpha-User": "attacker"}),
            None,
            sink,
        )
        assert sink.messages[0]["status"] == 200
        assert all(key != b"x-alpha-user" for key, _ in seen[-1]["headers"])

    asyncio.run(run())


def test_middleware_session_owner_guard() -> None:
    store = AuthStore(":memory:")
    store.add_invite("INVITE-1", max_uses=2)
    service = _service(store)
    a = _register(service, "a@example.com")
    b = _register(service, "b@example.com")
    sid_a = prepare_session_id(None, a.user.id)
    sid_b = prepare_session_id(None, b.user.id)

    async def run() -> None:
        seen: list[dict] = []

        async def app(scope, receive, send):
            seen.append(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = AuthMiddleware(app, service, required=True)

        sink = _Sink()
        await middleware(
            _http_scope(
                f"/api/session/{sid_a}/status",
                {"Authorization": f"Bearer {b.access_token}"},
            ),
            None,
            sink,
        )
        assert sink.messages[0]["status"] == 404
        assert seen == []

        sink = _Sink()
        await middleware(
            _http_scope(
                f"/api/session/{sid_a}/status",
                {"Authorization": f"Bearer {a.access_token}"},
            ),
            None,
            sink,
        )
        assert sink.messages[0]["status"] == 200
        assert seen[-1]["state"]["user_id"] == a.user.id

        sink = _Sink()
        await middleware(
            _http_scope(
                f"/api/session/{sid_b}/status",
                {"Authorization": f"Bearer {b.access_token}"},
            ),
            None,
            sink,
        )
        assert sink.messages[0]["status"] == 200

    asyncio.run(run())


def test_middleware_ws_closes_unauthenticated() -> None:
    sink = _Sink()

    async def app(scope, receive, send):
        raise AssertionError("未认证 WS 不应进入应用")

    middleware = AuthMiddleware(app, _service(), required=True)
    scope = {
        "type": "websocket",
        "path": "/ws/chat",
        "query_string": b"",
        "headers": [],
        "state": {},
    }
    asyncio.run(middleware(scope, None, sink))
    assert sink.messages == [
        {"type": "websocket.close", "code": 1008, "reason": "unauthorized"}
    ]


def test_middleware_dev_mode_uses_header() -> None:
    async def app(scope, receive, send):
        assert scope["state"]["user_id"] == "local-dev"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = AuthMiddleware(app, _service(), required=False)
    sink = _Sink()
    asyncio.run(
        middleware(
            _http_scope("/api/chat", {"X-Alpha-User": "local-dev"}),
            None,
            sink,
        )
    )
    assert sink.messages[0]["status"] == 200


def test_dev_mode_disables_session_namespace() -> None:
    token = current_auth_scope_enabled.set(False)
    try:
        assert prepare_session_id("legacy-session", "verify-user") == "legacy-session"
        assert len(new_session_id("verify-user")) == 8
        assert scoped_client_id("verify-user", "client") == "client"
    finally:
        current_auth_scope_enabled.reset(token)


def test_local_session_keeps_legacy_shape() -> None:
    sid = new_session_id("local")
    assert len(sid) == 8
    assert validate_session_id("legacy123", "local") == "legacy123"
    assert scoped_client_id("local", "client") == "client"


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok {name}")
    print("auth ok")
