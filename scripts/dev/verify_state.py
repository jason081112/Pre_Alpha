"""#69 引擎状态外置运行时验收。

流程：
    python scripts/dev/verify_state.py warm
    # 重启 shell.app
    python scripts/dev/verify_state.py resume
    python scripts/dev/verify_state.py metrics
    python scripts/dev/verify_state.py cleanup

覆盖：
- 两个用户、同一 persona 各 5 轮，state 不串
- 同一用户、luna / iris 交替各 5 轮，state 不串
- 冷启动后 turn_count、关系 EMA、驱力、聊天历史继续
- 同版本 CAS 双写只成功一次
- 单用户状态体积与 load_session 耗时
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor" / "OpenHer"
BASE = os.getenv("ALPHA_BASE", "http://127.0.0.1:8000")
DATA_DIR = Path(os.getenv("ALPHA_DATA_DIR", str(ROOT / ".data")))
OPENHER_DB = Path(
    os.getenv("ALPHA_OPENHER_DB", str(VENDOR / ".data" / "openher.db"))
)
EXTRA_DB = Path(
    os.getenv("ALPHA_AGENT_EXTRA_DB", str(DATA_DIR / "agent_extra.db"))
)
WARM_PATH = Path(os.getenv("ALPHA_STATE_WARM", "/tmp/state69-warm.json"))
RESUME_PATH = Path(os.getenv("ALPHA_STATE_RESUME", "/tmp/state69-resume.json"))
METRICS_PATH = Path(os.getenv("ALPHA_STATE_METRICS", "/tmp/state69-metrics.json"))
PERSONAS = ("luna", "iris")
ROUNDS = 5

results: list[dict] = []


def check(name: str, ok: bool, detail="") -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""), flush=True)


def headers(user: str) -> dict:
    return {"X-Alpha-User": user}


def build_sessions(run: str) -> list[dict]:
    a = f"state69-{run}-a"
    b = f"state69-{run}-b"
    c = f"state69-{run}-c"
    return [
        {"key": "a", "user": a, "persona": "luna", "sid": f"{a}-luna", "cid": f"{a}-web"},
        {"key": "b", "user": b, "persona": "luna", "sid": f"{b}-luna", "cid": f"{b}-web"},
        {"key": "c_luna", "user": c, "persona": "luna", "sid": f"{c}-luna", "cid": f"{c}-web"},
        {"key": "c_iris", "user": c, "persona": "iris", "sid": f"{c}-iris", "cid": f"{c}-web"},
    ]


def chat(client: httpx.Client, session: dict, message: str) -> tuple[dict, float]:
    started = time.perf_counter()
    response = client.post(
        BASE + "/api/chat",
        headers=headers(session["user"]),
        json={
            "message": message,
            "persona_id": session["persona"],
            "session_id": session["sid"],
            "user_name": session["user"],
            "client_id": session["cid"],
        },
    )
    elapsed = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        raise RuntimeError(f"chat {session['key']} HTTP {response.status_code}: {response.text[:300]}")
    return response.json(), elapsed


def read_openher(users: set[str], sessions: list[dict]) -> dict:
    placeholders = ",".join("?" for _ in users)
    with sqlite3.connect(OPENHER_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT user_id, persona_id, agent_data, metabolism_data, state_version, "
            "updated_at, length(agent_data) AS agent_bytes, "
            "length(metabolism_data) AS metabolism_bytes "
            f"FROM genome_state WHERE user_id IN ({placeholders})",
            tuple(users),
        ).fetchall()
    by_key = {(row["user_id"], row["persona_id"]): row for row in rows}
    output = {}
    for session in sessions:
        row = by_key.get((session["user"], session["persona"]))
        if row is None:
            output[session["key"]] = None
            continue
        agent = json.loads(row["agent_data"])
        metabolism = json.loads(row["metabolism_data"])
        output[session["key"]] = {
            "state_version": row["state_version"],
            "updated_at": row["updated_at"],
            "agent_bytes": row["agent_bytes"],
            "metabolism_bytes": row["metabolism_bytes"],
            "interaction_count": agent.get("interaction_count"),
            "age": agent.get("age"),
            "seed": agent.get("seed"),
            "drive_state": agent.get("drive_state"),
            "frustration": metabolism.get("frustration"),
        }
    return output


def read_extra(users: set[str], sessions: list[dict]) -> dict:
    placeholders = ",".join("?" for _ in users)
    with sqlite3.connect(EXTRA_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT user_id, persona_id, extra_json, updated_at, "
            "length(extra_json) AS extra_bytes "
            f"FROM agent_extra WHERE user_id IN ({placeholders})",
            tuple(users),
        ).fetchall()
    by_key = {(row["user_id"], row["persona_id"]): row for row in rows}
    output = {}
    for session in sessions:
        row = by_key.get((session["user"], session["persona"]))
        if row is None:
            output[session["key"]] = None
            continue
        extra = json.loads(row["extra_json"])
        output[session["key"]] = {
            "updated_at": row["updated_at"],
            "extra_bytes": row["extra_bytes"],
            "turn_count": extra.get("turn_count"),
            "relationship": extra.get("relationship"),
            "search_hit": extra.get("search_hit"),
        }
    return output


def history(client: httpx.Client, session: dict) -> dict:
    response = client.get(
        BASE + f"/api/chat/history/{session['persona']}",
        params={"client_id": session["cid"], "limit": 100},
    )
    body = response.json() if response.status_code == 200 else {}
    messages = body.get("messages", [])
    return {
        "status": response.status_code,
        "total": body.get("total", 0),
        "contents": [message.get("content", "") for message in messages],
    }


def warm() -> int:
    run = time.strftime("%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    sessions = build_sessions(run)
    users = {session["user"] for session in sessions}
    client = httpx.Client(timeout=120)

    status = client.get(BASE + "/api/status")
    check("app /api/status", status.status_code == 200, status.status_code)

    providers: dict[str, str] = {}
    for user in sorted(users):
        response = client.post(
            BASE + "/api/me/providers",
            headers=headers(user),
            json={
                "label": f"state69 mock {user[-1]}",
                "base_url": "http://127.0.0.1:9999/v1",
                "api_key": f"sk-state69-{run}-{user[-1]}",
                "models": ["mock-persona"],
                "probe": True,
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"create provider {user}: {response.status_code} {response.text[:300]}")
        providers[user] = response.json()["id"]
        routed = client.put(
            BASE + "/api/me/routing",
            headers=headers(user),
            json={"provider": providers[user], "model": "mock-persona", "fallback": []},
        )
        check(f"route {user[-1]}", routed.status_code == 200, routed.status_code)

    statuses: dict[str, list[dict]] = {session["key"]: [] for session in sessions}
    timings: list[float] = []

    def one(session: dict, turn: int) -> None:
        body, elapsed = chat(client, session, f"{session['key']}-r{turn}")
        statuses[session["key"]].append(
            {
                "turn_count": body.get("turn_count"),
                "history_length": body.get("history_length"),
                "relationship": body.get("relationship"),
                "drive_state": body.get("drive_state"),
                "dominant_drive": body.get("dominant_drive"),
                "age": body.get("age"),
            }
        )
        timings.append(elapsed)

    # 两个用户、同一 persona：交替 5 轮
    for turn in range(1, ROUNDS + 1):
        one(sessions[0], turn)
        one(sessions[1], turn)
    # 同一用户、两个 persona：交替 5 轮（合计 10 轮）
    for turn in range(1, ROUNDS + 1):
        one(sessions[2], turn)
        one(sessions[3], turn)

    for session in sessions:
        turns = [item["turn_count"] for item in statuses[session["key"]]]
        check(
            f"{session['key']} turn_count 1..{ROUNDS}",
            turns == list(range(1, ROUNDS + 1)),
            turns,
        )
        last = statuses[session["key"]][-1]
        check(
            f"{session['key']} history restored per turn",
            last["history_length"] >= ROUNDS * 2,
            last["history_length"],
        )
        check(
            f"{session['key']} relationship EMA advanced",
            (last["relationship"] or {}).get("depth", 0) > 0,
            last["relationship"],
        )

    db = read_openher(users, sessions)
    for session in sessions:
        item = db[session["key"]]
        check(
            f"{session['key']} state row exists",
            item is not None and item["interaction_count"] == ROUNDS,
            item,
        )

    a, b, c_luna, c_iris = sessions
    check(
        "two users same persona stay separate",
        db["a"]["seed"] != db["b"]["seed"] or db["a"]["drive_state"] != db["b"]["drive_state"],
        {"a_version": db["a"]["state_version"], "b_version": db["b"]["state_version"]},
    )
    check(
        "two personas same user stay separate",
        db["c_luna"]["seed"] != db["c_iris"]["seed"],
        {"luna_seed": db["c_luna"]["seed"], "iris_seed": db["c_iris"]["seed"]},
    )

    extra = read_extra(users, sessions)
    for session in sessions:
        item = extra[session["key"]]
        check(
            f"{session['key']} extra state persisted",
            item is not None
            and item["turn_count"] == ROUNDS
            and (item["relationship"] or {}).get("depth", 0) > 0,
            item,
        )

    histories = {session["key"]: history(client, session) for session in sessions}
    for session in sessions:
        item = histories[session["key"]]
        check(
            f"{session['key']} chat log has 10 rows",
            item["status"] == 200 and item["total"] == ROUNDS * 2,
            {"total": item["total"], "status": item["status"]},
        )
    check(
        "chat history does not cross users",
        "a-r1" in " ".join(histories["a"]["contents"])
        and "a-r1" not in " ".join(histories["b"]["contents"])
        and "b-r1" in " ".join(histories["b"]["contents"])
        and "b-r1" not in " ".join(histories["a"]["contents"]),
        {"a": histories["a"]["total"], "b": histories["b"]["total"]},
    )
    check(
        "chat history does not cross personas",
        "c_luna-r1" in " ".join(histories["c_luna"]["contents"])
        and "c_luna-r1" not in " ".join(histories["c_iris"]["contents"])
        and "c_iris-r1" in " ".join(histories["c_iris"]["contents"])
        and "c_iris-r1" not in " ".join(histories["c_luna"]["contents"]),
        {"luna": histories["c_luna"]["total"], "iris": histories["c_iris"]["total"]},
    )

    payload = {
        "run": run,
        "created_at": time.time(),
        "sessions": sessions,
        "providers": providers,
        "statuses": statuses,
        "db": db,
        "extra": extra,
        "history": histories,
        "timings_ms": {
            "avg": round(statistics.mean(timings), 1),
            "min": round(min(timings), 1),
            "max": round(max(timings), 1),
        },
    }
    WARM_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = all(item["ok"] for item in results)
    print(f"\n{'WARM PASS' if ok else 'WARM HAS FAILURES'} ({sum(1 for x in results if x['ok'])}/{len(results)})", flush=True)
    print(f"warm data: {WARM_PATH}", flush=True)
    return 0 if ok else 1


def resume() -> int:
    warm_data = json.loads(WARM_PATH.read_text(encoding="utf-8"))
    sessions = warm_data["sessions"]
    users = {session["user"] for session in sessions}
    client = httpx.Client(timeout=120)

    status = client.get(BASE + "/api/status")
    check("app /api/status after restart", status.status_code == 200, status.status_code)

    before_db = read_openher(users, sessions)
    before_extra = read_extra(users, sessions)
    for session in sessions:
        old = warm_data["db"][session["key"]]
        new = before_db[session["key"]]
        check(
            f"{session['key']} drive state survives restart",
            new is not None
            and new["interaction_count"] == old["interaction_count"]
            and new["drive_state"] == old["drive_state"],
            {"old": old["interaction_count"], "new": new["interaction_count"] if new else None},
        )
        old_extra = warm_data["extra"][session["key"]]
        new_extra = before_extra[session["key"]]
        check(
            f"{session['key']} relationship EMA survives restart",
            new_extra is not None
            and new_extra["turn_count"] == old_extra["turn_count"]
            and new_extra["relationship"] == old_extra["relationship"],
            {"old": old_extra["relationship"], "new": new_extra["relationship"] if new_extra else None},
        )

    statuses: dict[str, list[dict]] = {session["key"]: [] for session in sessions}
    for session in sessions:
        body, elapsed = chat(client, session, f"{session['key']}-resume")
        statuses[session["key"]].append(
            {
                "turn_count": body.get("turn_count"),
                "history_length": body.get("history_length"),
                "relationship": body.get("relationship"),
                "drive_state": body.get("drive_state"),
                "elapsed_ms": round(elapsed, 1),
            }
        )
        old = warm_data["statuses"][session["key"]][-1]
        check(
            f"{session['key']} turn_count continues after restart",
            body.get("turn_count") == old["turn_count"] + 1,
            {"before": old["turn_count"], "after": body.get("turn_count")},
        )
        check(
            f"{session['key']} chat history continues after restart",
            body.get("history_length", 0) >= old["history_length"] + 2,
            {"before": old["history_length"], "after": body.get("history_length")},
        )
        check(
            f"{session['key']} relationship not reset after restart",
            (body.get("relationship") or {}).get("depth", 0) > 0
            and abs(
                (body.get("relationship") or {}).get("depth", 0)
                - (old.get("relationship") or {}).get("depth", 0)
            )
            < 0.25,
            {"before": old.get("relationship"), "after": body.get("relationship")},
        )

    after_db = read_openher(users, sessions)
    after_extra = read_extra(users, sessions)
    for session in sessions:
        old = warm_data["db"][session["key"]]
        new = after_db[session["key"]]
        check(
            f"{session['key']} state advanced after resume",
            new is not None
            and new["interaction_count"] == old["interaction_count"] + 1
            and new["state_version"] > old["state_version"],
            {"interaction": new["interaction_count"] if new else None, "version": new["state_version"] if new else None},
        )
        check(
            f"{session['key']} extra turn_count advanced",
            after_extra[session["key"]] is not None
            and after_extra[session["key"]]["turn_count"] == old["turn_count"] + 1,
            after_extra[session["key"]],
        )

    payload = {
        "run": warm_data["run"],
        "resumed_at": time.time(),
        "statuses": statuses,
        "db": after_db,
        "extra": after_extra,
        "timings_ms": {
            "avg": round(
                statistics.mean(item[0]["elapsed_ms"] for item in statuses.values()), 1
            )
        },
    }
    RESUME_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = all(item["ok"] for item in results)
    print(f"\n{'RESUME PASS' if ok else 'RESUME HAS FAILURES'} ({sum(1 for x in results if x['ok'])}/{len(results)})", flush=True)
    print(f"resume data: {RESUME_PATH}", flush=True)
    return 0 if ok else 1


def metrics() -> int:
    warm_data = json.loads(WARM_PATH.read_text(encoding="utf-8"))
    resume_data = json.loads(RESUME_PATH.read_text(encoding="utf-8"))
    sessions = warm_data["sessions"]
    users = {session["user"] for session in sessions}

    db = read_openher(users, sessions)
    extra = read_extra(users, sessions)
    sizes = {}
    for session in sessions:
        row = db[session["key"]]
        ex = extra[session["key"]]
        sizes[session["key"]] = {
            "agent_bytes": row["agent_bytes"],
            "metabolism_bytes": row["metabolism_bytes"],
            "agent_state_bytes": row["agent_bytes"] + row["metabolism_bytes"],
            "extra_bytes": ex["extra_bytes"] if ex else 0,
            "total_bytes": row["agent_bytes"] + row["metabolism_bytes"] + (ex["extra_bytes"] if ex else 0),
        }
    totals = [item["total_bytes"] for item in sizes.values()]
    check(
        "state size recorded",
        all(value > 0 for value in totals),
        {"min": min(totals), "max": max(totals)},
    )

    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    from engine.state_store import StateStore  # noqa: E402
    from shell.agent_state import AgentExtraStore  # noqa: E402

    store = StateStore(str(OPENHER_DB))
    extra_store = AgentExtraStore(str(EXTRA_DB))
    load_ms = {}
    for session in sessions:
        samples = []
        extra_samples = []
        for _ in range(30):
            started = time.perf_counter()
            agent, metabolism = store.load_session(session["user"], session["persona"])
            samples.append((time.perf_counter() - started) * 1000)
            if agent is None or metabolism is None:
                raise RuntimeError(f"load_session 失败: {session['key']}")
            started = time.perf_counter()
            extra_store.load(session["user"], session["persona"])
            extra_samples.append((time.perf_counter() - started) * 1000)
        load_ms[session["key"]] = {
            "state_avg_ms": round(statistics.mean(samples), 3),
            "state_min_ms": round(min(samples), 3),
            "state_max_ms": round(max(samples), 3),
            "extra_avg_ms": round(statistics.mean(extra_samples), 3),
        }
    store.close()
    extra_store.close()

    cas_path = Path("/tmp/state69-cas.db")
    if cas_path.exists():
        cas_path.unlink()
    cas = StateStore(str(cas_path))
    cas.save_state("cas", "luna", agent_data="{}", metabolism_data="{}")
    version = cas.get_state_version("cas", "luna")
    first = cas.save_state(
        "cas", "luna", agent_data='{"round":1}', expected_version=version
    )
    second = cas.save_state(
        "cas", "luna", agent_data='{"round":2}', expected_version=version
    )
    final_version = cas.get_state_version("cas", "luna")
    check(
        "CAS allows one writer per version",
        first is True and second is False and final_version == version + 1,
        {"first": first, "second": second, "version": final_version},
    )

    cas.save_state("cas", "threads", agent_data="{}", metabolism_data="{}")
    barrier = threading.Barrier(8)

    def cas_writer(index: int):
        barrier.wait()
        try:
            return cas.save_state(
                "cas",
                "threads",
                agent_data=json.dumps({"writer": index}),
                expected_version=0,
            )
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(cas_writer, range(8)))
    successes = sum(value is True for value in outcomes)
    errors = [value for value in outcomes if isinstance(value, str)]
    check(
        "CAS concurrent writers: one success",
        successes == 1,
        {"successes": successes, "errors": errors},
    )
    cas.close()

    db_bytes = OPENHER_DB.stat().st_size if OPENHER_DB.exists() else 0
    extra_bytes = EXTRA_DB.stat().st_size if EXTRA_DB.exists() else 0
    payload = {
        "run": warm_data["run"],
        "measured_at": time.time(),
        "sizes": sizes,
        "size_summary": {
            "min_bytes": min(totals),
            "max_bytes": max(totals),
            "avg_bytes": round(statistics.mean(totals)),
        },
        "load_ms": load_ms,
        "db_files": {"openher_db": db_bytes, "agent_extra_db": extra_bytes},
        "cas": {
            "sequential": {"first": first, "second": second, "version": final_version},
            "threads": {"successes": successes, "errors": errors},
        },
        "turn_latency_ms": resume_data.get("timings_ms"),
    }
    METRICS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = all(item["ok"] for item in results)
    print(f"\n{'METRICS PASS' if ok else 'METRICS HAS FAILURES'} ({sum(1 for x in results if x['ok'])}/{len(results)})", flush=True)
    print(f"metrics data: {METRICS_PATH}", flush=True)
    return 0 if ok else 1


def cleanup() -> int:
    warm_data = json.loads(WARM_PATH.read_text(encoding="utf-8"))
    sessions = warm_data["sessions"]
    providers = warm_data["providers"]
    client = httpx.Client(timeout=30)
    for user in sorted({session["user"] for session in sessions}):
        client.delete(BASE + "/api/me/routing", headers=headers(user))
        provider_id = providers.get(user)
        if provider_id:
            response = client.delete(
                BASE + f"/api/me/providers/{provider_id}", headers=headers(user)
            )
            print(f"delete {provider_id}: {response.status_code}", flush=True)
    print("cleanup done", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("warm", "resume", "metrics", "cleanup"))
    args = parser.parse_args()
    return {"warm": warm, "resume": resume, "metrics": metrics, "cleanup": cleanup}[args.phase]()


if __name__ == "__main__":
    raise SystemExit(main())
