# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#83 人格 CRUD：仓库核心（零依赖）+ HTTP 层（有 fastapi 时）。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.persona_repo import (  # noqa: E402
    ConflictError,
    InvalidProfileError,
    NotFoundError,
    PersonaRepository,
    QuotaExceededError,
    RawTextNotAllowedError,
    _find_forbidden,
)


def asserted(value=None, provenance="source", basis=None, reason=None) -> dict:
    return {"kind": "asserted", "value": value, "provenance": provenance,
            "basis": basis, "reason": reason}


def payload(persona_id: str = "xiaolan", name: str = "小岚") -> dict:
    return {
        "schema_version": "2.0",
        "persona_id": persona_id,
        "identity": {"name": asserted(name), "lang": "zh"},
        "expression": {
            "rhythm": asserted("短句为主"),
            "habits": [asserted("算了，你忙。")],
        },
        "hard_rules": [asserted("被忽略时把话收短")],
    }


def new_repo(tmp: str, **kwargs) -> PersonaRepository:
    return PersonaRepository(Path(tmp), **kwargs)


def test_create_list_get() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp)
        created = repo.create("u1", payload())
        assert created["persona_id"] == "xiaolan"
        assert created["version"] == 1
        assert [p["persona_id"] for p in repo.list("u1")] == ["xiaolan"]
        got = repo.get("u1", "xiaolan")
        assert got["profile"]["identity"]["name"]["value"] == "小岚"
        assert (Path(tmp) / "u1" / "personas" / "xiaolan" / "SOUL.md").is_file()


def test_create_duplicate_is_conflict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp)
        repo.create("u1", payload())
        try:
            repo.create("u1", payload())
        except ConflictError as exc:
            assert exc.status == 409
            return
        raise AssertionError("重复创建应抛 ConflictError")


def test_create_requires_slug() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp)
        bad = payload()
        bad["persona_id"] = "XiaoLan"
        try:
            repo.create("u1", bad)
        except InvalidProfileError:
            return
        raise AssertionError("非法 persona_id 应被拒绝")


def test_raw_text_rejected_deeply() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp)
        bad = payload()
        bad["meta"] = {"nested": [{"chat_log": "原始记录"}]}
        try:
            repo.create("u1", bad)
        except RawTextNotAllowedError as exc:
            assert exc.status == 422 and "chat_log" in exc.message
            return
        raise AssertionError("深层原文键应被拒绝")


def test_find_forbidden_reports_path() -> None:
    assert _find_forbidden({"a": [{"raw_text": "x"}]}) == "$.a[0].raw_text"
    assert _find_forbidden({"ok": 1}) is None


def test_optimistic_lock() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp)
        repo.create("u1", payload())
        updated = repo.replace("u1", "xiaolan", payload(name="小岚2"), 1)
        assert updated["version"] == 2
        try:
            repo.replace("u1", "xiaolan", payload(), 1)
        except ConflictError as exc:
            assert "版本冲突" in exc.message
            return
        raise AssertionError("旧版本写入应冲突")


def test_patch_merges_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp)
        repo.create("u1", payload())
        repo.patch("u1", "xiaolan", {"identity": {"name": asserted("小岚改")}}, 1)
        got = repo.get("u1", "xiaolan")
        assert got["profile"]["identity"]["name"]["value"] == "小岚改"
        assert got["profile"]["expression"]["rhythm"]["value"] == "短句为主"
        assert got["state"]["version"] == 2


def test_patch_invalid_result_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp)
        repo.create("u1", payload())
        try:
            repo.patch("u1", "xiaolan", {"identity": {"name": asserted(None, "source")}}, 1)
        except InvalidProfileError:
            return
        raise AssertionError("改坏后应校验失败")


def test_soft_delete_and_restore() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp)
        repo.create("u1", payload())
        repo.delete("u1", "xiaolan")
        assert repo.list("u1") == []
        assert [p["persona_id"] for p in repo.list("u1", include_deleted=True)] == ["xiaolan"]
        try:
            repo.get("u1", "xiaolan")
        except NotFoundError:
            pass
        else:
            raise AssertionError("软删除后 get 应 404")
        repo.restore("u1", "xiaolan")
        assert [p["persona_id"] for p in repo.list("u1")] == ["xiaolan"]


def test_quota() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp, max_personas=1)
        repo.create("u1", payload())
        try:
            repo.create("u1", payload("other"))
        except QuotaExceededError as exc:
            assert exc.status == 429
            return
        raise AssertionError("超配额应抛 QuotaExceededError")


def test_user_isolation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp)
        repo.create("u1", payload())
        assert repo.list("u2") == []
        try:
            repo.get("u2", "xiaolan")
        except NotFoundError:
            return
        raise AssertionError("跨用户访问应 404")


def test_fork() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp)
        repo.create("u1", payload())
        copied = repo.fork("u1", "xiaolan", new_id="xiaolan-copy")
        assert copied["persona_id"] == "xiaolan-copy"
        assert copied["source"] == "fork:xiaolan"
        assert len(repo.list("u1")) == 2


def test_version_snapshots_pruned() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp, keep_versions=2)
        repo.create("u1", payload())
        repo.replace("u1", "xiaolan", payload(name="v2"), 1)
        repo.replace("u1", "xiaolan", payload(name="v3"), 2)
        versions = sorted(
            (Path(tmp) / "u1" / "personas" / "xiaolan" / "versions").glob("v*.json")
        )
        assert len(versions) == 2, [p.name for p in versions]


def test_mirror_dir_gets_soul() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        mirror = Path(tmp) / "engine" / "personas"
        repo = PersonaRepository(Path(tmp) / "data", mirror_dir=mirror)
        repo.create("u1", payload())
        soul = mirror / "xiaolan" / "SOUL.md"
        assert soul.is_file() and "小岚" in soul.read_text(encoding="utf-8")
        repo.delete("u1", "xiaolan")
        assert not soul.exists(), "软删除后镜像应移除"


def test_on_change_callback() -> None:
    calls: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        repo = new_repo(tmp, on_change=lambda u, p: calls.append((u, p)))
        repo.create("u1", payload())
        repo.delete("u1", "xiaolan")
        assert calls == [("u1", "xiaolan"), ("u1", "xiaolan")]


# ── HTTP 层（有 fastapi/testclient 时） ──────────────────────────────

def _client(tmp: str):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from shell.personas_api import build_personas_router

    events: list[str] = []
    repo = PersonaRepository(Path(tmp))
    app = FastAPI()
    app.include_router(build_personas_router(repo, audit=lambda e, p: events.append(e)))
    return TestClient(app), repo, events


def test_http_crud_flow() -> None:
    try:
        from fastapi.testclient import TestClient  # noqa: F401
    except ImportError:
        print("SKIP: fastapi 未安装")
        return
    with tempfile.TemporaryDirectory() as tmp:
        client, _, events = _client(tmp)
        r = client.post("/v1/personas", json=payload(), headers={"x-alpha-user": "u1"})
        assert r.status_code == 201, r.text

        r = client.get("/v1/personas", headers={"x-alpha-user": "u1"})
        assert [i["persona_id"] for i in r.json()["items"]] == ["xiaolan"]

        r = client.put("/v1/personas/xiaolan", json=payload(name="小岚2"),
                       headers={"x-alpha-user": "u1", "If-Match": '"1"'})
        assert r.status_code == 200 and r.json()["version"] == 2

        r = client.put("/v1/personas/xiaolan", json=payload(),
                       headers={"x-alpha-user": "u1", "If-Match": '"1"'})
        assert r.status_code == 409 and r.json()["error"] == "version_conflict"

        r = client.put("/v1/personas/xiaolan", json=payload(), headers={"x-alpha-user": "u1"})
        assert r.status_code == 400

        r = client.patch("/v1/personas/xiaolan", json={"identity": {"name": asserted("小岚3")}},
                         headers={"x-alpha-user": "u1", "If-Match": "2"})
        assert r.status_code == 200 and r.json()["version"] == 3

        r = client.delete("/v1/personas/xiaolan", headers={"x-alpha-user": "u1"})
        assert r.status_code == 200
        r = client.get("/v1/personas/xiaolan", headers={"x-alpha-user": "u1"})
        assert r.status_code == 404

        r = client.post("/v1/personas/xiaolan/restore", headers={"x-alpha-user": "u1"})
        assert r.status_code == 200

        assert "persona.create" in events and "persona.update" in events


def test_http_raw_text_422() -> None:
    try:
        from fastapi.testclient import TestClient  # noqa: F401
    except ImportError:
        print("SKIP: fastapi 未安装")
        return
    with tempfile.TemporaryDirectory() as tmp:
        client, _, _ = _client(tmp)
        bad = payload()
        bad["meta"] = {"raw_text": "原文"}
        r = client.post("/v1/personas", json=bad, headers={"x-alpha-user": "u1"})
        assert r.status_code == 422
        assert r.json()["error"] == "raw_text_not_allowed"


def test_http_user_isolation_404() -> None:
    try:
        from fastapi.testclient import TestClient  # noqa: F401
    except ImportError:
        print("SKIP: fastapi 未安装")
        return
    with tempfile.TemporaryDirectory() as tmp:
        client, _, _ = _client(tmp)
        client.post("/v1/personas", json=payload(), headers={"x-alpha-user": "u1"})
        r = client.get("/v1/personas/xiaolan", headers={"x-alpha-user": "u2"})
        assert r.status_code == 404


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} persona api tests passed")
