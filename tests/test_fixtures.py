# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""P2-25 脱敏聊天样本的零依赖校验。"""

from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHAT_DIR = ROOT / "tests" / "fixtures" / "chats"
CANONICAL_FILES = (
    "relationship_zh.canonical.json",
    "colleague_zh.canonical.json",
)
TEXT_FILES = (
    "wechat_like.txt",
    "qq_like.txt",
)
CSV_FILE = "wechat_like.csv"
SOCIAL_FILE = "social_posts.json"
PHOTO_FILE = "photo_ocr.json"

MESSAGE_TYPES = {"text", "voice", "image", "sticker", "system", "link"}
PII_PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "url": re.compile(r"https?://"),
}


def _load_json(name: str) -> dict:
    return json.loads((CHAT_DIR / name).read_text(encoding="utf-8"))


def _assert_no_pii(text: str, *, source: str) -> None:
    for label, pattern in PII_PATTERNS.items():
        match = pattern.search(text)
        assert match is None, f"{source}: 发现疑似 {label}: {match.group(0)!r}"


def test_fixture_files_exist() -> None:
    expected = set(CANONICAL_FILES) | set(TEXT_FILES) | {CSV_FILE, SOCIAL_FILE, PHOTO_FILE}
    actual = {path.name for path in CHAT_DIR.glob("*") if path.is_file()}
    missing = expected - actual
    assert not missing, f"缺少样本: {sorted(missing)}"
    assert (CHAT_DIR / "README.md").is_file()


def test_canonical_schema() -> None:
    for name in CANONICAL_FILES:
        data = _load_json(name)
        assert data["schema_version"] == "1.0", name
        assert data["synthetic"] is True, name
        assert data["language"] == "zh-CN", name
        assert data["fixture_id"], name

        participants = {item["id"]: item for item in data["participants"]}
        assert participants, name
        assert any(item["role"] == "owner" for item in participants.values()), name
        assert any(item["role"] == "target" for item in participants.values()), name

        conversation = data["conversation"]
        assert conversation["id"] and conversation["platform"], name
        assert conversation["timezone"] == "Asia/Shanghai", name

        seen: set[str] = set()
        for message in data["messages"]:
            message_id = message["id"]
            assert message_id not in seen, f"{name}: 重复消息 ID {message_id}"
            seen.add(message_id)
            assert message["sender_id"] in participants, f"{name}: 未知 sender"
            assert message["type"] in MESSAGE_TYPES, f"{name}: 未知类型"
            assert str(message["text"]).strip(), f"{name}: 空消息"
            assert isinstance(message["attachments"], list), name
            _assert_no_pii(str(message["text"]), source=f"{name}/{message_id}")
            datetime.fromisoformat(message["timestamp"])
            reply_to = message["reply_to"]
            if reply_to is not None:
                assert reply_to in seen, f"{name}: reply_to 指向未知消息"


def test_text_samples_are_marked_synthetic() -> None:
    for name in TEXT_FILES:
        text = (CHAT_DIR / name).read_text(encoding="utf-8")
        assert text.startswith("# SYNTHETIC FIXTURE"), name
        _assert_no_pii(text, source=name)


def test_csv_sample() -> None:
    with (CHAT_DIR / CSV_FILE).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows, CSV_FILE
    assert list(rows[0]) == ["timestamp", "sender", "type", "text"], CSV_FILE
    for row in rows:
        assert row["sender"] and row["text"], CSV_FILE
        assert row["type"] in MESSAGE_TYPES, CSV_FILE
        datetime.fromisoformat(row["timestamp"])
        _assert_no_pii(row["text"], source=CSV_FILE)


def test_social_sample() -> None:
    data = _load_json(SOCIAL_FILE)
    assert data["synthetic"] is True
    assert data["schema_version"] == "1.0"
    assert data["posts"]
    for post in data["posts"]:
        assert post["id"] and post["text"]
        datetime.fromisoformat(post["created_at"])
        _assert_no_pii(post["text"], source=SOCIAL_FILE)


def test_photo_ocr_sample() -> None:
    data = _load_json(PHOTO_FILE)
    assert data["synthetic"] is True
    assert data["schema_version"] == "1.0"
    assert data["images"]
    for image in data["images"]:
        assert image["id"] and image["kind"], PHOTO_FILE
        assert image["width"] > 0 and image["height"] > 0
        assert image["ocr_blocks"]
        for block in image["ocr_blocks"]:
            assert block["text"]
            assert len(block["bbox"]) == 4
            assert all(isinstance(value, int) for value in block["bbox"])
            assert 0 <= float(block["confidence"]) <= 1
            _assert_no_pii(block["text"], source=PHOTO_FILE)


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} fixture tests passed")
