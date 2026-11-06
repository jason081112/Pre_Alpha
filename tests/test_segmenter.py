# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#96 分段器：空行切段、跨块安全、index 每段归零、空段不产生气泡。

    python3 tests/test_segmenter.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.stream_parse import Segmenter  # noqa: E402


def run(chunks: list[str]) -> tuple[list[tuple[str, int, str]], list[str]]:
    """返回 (事件列表[(kind, segment, text)], 段文本)。"""
    seg = Segmenter()
    events = []
    for c in chunks:
        for ev in seg.feed(c):
            events.append((ev.kind, ev.segment, ev.text))
    for ev in seg.finish():
        events.append((ev.kind, ev.segment, ev.text))
    return events, seg.segments


def test_single_segment():
    events, segs = run(["我在。\n别急。"])
    assert segs == ["我在。\n别急。"], segs
    assert [k for k, _, _ in events] == ["chunk", "segment_end"]


def test_two_segments_split_on_blank_line():
    events, segs = run(["我在。\n\n快去把鞋换了。"])
    assert segs == ["我在。", "快去把鞋换了。"], segs
    ends = [e for e in events if e[0] == "segment_end"]
    assert [e[1] for e in ends] == [0, 1]
    assert [e[2] for e in ends] == ["我在。", "快去把鞋换了。"]


def test_break_split_across_chunks():
    """分段符被切成两个 chunk（\n + \n）也要正确切段。"""
    events, segs = run(["我在。\n", "\n快去。"])
    assert segs == ["我在。", "快去。"], segs


def test_index_resets_per_segment():
    seg = Segmenter()
    got = []
    for c in ["第一段前半", "，第一段后半\n\n第二段"]:
        for ev in seg.feed(c):
            got.append((ev.segment, ev.index, ev.kind, ev.text))
    for ev in seg.finish():
        got.append((ev.segment, ev.index, ev.kind, ev.text))

    chunks = [g for g in got if g[2] == "chunk"]
    # 每段 index 从 0 开始且连续
    for sid in {c[0] for c in chunks}:
        idx = [c[1] for c in chunks if c[0] == sid]
        assert idx == list(range(len(idx))), (sid, idx)


def test_blank_only_segment_dropped():
    """连续空行不产生空气泡。"""
    events, segs = run(["第一段\n\n\n\n第二段"])
    assert segs == ["第一段", "第二段"], segs
    assert len([e for e in events if e[0] == "segment_end"]) == 2


def test_three_segments_match_upstream_shape():
    text = "我在。\n\n快去把鞋换了。\n\n先去洗个热水澡。"
    events, segs = run([text])
    assert segs == ["我在。", "快去把鞋换了。", "先去洗个热水澡。"], segs
    assert len([e for e in events if e[0] == "segment_end"]) == 3


def test_chunks_join_equals_segments():
    text = "甲\n\n乙\n\n丙"
    events, segs = run([text])
    joined = "".join(e[2] for e in events if e[0] == "chunk")
    norm = lambda s: "".join(s.split())  # noqa: E731
    assert norm(joined) == norm("".join(segs)), (joined, segs)


def test_no_trailing_break_still_closes():
    events, segs = run(["只有一段没有结尾"])
    assert segs == ["只有一段没有结尾"]
    assert events[-1][0] == "segment_end"


def test_empty_input():
    events, segs = run([])
    assert events == [] and segs == []


def test_whitespace_only_no_bubble():
    events, segs = run(["   \n\n  "])
    assert segs == [], segs
    assert [e for e in events if e[0] == "segment_end"] == []


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
