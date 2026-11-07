# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#50 导出渲染：纯函数，不碰数据库。

    python3 tests/test_export.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.export_core import (  # noqa: E402
    build_payload,
    render_markdown,
    suggest_filename,
)

T0 = 1_800_000_000.0  # 固定时间，避免真实时钟带来的不稳定


def msgs():
    return [
        {"id": 1, "role": "user", "content": "今天加班到现在", "modality": "文字",
         "image_url": None, "created_at": T0},
        {"id": 2, "role": "assistant", "content": "你还没吃饭吧", "modality": "文字",
         "image_url": None, "created_at": T0 + 5},
    ]


def test_markdown_has_both_speakers():
    md = render_markdown(msgs(), persona_id="luna", persona_name="Luna", exported_at=T0)
    assert "**我**" in md
    assert "**她**" in md
    assert "今天加班到现在" in md
    assert "你还没吃饭吧" in md


def test_markdown_header_fields():
    md = render_markdown(msgs(), persona_id="luna", exported_at=T0)
    assert md.startswith("# luna · 对话记录")
    assert "消息数：2" in md
    assert "`luna`" in md


def test_markdown_empty_history_is_valid_doc():
    md = render_markdown([], persona_id="luna", exported_at=T0)
    assert "# luna" in md
    assert "还没有任何消息" in md


def test_markdown_multiline_kept_in_quote_block():
    m = [{"id": 1, "role": "assistant", "content": "第一行\n第二行\n\n第四行",
          "modality": "文字", "image_url": None, "created_at": T0}]
    md = render_markdown(m, persona_id="luna", exported_at=T0)
    assert "> 第一行" in md
    assert "> 第二行" in md
    assert "> 第四行" in md


def test_markdown_groups_by_day():
    two_days = [
        {"id": 1, "role": "user", "content": "一天", "modality": "文字",
         "image_url": None, "created_at": T0},
        {"id": 2, "role": "user", "content": "两天", "modality": "文字",
         "image_url": None, "created_at": T0 + 86400},
    ]
    md = render_markdown(two_days, persona_id="luna", exported_at=T0)
    assert md.count("\n## ") == 2, md


def test_markdown_shows_modality_when_not_text():
    m = [{"id": 1, "role": "assistant", "content": "", "modality": "语音",
          "image_url": "/api/selfie/x.jpg", "created_at": T0}]
    md = render_markdown(m, persona_id="luna", exported_at=T0)
    assert "（语音）" in md
    assert "无文字内容" in md
    assert "![图片](/api/selfie/x.jpg)" in md


def test_payload_schema_and_count():
    p = build_payload(msgs(), persona_id="luna", exported_at=T0)
    assert p["schema"] == "alpha.export.chat/v1"
    assert p["count"] == 2
    assert p["persona_id"] == "luna"
    assert len(p["messages"]) == 2
    assert p["messages"][0]["role"] == "user"
    assert p["messages"][0]["created_at_iso"].startswith("20")


def test_payload_survives_missing_fields():
    p = build_payload([{"role": "user", "content": "x"}], persona_id="luna", exported_at=T0)
    assert p["messages"][0]["modality"] == "文字"
    assert p["messages"][0]["created_at_iso"] is None


def test_filename_is_safe():
    name = suggest_filename("lu/na:1", ext="md", ts=T0)
    assert name.startswith("alpha-luna1-")
    assert name.endswith(".md")
    assert "/" not in name and ":" not in name


def test_filename_json():
    assert suggest_filename("luna", ext="json", ts=T0).endswith(".json")


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
