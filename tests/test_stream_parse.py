# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#94 流式解析器：跨块安全 + 标记不泄漏 + 兜底。

不依赖引擎，纯字符串，可直接跑：
    python3 tests/test_stream_parse.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.stream_parse import ReplyExtractor  # noqa: E402


def run(chunks: list[str]) -> tuple[str, str]:
    ex = ReplyExtractor()
    out = []
    for c in chunks:
        piece = ex.feed(c)
        if piece:
            out.append(piece)
    tail = ex.finish()
    if tail:
        out.append(tail)
    return "".join(out), ex.raw


def test_basic():
    text = "【内心独白】他有点累。\n【最终回复】我在。\n别急。"
    got, _ = run([text])
    assert got == "我在。\n别急。", repr(got)


def test_marker_split_across_chunks():
    """标记被切在中间也不能漏。"""
    got, _ = run(["【内", "心独白】嗯。\n【最终回", "复】我在。"])
    assert got == "我在。", repr(got)


def test_end_marker_stops_stream():
    text = "【最终回复】我在。\n别急。\n【表达方式】语音"
    got, _ = run([text])
    assert got == "我在。\n别急。\n", repr(got)


def test_end_marker_split_across_chunks():
    got, _ = run(["【最终回复】我在。\n【表达", "方式】表情"])
    assert got.strip() == "我在。".strip(), repr(got)


def test_no_marker_sends_nothing_midstream():
    """模型不守协议：中途一个字都不发，避免把内心独白当回复吐出去。"""
    got, raw = run(["他说", "会来的", "。"])
    assert got == "", repr(got)
    assert raw == "他说会来的。"


def test_english_markers():
    got, _ = run(["[Final Reply]I'm here.\n[Expression Mode]text"])
    assert got.strip() == "I'm here.".strip(), repr(got)


def test_multiple_chunks_stream_incrementally():
    ex = ReplyExtractor()
    chunks = ["【内心独白】嗯。", "【最终回复】我在", "。\n先去", "换鞋。", "\n【表达方式】文字"]
    pieces = [ex.feed(c) for c in chunks]
    pieces.append(ex.finish())
    non_empty = [p for p in pieces if p]
    assert len(non_empty) >= 3, non_empty
    assert "".join(pieces) == "我在。\n先去换鞋。\n", repr("".join(pieces))


def test_marker_like_text_stays_in_reply():
    """正文里出现「【表达方式】」字样 —— 按第一次出现截断，不能出现半个标记。"""
    got, _ = run(["【最终回复】好", "的，【表达", "方式】是术语。"])
    assert "【" not in got, repr(got)


def test_empty_input():
    got, raw = run([])
    assert got == "" and raw == ""


def test_tail_flushed_when_still_in_reply():
    """流结束时还在正文里 —— 尾部残留必须吐出来，不能吞字。"""
    ex = ReplyExtractor()
    a = ex.feed("【最终回复】我在")
    b = ex.feed("。慢慢说")
    tail = ex.finish()
    assert (a + b + (tail or "")) == "我在。慢慢说", repr(a + b + (tail or ""))


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
