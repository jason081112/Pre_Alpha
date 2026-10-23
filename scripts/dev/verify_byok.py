"""#71 BYOK Key 生命周期运行时验收。

前置：
    python scripts/dev/mock_provider.py
    python -m shell.app

运行：
    python scripts/dev/verify_byok.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import httpx

BASE = os.getenv("ALPHA_BASE", "http://127.0.0.1:8000")
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("ALPHA_DATA_DIR", str(ROOT / ".data")))
DB = DATA_DIR / "routing.db"
OUT = Path(os.getenv("ALPHA_BYOK_OUT", "/tmp/verify-byok.json"))
LOG = Path(os.getenv("ALPHA_APP_LOG", "/tmp/app.log"))
USER = f"byok-{int(time.time())}"
KEYS = ("sk-byok-1111", "sk-byok-2222", "sk-byok-3333")
results: list[dict] = []


def record(name: str, ok: bool, detail="") -> None:
    results.append({"check": name, "ok": ok, "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail) if detail else ""), flush=True)


def main() -> int:
    client = httpx.Client(timeout=120)
    headers = {"X-Alpha-User": USER}

    status = client.get(BASE + "/api/status")
    record("app /api/status", status.status_code == 200, status.status_code)

    # 1. 创建
    r = client.post(
        BASE + "/api/me/providers",
        json={
            "label": "byok mock",
            "base_url": "http://127.0.0.1:9999/v1",
            "api_key": KEYS[0],
            "models": ["mock-ok"],
            "probe": True,
        },
        headers=headers,
    )
    body = r.json()
    provider_id = body.get("id")
    record(
        "create provider",
        r.status_code == 200
        and body.get("key_last4") == "1111"
        and body.get("key_version") == 1
        and body.get("revoked") is False,
        {k: body.get(k) for k in ("id", "key_last4", "key_version", "revoked", "has_key")},
    )
    record("create response hides key", KEYS[0] not in r.text, r.text[:120])

    # 2. 列表脱敏
    listing = client.get(BASE + "/api/me/providers", headers=headers).text
    record(
        "list shows last4 only",
        KEYS[0] not in listing and '"key_last4":"1111"' in listing,
        listing[:160],
    )

    # 3. 落盘检查
    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            "SELECT api_key_enc, key_last4, key_version, revoked_at "
            "FROM user_provider WHERE id = ?",
            (provider_id,),
        ).fetchone()
    record(
        "ciphertext at rest",
        bool(row)
        and row[0].startswith("v1:")
        and KEYS[0] not in row[0]
        and row[1] == "1111"
        and row[2] == 1,
        (row[0][:20] + "...", row[1], row[2]) if row else None,
    )

    # 4. 路由 + 对话
    r = client.put(
        BASE + "/api/me/routing",
        json={"provider": provider_id, "model": "mock-ok", "fallback": []},
        headers=headers,
    )
    record("route to custom", r.status_code == 200, r.status_code)
    r = client.post(
        BASE + "/api/chat",
        json={"message": "hello", "persona_id": "luna", "user_name": USER},
        headers=headers,
    )
    record(
        "chat uses rotated key #1",
        r.status_code == 200 and "mock reply" in str(r.json().get("response")),
        {k: r.json().get(k) for k in ("route_used", "response")} if r.status_code == 200 else r.text[:120],
    )

    # 5. 只改元数据，Key/版本不变
    r = client.patch(
        BASE + f"/api/me/providers/{provider_id}",
        json={"label": "byok renamed"},
        headers=headers,
    )
    body = r.json()
    record(
        "metadata update keeps key version",
        r.status_code == 200
        and body.get("label") == "byok renamed"
        and body.get("key_version") == 1
        and body.get("key_last4") == "1111",
        {k: body.get(k) for k in ("label", "key_version", "key_last4")},
    )

    # 6. 轮换 Key
    r = client.patch(
        BASE + f"/api/me/providers/{provider_id}",
        json={"api_key": KEYS[1], "probe": True},
        headers=headers,
    )
    body = r.json()
    record(
        "rotate key increments version",
        r.status_code == 200
        and body.get("key_version") == 2
        and body.get("key_last4") == "2222"
        and KEYS[1] not in r.text,
        {k: body.get(k) for k in ("key_version", "key_last4", "revoked")},
    )
    with sqlite3.connect(DB) as conn:
        rotated_enc = conn.execute(
            "SELECT api_key_enc FROM user_provider WHERE id = ?", (provider_id,)
        ).fetchone()[0]
    record(
        "rotated ciphertext changed",
        rotated_enc.startswith("v1:") and KEYS[1] not in rotated_enc,
        rotated_enc[:20] + "...",
    )
    r = client.post(
        BASE + "/api/chat",
        json={"message": "after rotate", "persona_id": "luna", "user_name": USER},
        headers=headers,
    )
    record("chat uses rotated key #2", r.status_code == 200, r.status_code)

    # 7. 撤销
    r = client.post(BASE + f"/api/me/providers/{provider_id}/revoke", headers=headers)
    body = r.json()
    record(
        "revoke clears key",
        r.status_code == 200
        and body.get("revoked") is True
        and body.get("has_key") is False
        and body.get("key_version") == 3
        and body.get("key_last4") == "",
        {k: body.get(k) for k in ("revoked", "has_key", "key_version", "key_last4")},
    )
    with sqlite3.connect(DB) as conn:
        revoked = conn.execute(
            "SELECT api_key_enc, revoked_at FROM user_provider WHERE id = ?",
            (provider_id,),
        ).fetchone()
    record(
        "revoked row has no ciphertext",
        revoked[0] == "" and revoked[1] is not None,
        (revoked[0], revoked[1]),
    )

    # 8. 撤销后对话应 409 + 可操作提示
    r = client.post(
        BASE + "/api/chat",
        json={"message": "after revoke", "persona_id": "luna", "user_name": USER},
        headers=headers,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    record(
        "revoked chat returns actionable 409",
        r.status_code == 409
        and body.get("action") == "reconfigure_provider"
        and "撤销" in str(body.get("detail"))
        and "重新配置" in str(body.get("detail")),
        {"status": r.status_code, "body": body},
    )

    # 9. 撤销后不能再把它设为当前路由
    r = client.put(
        BASE + "/api/me/routing",
        json={"provider": provider_id, "model": "mock-ok", "fallback": []},
        headers=headers,
    )
    record("revoked provider cannot be routed", r.status_code == 409, r.text[:120])

    # 10. 重新配置后恢复
    r = client.patch(
        BASE + f"/api/me/providers/{provider_id}",
        json={"api_key": KEYS[2], "probe": True},
        headers=headers,
    )
    body = r.json()
    record(
        "reactivate by rotation",
        r.status_code == 200
        and body.get("revoked") is False
        and body.get("has_key") is True
        and body.get("key_version") == 4
        and body.get("key_last4") == "3333",
        {k: body.get(k) for k in ("revoked", "has_key", "key_version", "key_last4")},
    )
    r = client.post(
        BASE + "/api/chat",
        json={"message": "after reactivate", "persona_id": "luna", "user_name": USER},
        headers=headers,
    )
    record("chat after reactivate", r.status_code == 200, r.status_code)

    # 11. 审计只含事件
    r = client.get(BASE + f"/api/me/providers/{provider_id}/audit", headers=headers)
    body = r.json()
    events = [event["event"] for event in body.get("events", [])]
    record(
        "audit records lifecycle",
        r.status_code == 200
        and events == ["created", "updated", "rotated", "revoked", "rotated"]
        and not any(key in r.text for key in KEYS),
        events,
    )

    # 12. 使用中删除保护 -> 切走后删除
    r = client.delete(BASE + f"/api/me/providers/{provider_id}", headers=headers)
    record("delete blocked while routed", r.status_code == 409, r.status_code)
    client.put(
        BASE + "/api/me/routing",
        json={"provider": "ollama-cloud", "model": "gemma4:31b", "fallback": []},
        headers=headers,
    )
    r = client.delete(BASE + f"/api/me/providers/{provider_id}", headers=headers)
    record(
        "delete after switch",
        r.status_code == 200 and r.json().get("removed") is True,
        r.text[:100],
    )

    # 13. 日志不含明文 Key
    if LOG.exists():
        log_text = LOG.read_text(errors="ignore")
        record(
            "app log has no plaintext key",
            not any(key in log_text for key in KEYS),
            f"{LOG} ({len(log_text)} bytes)",
        )
    else:
        record("app log has no plaintext key", False, f"missing {LOG}")

    ok = all(item["ok"] for item in results)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n{'ALL PASS' if ok else 'HAS FAILURES'} "
        f"({sum(1 for item in results if item['ok'])}/{len(results)})",
        flush=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
