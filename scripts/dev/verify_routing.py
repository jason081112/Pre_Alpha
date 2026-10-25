"""#70 运行时集成验证。

前置：
    python scripts/dev/mock_provider.py
    ALPHA_ALLOW_PRIVATE_PROVIDERS=1 python -m shell.app

运行：
    python scripts/dev/verify_routing.py
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx

BASE = os.getenv("ALPHA_BASE", "http://127.0.0.1:8000")
ROOT = str(Path(__file__).resolve().parents[2])
DATA_DIR = Path(os.getenv("ALPHA_DATA_DIR", str(Path(ROOT) / ".data")))
USER = f"verify-{int(time.time())}"
MODEL = os.getenv("ALPHA_ROUTE_MODEL", "gemma4:31b")
OUT = Path(os.getenv("ALPHA_VERIFY_OUT", "/tmp/verify-routing.json"))
results = []


def record(name, ok, detail=""):
    results.append({"check": name, "ok": ok, "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail) if detail else ""), flush=True)


def main():
    client = httpx.Client(timeout=120)
    headers = {"X-Alpha-User": USER}

    # 0. 服务与角色
    status = client.get(BASE + "/api/status")
    record("app /api/status", status.status_code == 200, status.status_code)
    personas = client.get(BASE + "/api/personas").json()
    items = personas.get("personas", personas) if isinstance(personas, dict) else personas
    ids = [p.get("persona_id") or p.get("id") for p in items]
    persona = "luna" if "luna" in ids else ids[0]
    record("personas loaded", bool(ids), ids)

    # 1. 未配置路由 -> 409
    r = client.post(
        BASE + "/api/chat",
        json={"message": "hi", "persona_id": persona},
        headers={"X-Alpha-User": f"{USER}-empty"},
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    record("409 route_not_configured", r.status_code == 409 and body.get("error") == "route_not_configured", body)

    # 2. 创建自定义 provider（走真实 AES-GCM + /models 探活）
    r = client.post(
        BASE + "/api/me/providers",
        json={
            "label": "local mock",
            "base_url": "http://127.0.0.1:9999/v1",
            "api_key": "test-key-123",
            "models": ["mock-ok", "mock-500", "mock-cut"],
            "probe": True,
        },
        headers=headers,
    )
    record("create custom provider", r.status_code == 200, r.status_code)
    provider = r.json()
    cid = provider.get("id")

    # 3. 目录合并
    catalog = client.get(BASE + "/api/routes/catalog", headers=headers).json()
    pids = [p.get("id") for p in catalog.get("providers", [])]
    record("catalog merged", cid in pids and "ollama-cloud" in pids, pids)

    # 4. Key 不回显
    listing = client.get(BASE + "/api/me/providers", headers=headers).text
    record("key not echoed", "test-key-123" not in listing and provider.get("has_key") is True, listing[:120])

    db = sqlite3.connect(str(DATA_DIR / "routing.db"))
    enc = db.execute("SELECT api_key_enc FROM user_provider WHERE id=?", (cid,)).fetchone()[0]
    record("key encrypted at rest", enc.startswith("v1:") and "test-key-123" not in enc, enc[:24] + "...")

    # 5. 切到 mock
    def set_route(provider_id, model, fallback=None):
        r = client.put(
            BASE + "/api/me/routing",
            json={"provider": provider_id, "model": model, "fallback": fallback or []},
            headers=headers,
        )
        return r

    r = set_route(cid, "mock-ok")
    record("PUT routing custom", r.status_code == 200, r.text[:120])
    r = client.post(
        BASE + "/api/chat",
        json={"message": "hello", "persona_id": persona, "user_name": USER},
        headers=headers,
    )
    data = r.json()
    record(
        "chat via custom + usage",
        r.status_code == 200
        and data.get("route_used") == f"{cid}/mock-ok"
        and isinstance(data.get("usage"), dict)
        and data["usage"].get("total_tokens") == 15,
        {k: data.get(k) for k in ("route_used", "usage", "latency_ms", "response")},
    )
    record("response content", "mock reply" in str(data.get("response")), data.get("response"))

    # 6. 切回 ollama
    r = set_route("ollama-cloud", MODEL)
    record("PUT routing ollama", r.status_code == 200, r.text[:120])
    r = client.post(
        BASE + "/api/chat",
        json={"message": "用一句话介绍你自己。", "persona_id": persona, "user_name": USER},
        headers=headers,
    )
    data = r.json()
    record(
        "chat via ollama",
        r.status_code == 200 and data.get("route_used") == f"ollama-cloud/{MODEL}",
        {k: data.get(k) for k in ("route_used", "latency_ms", "usage")},
    )

    # 7. 500 -> 回退到 ollama
    r = set_route(cid, "mock-500", [{"provider": "ollama-cloud", "model": MODEL}])
    record("PUT routing 500+fallback", r.status_code == 200, r.text[:120])
    r = client.post(
        BASE + "/api/chat",
        json={"message": "fallback test", "persona_id": persona, "user_name": USER},
        headers=headers,
    )
    data = r.json()
    attempts = data.get("fallback_attempts") or []
    record(
        "fallback on 500",
        r.status_code == 200
        and data.get("route_used") == f"ollama-cloud/{MODEL}"
        and any("500" in str(a.get("error", "")) for a in attempts),
        {"route_used": data.get("route_used"), "attempts": attempts},
    )

    # 8. 连接失败 -> 回退
    r = client.post(
        BASE + "/api/me/providers",
        json={
            "label": "dead endpoint",
            "base_url": "http://127.0.0.1:9998/v1",
            "api_key": "dead-key",
            "models": ["dead-model"],
            "probe": False,
        },
        headers=headers,
    )
    dead = r.json().get("id")
    set_route(dead, "dead-model", [{"provider": "ollama-cloud", "model": MODEL}])
    r = client.post(
        BASE + "/api/chat",
        json={"message": "connect error test", "persona_id": persona, "user_name": USER},
        headers=headers,
    )
    data = r.json()
    record(
        "fallback on connect error",
        r.status_code == 200 and data.get("route_used") == f"ollama-cloud/{MODEL}",
        {"route_used": data.get("route_used"), "attempts": data.get("fallback_attempts")},
    )

    # 9. 首 token 后断流 -> 不重试
    sys.path.insert(0, ROOT)
    vendor = ROOT + "/vendor/OpenHer"
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    from shell.crypto import SecretBox
    from shell.custom import CustomProviderStore
    from shell.providers import Registry, Route
    from shell.routing import RoutingLLMClient, attempts as attempt_ctx, routing_context

    store = CustomProviderStore(str(DATA_DIR / "routing.db"), SecretBox.from_env(str(DATA_DIR)))
    router = RoutingLLMClient(registry=Registry.load(), custom_store=store)

    async def stream_probe():
        got = []
        error = None
        with routing_context(Route(cid, "mock-cut"), [], user=USER):
            try:
                async for chunk in router.chat_stream([]):
                    got.append(chunk)
            except Exception as exc:  # noqa: BLE001
                error = type(exc).__name__
            return got, error, list(attempt_ctx.get())

    got, error, attempts_ctx = asyncio.run(stream_probe())
    record(
        "no retry after first token",
        bool(got) and error is not None and len(attempts_ctx) == 1,
        {"chunks": got, "error": error, "attempts": attempts_ctx},
    )
    store.close()

    # 10. 删除保护 + 清理（当前路由是 dead，先删它应 409）
    r = client.delete(BASE + f"/api/me/providers/{dead}", headers=headers)
    record("delete blocked while in use", r.status_code == 409, r.status_code)
    set_route("ollama-cloud", MODEL)
    r = client.delete(BASE + f"/api/me/providers/{cid}", headers=headers)
    record("delete after switch", r.status_code == 200 and r.json().get("removed") is True, r.text[:80])
    client.delete(BASE + f"/api/me/providers/{dead}", headers=headers)

    ok = all(item["ok"] for item in results)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\n{'ALL PASS' if ok else 'HAS FAILURES'} ({sum(1 for i in results if i['ok'])}/{len(results)})", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
