#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#66 运行时验收：注册、登录、刷新、双用户会话/历史/记忆隔离。

前置：
    ALPHA_REQUIRE_INVITE=1 python -m shell.app
    ALPHA_INVITE_CODE=... python scripts/dev/verify_auth.py

如果邀请码没通过环境变量传入，脚本会尝试在本地 auth.db 生成一个 2 次邀请码。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = os.getenv("ALPHA_BASE", "http://127.0.0.1:8000")
DATA_DIR = Path(os.getenv("ALPHA_DATA_DIR", str(ROOT / ".data")))
AUTH_DB = Path(os.getenv("ALPHA_AUTH_DB", str(DATA_DIR / "auth.db")))
OPENHER_DB = Path(
    os.getenv("ALPHA_OPENHER_DB", str(ROOT / "vendor" / "OpenHer" / ".data" / "openher.db"))
)
CHAT_DB = Path(
    os.getenv("ALPHA_CHAT_DB", str(ROOT / "vendor" / "OpenHer" / ".data" / "chat.db"))
)
EXTRA_DB = Path(
    os.getenv("ALPHA_AGENT_EXTRA_DB", str(DATA_DIR / "agent_extra.db"))
)
OUT = Path(os.getenv("ALPHA_AUTH_OUT", "/tmp/verify-auth66.json"))

results: list[dict] = []


def record(name: str, ok: bool | None, detail="") -> None:
    results.append({"check": name, "ok": ok, "detail": detail})
    mark = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    print(f"{mark} {name}" + (f" | {detail}" if detail else ""), flush=True)


def _invite() -> str:
    code = os.getenv("ALPHA_INVITE_CODE", "").strip()
    if code:
        return code
    from shell.auth.store import AuthStore

    store = AuthStore(AUTH_DB)
    try:
        code = "ALPHA-" + "".join(
            secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10)
        )
        store.add_invite(code, label="verify-auth66", max_uses=2)
        print(f"local invite generated: {code}", flush=True)
        return code
    finally:
        store.close()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: httpx.Client, email: str, password: str, invite: str) -> dict:
    response = client.post(
        BASE + "/api/auth/register",
        json={"email": email, "password": password, "invite_code": invite},
    )
    if response.status_code == 409:
        response = client.post(
            BASE + "/api/auth/login",
            json={"email": email, "password": password},
        )
    response.raise_for_status()
    return response.json()


def _chat(
    client: httpx.Client,
    token: str,
    persona: str,
    message: str,
    client_id: str,
    session_id: str | None = None,
) -> tuple[int, dict]:
    payload = {
        "message": message,
        "persona_id": persona,
        "client_id": client_id,
    }
    if session_id:
        payload["session_id"] = session_id
    response = client.post(BASE + "/api/chat", json=payload, headers=_auth(token))
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:300]}
    return response.status_code, body


def _history(client: httpx.Client, token: str, persona: str, client_id: str) -> dict:
    response = client.get(
        BASE + f"/api/chat/history/{persona}",
        params={"client_id": client_id, "limit": 100},
        headers=_auth(token),
    )
    response.raise_for_status()
    return response.json()


def _db_user_ids(path: Path, persona: str) -> set[str]:
    if not path.exists():
        return set()
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM genome_state WHERE persona_id = ?",
                (persona,),
            ).fetchall()
        return {row[0] for row in rows}
    except sqlite3.Error:
        return set()


def _db_extra_user_ids(path: Path, persona: str) -> set[str]:
    if not path.exists():
        return set()
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM agent_extra WHERE persona_id = ?",
                (persona,),
            ).fetchall()
        return {row[0] for row in rows}
    except sqlite3.Error:
        return set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", default=os.getenv("ALPHA_PERSONA", "luna"))
    parser.add_argument("--invite", default="")
    args = parser.parse_args(argv)

    if args.invite:
        os.environ["ALPHA_INVITE_CODE"] = args.invite
    invite = _invite()
    run = f"{int(time.time())}-{secrets.token_hex(2)}"
    password = "Auth66-" + secrets.token_urlsafe(8)
    persona = args.persona
    client = httpx.Client(timeout=180)
    try:
        status = client.get(BASE + "/api/status")
        record("app /api/status", status.status_code == 200, status.status_code)
        if status.status_code != 200:
            return 1

        anon = client.get(BASE + "/api/me/routing")
        record("unauthenticated config is 401", anon.status_code == 401, anon.status_code)

        a = _register(client, f"auth66-{run}-a@example.test", password, invite)
        b = _register(client, f"auth66-{run}-b@example.test", password, invite)
        token_a, token_b = a["access_token"], b["access_token"]
        user_a, user_b = a["user"]["id"], b["user"]["id"]
        record("register two users", user_a != user_b, f"{user_a} / {user_b}")

        me_a = client.get(BASE + "/api/auth/me", headers=_auth(token_a))
        record(
            "access token identifies user A",
            me_a.status_code == 200 and me_a.json()["user"]["id"] == user_a,
            me_a.status_code,
        )

        login = client.post(
            BASE + "/api/auth/login",
            json={"email": f"auth66-{run}-a@example.test", "password": password},
        )
        record("login returns token pair", login.status_code == 200, login.status_code)
        refresh = client.post(
            BASE + "/api/auth/refresh",
            json={"refresh_token": login.json()["refresh_token"]},
        )
        record("refresh rotates refresh token", refresh.status_code == 200, refresh.status_code)
        old_refresh = client.post(
            BASE + "/api/auth/refresh",
            json={"refresh_token": login.json()["refresh_token"]},
        )
        record("old refresh token rejected", old_refresh.status_code == 401, old_refresh.status_code)

        logout = client.post(
            BASE + "/api/auth/logout",
            json={"refresh_token": login.json()["refresh_token"]},
            headers=_auth(login.json()["access_token"]),
        )
        record("logout accepts access/refresh", logout.status_code == 200, logout.status_code)

        # 并发聊天：两个用户同一 persona
        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(
                _chat,
                client,
                token_a,
                persona,
                f"A secret {run}",
                f"auth66-{run}-a-web",
            )
            fb = pool.submit(
                _chat,
                client,
                token_b,
                persona,
                f"B secret {run}",
                f"auth66-{run}-b-web",
            )
            status_a, body_a = fa.result()
            status_b, body_b = fb.result()

        if status_a == 409 and body_a.get("error") == "route_not_configured":
            record("concurrent chat", None, "route_not_configured；先配置模型")
            record("cross-user session isolation", None, "需要可用的 chat 路由")
            record("history isolation", None, "需要可用的 chat 路由")
            record("memory isolation (openher.db)", None, "需要可用的 chat 路由")
            return _finish()

        record(
            "concurrent chat A",
            status_a == 200,
            body_a.get("session_id") or body_a,
        )
        record(
            "concurrent chat B",
            status_b == 200,
            body_b.get("session_id") or body_b,
        )
        sid_a = body_a.get("session_id", "")
        sid_b = body_b.get("session_id", "")
        record(
            "session ids differ",
            bool(sid_a and sid_b and sid_a != sid_b),
            f"{sid_a} / {sid_b}",
        )

        cross = client.get(BASE + f"/api/session/{sid_a}/status", headers=_auth(token_b))
        record("B cannot read A session status", cross.status_code == 404, cross.status_code)

        hist_a = _history(client, token_a, persona, f"auth66-{run}-a-web")
        hist_b = _history(client, token_b, persona, f"auth66-{run}-b-web")
        hist_b_with_a_client = _history(client, token_b, persona, f"auth66-{run}-a-web")
        text_a = json.dumps(hist_a, ensure_ascii=False)
        text_b = json.dumps(hist_b, ensure_ascii=False)
        text_b_cross = json.dumps(hist_b_with_a_client, ensure_ascii=False)
        record(
            "history A sees A only",
            f"A secret {run}" in text_a and f"B secret {run}" not in text_a,
            f"messages={len(hist_a.get('messages', []))}",
        )
        record(
            "history B cannot read A client_id",
            f"A secret {run}" not in text_b_cross,
            f"messages={len(hist_b_with_a_client.get('messages', []))}",
        )
        record(
            "history B sees B only",
            f"B secret {run}" in text_b and f"A secret {run}" not in text_b,
            f"messages={len(hist_b.get('messages', []))}",
        )

        openher_ids = _db_user_ids(OPENHER_DB, persona)
        extra_ids = _db_extra_user_ids(EXTRA_DB, persona)
        record(
            "memory rows keyed by auth user",
            user_a in openher_ids and user_b in openher_ids,
            f"openher={len(openher_ids)} extra={len(extra_ids)}",
        )
        record(
            "relationship side-state keyed by auth user",
            user_a in extra_ids and user_b in extra_ids,
            f"extra={len(extra_ids)}",
        )

        # 聊天历史落盘也应带用户命名空间
        if CHAT_DB.exists():
            try:
                with sqlite3.connect(CHAT_DB) as conn:
                    clients = {
                        row[0]
                        for row in conn.execute(
                            "SELECT DISTINCT client_id FROM chat_messages "
                            "WHERE persona_id = ? AND content LIKE ?",
                            (persona, f"%{run}%"),
                        ).fetchall()
                    }
                record(
                    "chat log client_id namespaced",
                    len(clients) >= 2
                    and all(
                        c.startswith("u") and ":" in c
                        for c in clients
                    ),
                    sorted(clients)[:3],
                )
            except sqlite3.Error as exc:
                record("chat log client_id namespaced", None, type(exc).__name__)
        else:
            record("chat log client_id namespaced", None, "chat.db 不存在")

        return _finish()
    finally:
        client.close()


def _finish() -> int:
    OUT.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failed = [r for r in results if r["ok"] is False]
    skipped = [r for r in results if r["ok"] is None]
    print(
        f"\n{len(results) - len(failed) - len(skipped)} PASS, "
        f"{len(failed)} FAIL, {len(skipped)} SKIP -> {OUT}",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
