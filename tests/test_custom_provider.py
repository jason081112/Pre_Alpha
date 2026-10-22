"""自定义 provider 的零依赖测试。

    python3 tests/test_custom_provider.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.custom import CustomProviderStore, validate_base_url  # noqa: E402
from shell.providers import ProviderSpec, Registry, Route, RouteError  # noqa: E402
from shell.routing import RoutingLLMClient, routing_context  # noqa: E402
from shell.store import RoutingStore  # noqa: E402


class FakeCipher:
    """不依赖 cryptography 的测试替身（带 AAD 绑定）。"""

    def encrypt(self, value: str, aad: str = "") -> str:
        return "enc:" + aad + "|" + value[::-1]

    def decrypt(self, token: str, aad: str = "") -> str:
        assert token.startswith("enc:")
        stored_aad, _, payload = token[4:].partition("|")
        if stored_aad != aad:
            raise ValueError("AAD 不匹配")
        return payload[::-1]


class RecorderClient:
    def __init__(self, spec: ProviderSpec, route: Route, seen: list):
        self.spec = spec
        self.route = route
        self.seen = seen

    async def chat(self, *args, **kwargs):
        self.seen.append((self.spec.id, self.spec.base_url, self.spec.api_key))
        return f"ok:{self.spec.id}"

    async def chat_stream(self, *args, **kwargs):
        self.seen.append((self.spec.id, self.spec.base_url, self.spec.api_key))
        yield f"ok:{self.spec.id}"


def test_validate_base_url() -> None:
    assert validate_base_url("https://api.example.com/v1") == "https://api.example.com/v1"
    assert validate_base_url("http://localhost:11434/v1") == "http://localhost:11434/v1"
    assert validate_base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"

    for bad in (
        "http://api.example.com/v1",
        "file:///etc/passwd",
        "https://user:pass@example.com/v1",
        "https://10.0.0.1/v1",
        "https://192.168.1.5/v1",
    ):
        try:
            validate_base_url(bad)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝: {bad}")


def test_store_roundtrip_and_masking() -> None:
    store = CustomProviderStore(":memory:", FakeCipher())
    provider = store.add(
        "u1", "我的中转", "https://relay.example.com/v1", "sk-secret", ["gpt-x"]
    )
    assert provider.id.startswith("custom-")
    assert provider.has_key

    plain = store.get(provider.id, "u1", decrypt=False)
    assert plain is not None and plain.api_key == ""
    assert "sk-secret" not in str(plain.to_public())

    secret = store.get(provider.id, "u1", decrypt=True)
    assert secret is not None and secret.api_key == "sk-secret"

    assert store.get(provider.id, "u2") is None
    assert store.delete(provider.id, "u2") is False
    assert store.delete(provider.id, "u1") is True
    assert store.get(provider.id, "u1") is None
    store.close()


def test_add_rejects_bad_input() -> None:
    store = CustomProviderStore(":memory:", FakeCipher())
    for kwargs in (
        {"label": "", "base_url": "https://x.example.com/v1", "api_key": "k"},
        {"label": "x", "base_url": "ftp://x.example.com", "api_key": "k"},
        {"label": "x", "base_url": "https://x.example.com/v1", "api_key": ""},
    ):
        try:
            store.add("u1", **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝: {kwargs}")
    store.close()


def test_route_uses_custom_provider_and_key() -> None:
    store = CustomProviderStore(":memory:", FakeCipher())
    provider = store.add(
        "u1", "relay", "https://relay.example.com/v1", "sk-secret", ["gpt-x"]
    )
    seen: list = []
    platform = Registry(
        {"openai": ProviderSpec("openai", "openai", "", "OPENAI_API_KEY", ("gpt-4o",), ("zh",))}
    )
    client = RoutingLLMClient(
        registry=platform,
        custom_store=store,
        client_factory=lambda spec, route, t, m: RecorderClient(spec, route, seen),
    )

    async def run():
        with routing_context(Route(provider.id, "gpt-x"), user="u1"):
            return await client.chat([])

    assert asyncio.run(run()) == f"ok:{provider.id}"
    assert seen == [(provider.id, "https://relay.example.com/v1", "sk-secret")]
    store.close()


def test_custom_provider_is_user_scoped() -> None:
    store = CustomProviderStore(":memory:", FakeCipher())
    provider = store.add(
        "u1", "relay", "https://relay.example.com/v1", "sk-secret", ["gpt-x"]
    )
    registry = Registry(
        {"openai": ProviderSpec("openai", "openai", "", "OPENAI_API_KEY", ("gpt-4o",), ("zh",))}
    )
    client = RoutingLLMClient(registry=registry, custom_store=store)

    async def run():
        with routing_context(Route(provider.id, "gpt-x"), user="u2"):
            try:
                await client.chat([])
            except RouteError:
                return True
            return False

    assert asyncio.run(run()) is True
    store.close()


def test_routing_store_can_reference_custom_id() -> None:
    tmp = Path(tempfile.mkdtemp()) / "routing.db"
    routing = RoutingStore(tmp)
    custom = CustomProviderStore(tmp, FakeCipher())
    provider = custom.add("u1", "relay", "https://relay.example.com/v1", "sk", ["gpt-x"])

    routing.put("u1", Route(provider.id, "gpt-x"), [])
    record = routing.get("u1")
    assert record is not None and record.route.provider == provider.id
    routing.close()
    custom.close()


def test_ciphertext_cannot_be_swapped_between_rows() -> None:
    store = CustomProviderStore(":memory:", FakeCipher())
    first = store.add("u1", "a", "https://a.example.com/v1", "sk-a", ["m"])
    second = store.add("u1", "b", "https://b.example.com/v1", "sk-b", ["m"])

    with store._lock:
        store._conn.execute(
            "UPDATE user_provider SET api_key_enc = ? WHERE id = ?",
            (second.api_key_enc, first.id),
        )
        store._conn.commit()

    try:
        store.get(first.id, "u1", decrypt=True)
    except ValueError:
        pass
    else:
        raise AssertionError("密文搬运应被 AAD 拒绝")
    store.close()


def test_master_key_passphrase_is_stable() -> None:
    from shell.crypto import load_master_key

    data_dir = Path(tempfile.mkdtemp())
    os.environ["ALPHA_MASTER_KEY"] = "correct horse battery staple"
    try:
        first = load_master_key(data_dir)
        second = load_master_key(data_dir)
    finally:
        os.environ.pop("ALPHA_MASTER_KEY", None)
    assert first == second and len(first) == 32


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"✓ {test.__name__}")
    print(f"custom provider ok ({len(tests)} tests)")
