# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#36 事实/共同记忆 → SQLite FTS5。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.memory import (  # noqa: E402
    MAX_CONTENT,
    MAX_CONTEXT_ITEMS,
    MemoryError,
    MemoryStore,
    tokenize,
    validate_memory,
)

PROFILE = ROOT / "tests" / "fixtures" / "profiles" / "xiaolan.json"


def _store() -> MemoryStore:
    return MemoryStore(":memory:")


def _cli(db: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, ALPHA_MEMORY_DB=str(db))
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )


# ---------------------------------------------------------------- 切分与校验


def test_tokenize_chinese_by_char() -> None:
    assert tokenize("加班") == "加 班"
    assert tokenize("后端 API 挂了") == "后 端 api 挂 了"
    assert tokenize("") == ""


def test_validate_normal() -> None:
    assert validate_memory("对方多次加班") == []


def test_validate_rejects_empty_and_long() -> None:
    assert any("为空" in e for e in validate_memory(""))
    assert any("过长" in e for e in validate_memory("字" * (MAX_CONTENT + 1)))


def test_validate_rejects_bad_role_and_category() -> None:
    assert any("role" in e for e in validate_memory("x", role="boss"))
    assert any("category" in e for e in validate_memory("x", category="rumor"))


def test_validate_rejects_privacy_and_markers() -> None:
    assert any("隐私" in e for e in validate_memory("电话 13812345678"))
    assert any("隐私" in e for e in validate_memory("邮箱 a@b.com"))
    assert any("空壳" in e for e in validate_memory("【内心独白】算了"))


# ---------------------------------------------------------------- 写入与检索


def test_add_records_role_source_timestamp() -> None:
    store = _store()
    record = store.add("xiaolan", "对方多次加班", role="fact", category="fact",
                       source="distilled", importance=0.9, source_turn="profile.json")
    assert record.role == "fact"
    assert record.source == "distilled"
    assert record.source_turn == "profile.json"
    assert record.created_at > 0 and record.updated_at >= record.created_at


def test_duplicate_content_is_absorbed() -> None:
    store = _store()
    first = store.add("xiaolan", "对方多次加班", importance=0.4)
    second = store.add("xiaolan", "对方多次加班", importance=0.9)
    assert first.id == second.id
    assert second.importance == 0.9
    assert store.count("xiaolan") == 1


def test_importance_is_clamped() -> None:
    store = _store()
    assert store.add("p", "a", importance=5.0).importance == 1.0
    assert store.add("p", "b", importance=-1.0).importance == 0.0


def test_search_finds_two_char_chinese_query() -> None:
    """trigram 对 <3 字查询返回空（实测），所以必须靠切分 + LIKE 兜住。"""
    store = _store()
    store.add("xiaolan", "对方多次因加班临时失约", role="fact")
    assert len(store.search("xiaolan", "加班")) == 1
    assert len(store.search("xiaolan", "失约")) == 1


def test_search_single_char() -> None:
    store = _store()
    store.add("p", "答应过的店对方记着并订了位")
    assert len(store.search("p", "店")) == 1


def test_search_miss_returns_empty() -> None:
    store = _store()
    store.add("p", "今天很累")
    assert store.search("p", "展览") == []


def test_search_empty_query_falls_back_to_recent() -> None:
    store = _store()
    store.add("p", "第一条")
    store.add("p", "第二条")
    assert len(store.search("p", "")) == 2


def test_search_is_persona_and_user_scoped() -> None:
    store = _store()
    store.add("xiaolan", "共同记忆")
    store.add("linzhou", "共同记忆")
    assert len(store.search("xiaolan", "共同")) == 1
    assert store.search("xiaolan", "共同", user_id="bob") == []


def test_delete_removes_from_fts() -> None:
    store = _store()
    record = store.add("p", "要被删掉的记忆")
    assert store.search("p", "删掉")
    assert store.delete(record.id) is True
    assert store.search("p", "删掉") == []


def test_get_important_and_recent() -> None:
    store = _store()
    store.add("p", "低", importance=0.2)
    store.add("p", "高", importance=0.9)
    assert [r.content for r in store.get_important("p", min_importance=0.7)] == ["高"]
    assert store.get_recent("p")[0].content == "高"


def test_stats_by_role() -> None:
    store = _store()
    store.add("p", "用户说的", role="user")
    store.add("p", "她说的", role="assistant")
    store.add("p", "事实", role="fact")
    assert store.stats("p") == {"total": 3, "user": 1, "assistant": 1, "fact": 1}


# ---------------------------------------------------------------- 批量导入


def test_add_facts_skips_duplicates_and_empty() -> None:
    store = _store()
    added = store.add_facts("p", ["事实一", "", "事实一", "事实二"])
    assert added == 2
    assert store.count("p") == 2


def test_seed_from_profile_is_idempotent() -> None:
    store = _store()
    data = json.loads(PROFILE.read_text(encoding="utf-8"))
    first = store.seed_from_profile(data)
    second = store.seed_from_profile(data)
    expected = len(data["relation"]["defining_facts"]) + len(data["memory_signature"])
    assert first == expected
    assert second == 0
    assert store.count("xiaolan") == expected


def test_seed_marks_source_as_distilled() -> None:
    store = _store()
    store.seed_from_profile(json.loads(PROFILE.read_text(encoding="utf-8")))
    assert all(r.source == "distilled" for r in store.get_recent("xiaolan"))


def test_seed_skips_privacy_items() -> None:
    store = _store()
    data = {"persona_id": "p", "memory_signature": [{"kind": "asserted", "value": "电话 13812345678"},
                                                   {"kind": "asserted", "value": "正常事实"}]}
    assert store.seed_from_profile(data) == 1


# ---------------------------------------------------------------- 注入与导出


def test_build_context_empty_returns_empty_string() -> None:
    assert _store().build_context("p") == ""


def test_build_context_format() -> None:
    store = _store()
    store.add("p", "对方多次加班", role="fact", importance=0.9)
    text = store.build_context("p")
    assert text.startswith("【你记得的事】")
    assert "- [fact] 对方多次加班" in text


def test_build_context_respects_limit_and_dedupes() -> None:
    store = _store()
    for index in range(MAX_CONTEXT_ITEMS + 5):
        store.add("p", f"事实{index}", importance=0.9)
    lines = store.build_context("p").splitlines()
    assert len(lines) == 1 + MAX_CONTEXT_ITEMS
    assert len(set(lines)) == len(lines)


def test_build_context_english() -> None:
    store = _store()
    store.add("p", "she hates being late", importance=0.9)
    assert "What you remember" in store.build_context("p", lang="en")


def test_export_for_engine_shape() -> None:
    store = _store()
    store.add("p", "事实", role="fact", importance=0.8, source_turn="t1")
    row = store.export_for_engine("p")[0]
    assert set(row) == {"user_id", "persona_id", "content", "category", "importance",
                        "source_turn"}
    assert "role" not in row


def test_export_covers_all_rows() -> None:
    store = _store()
    for index in range(20):
        store.add("p", f"记忆{index}")
    assert len(store.export_for_engine("p")) == 20


# ---------------------------------------------------------------- CLI


def test_cli_full_cycle(tmp_path: Path) -> None:
    db = tmp_path / "mem.db"
    added = _cli(db, "persona", "memory", "xiaolan", "add",
                 "--content", "对方多次加班", "--role", "fact", "--importance", "0.9")
    assert added.returncode == 0, added.stderr

    listed = _cli(db, "persona", "memory", "xiaolan", "list")
    assert "共 1 条" in listed.stdout

    searched = _cli(db, "persona", "memory", "xiaolan", "search", "--query", "加班")
    assert "命中 1 条" in searched.stdout

    context = _cli(db, "persona", "memory", "xiaolan", "context")
    assert "【你记得的事】" in context.stdout


def test_cli_seed_and_context(tmp_path: Path) -> None:
    db = tmp_path / "mem.db"
    seeded = _cli(db, "persona", "memory", "xiaolan", "seed", "--file", str(PROFILE))
    assert seeded.returncode == 0, seeded.stderr
    assert "导入 3 条" in seeded.stdout
    context = _cli(db, "persona", "memory", "xiaolan", "context", "--query", "展览")
    assert "展览" in context.stdout


def test_cli_rejects_invalid(tmp_path: Path) -> None:
    proc = _cli(tmp_path / "m.db", "persona", "memory", "p", "add",
                "--content", "电话 13812345678")
    assert proc.returncode == 2
    assert "隐私" in proc.stderr


def test_cli_export_is_json(tmp_path: Path) -> None:
    db = tmp_path / "mem.db"
    _cli(db, "persona", "memory", "p", "add", "--content", "事实")
    proc = _cli(db, "persona", "memory", "p", "export")
    payload = json.loads(proc.stdout[proc.stdout.index("["):])
    assert payload[0]["content"] == "事实"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        if "tmp_path" in test.__code__.co_varnames[:test.__code__.co_argcount]:
            with tempfile.TemporaryDirectory() as tmp:
                test(Path(tmp))
        else:
            test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} memory tests passed")
