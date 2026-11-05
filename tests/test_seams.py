# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#93 接缝守卫：5 个注入点的启动自检。

零依赖（只用 types.SimpleNamespace），不导入 shell.app，不启动引擎。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.seams import SEAM_NAMES, check_seams  # noqa: E402


class FakeLoader:
    def __init__(self, with_reload: bool = True) -> None:
        if with_reload:
            self.reload = self._reload

    def _reload(self, persona_id: str) -> None:
        return None


def _good_upstream():
    """模拟外壳注入完成后的上游模块。"""
    ours = {
        "get_or_create_session": lambda *a, **k: None,
        "_persist_agent": lambda *a, **k: None,
        "llm_client": object(),
        "chat_log_store": object(),
    }
    mod = SimpleNamespace(**ours, persona_loader=FakeLoader())
    return mod, ours


def test_all_seams_present():
    upstream, ours = _good_upstream()
    report = check_seams(
        upstream,
        get_or_create_session=ours["get_or_create_session"],
        persist_agent=ours["_persist_agent"],
        llm_client=ours["llm_client"],
        chat_log_store=ours["chat_log_store"],
    )
    assert report.ok, report.lines()
    assert [s.name for s in report.seams] == list(SEAM_NAMES)
    assert all(s.ok for s in report.seams)


def test_llm_client_not_replaced_is_fatal():
    """上游原版 llm_client 还在 —— 接线没做，必须拦。"""
    upstream, ours = _good_upstream()
    original = object()
    upstream.llm_client = original  # 上游自己的客户端
    report = check_seams(
        upstream,
        get_or_create_session=ours["get_or_create_session"],
        persist_agent=ours["_persist_agent"],
        llm_client=ours["llm_client"],
        chat_log_store=ours["chat_log_store"],
    )
    assert not report.ok
    assert [s.name for s in report.missing] == ["llm_client"]
    assert "未注入" in report.missing[0].detail


def test_missing_attribute_is_fatal():
    """上游把 get_or_create_session 改名 —— 最典型的升级断裂。"""
    upstream, ours = _good_upstream()
    del upstream.get_or_create_session
    report = check_seams(
        upstream,
        get_or_create_session=ours["get_or_create_session"],
        persist_agent=ours["_persist_agent"],
        llm_client=ours["llm_client"],
        chat_log_store=ours["chat_log_store"],
    )
    assert not report.ok
    assert "get_or_create_session" in [s.name for s in report.missing]
    assert "上游没有" in report.seams[0].detail


def test_persona_loader_without_reload_is_fatal():
    upstream, ours = _good_upstream()
    upstream.persona_loader = FakeLoader(with_reload=False)
    report = check_seams(
        upstream,
        get_or_create_session=ours["get_or_create_session"],
        persist_agent=ours["_persist_agent"],
        llm_client=ours["llm_client"],
        chat_log_store=ours["chat_log_store"],
    )
    assert not report.ok
    names = [s.name for s in report.missing]
    assert "persona_loader.reload" in names


def test_chat_log_store_absent_is_warning_only():
    """上游没开持久化不算故障：历史隔离由 session 前缀兜底。"""
    upstream, ours = _good_upstream()
    upstream.chat_log_store = None
    report = check_seams(
        upstream,
        get_or_create_session=ours["get_or_create_session"],
        persist_agent=ours["_persist_agent"],
        llm_client=ours["llm_client"],
        chat_log_store=None,
    )
    assert report.ok  # 没有 fatal
    warned = [s for s in report.seams if not s.ok]
    assert [s.name for s in warned] == ["chat_log_store"]
    assert warned[0].fatal is False


def test_fail_message_names_missing_seams():
    upstream = SimpleNamespace()  # 什么都没有
    report = check_seams(upstream)
    assert not report.ok
    msg = report.fail_message()
    assert "拒绝启动" in msg
    assert "get_or_create_session" in msg
    assert "ALPHA_SEAM_STRICT=0" in msg


def test_reload_probe_failure_is_fatal():
    upstream, ours = _good_upstream()

    def boom() -> bool:
        raise RuntimeError("reload 挂了")

    report = check_seams(
        upstream,
        get_or_create_session=ours["get_or_create_session"],
        persist_agent=ours["_persist_agent"],
        llm_client=ours["llm_client"],
        chat_log_store=ours["chat_log_store"],
        persona_reload_probe=boom,
    )
    assert not report.ok
    assert "探针调用失败" in [s for s in report.seams if not s.ok][0].detail


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"✓ {fn.__name__}")
        except Exception:
            failed += 1
            print(f"✗ {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
