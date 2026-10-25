# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#68 OpenAI 兼容层零依赖测试。

直接运行：
    python3 tests/test_openai_compat.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.openai_format import (  # noqa: E402
    OpenAIInputError,
    ReplyStreamExtractor,
    completion_body,
    error_body,
    extract_last_user_text,
    is_openai_path,
    resolve_model_alias,
    stream_chunk_body,
)


def test_extract_last_user_text_from_openai_messages() -> None:
    messages = [
        {"role": "system", "content": "ignore me"},
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "回复"},
        {"role": "user", "content": "最后一句"},
    ]
    assert extract_last_user_text(messages) == "最后一句"

    parts = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图："},
                {"type": "image_url", "image_url": {"url": "data:..."}},
                {"type": "text", "text": "这是什么？"},
            ],
        }
    ]
    assert extract_last_user_text(parts) == "看图：[图片]这是什么？"

    class Message:
        role = "user"
        content = "对象消息"

    assert extract_last_user_text([Message()]) == "对象消息"


def test_extract_rejects_missing_or_empty_user_message() -> None:
    for messages in (
        [],
        [{"role": "system", "content": "system"}],
        [{"role": "user", "content": "   "}],
    ):
        try:
            extract_last_user_text(messages)
        except OpenAIInputError as exc:
            assert exc.code == "invalid_messages"
            assert exc.param == "messages"
        else:
            raise AssertionError(f"应拒绝: {messages}")


def test_model_alias_mapping() -> None:
    ids = ["luna", "vivian"]
    selected = resolve_model_alias("vivian", persona_ids=ids, default_persona="luna")
    assert selected.persona_id == "vivian"
    assert selected.route_key is None
    assert selected.requested == "vivian"

    explicit = resolve_model_alias(
        "openai/gpt-4o", persona_ids=ids, default_persona="luna"
    )
    assert explicit.persona_id == "luna"
    assert explicit.route_key == "openai/gpt-4o"

    fallback = resolve_model_alias("gpt-4o-mini", persona_ids=ids, default_persona="luna")
    assert fallback.persona_id == "luna"
    assert fallback.route_key is None
    assert fallback.requested == "gpt-4o-mini"

    default = resolve_model_alias("", persona_ids=ids, default_persona="luna")
    assert default.persona_id == "luna"
    assert default.requested == "alpha"


def _collect(chunks: list[str]) -> tuple[list[str], ReplyStreamExtractor]:
    extractor = ReplyStreamExtractor()
    output: list[str] = []
    for chunk in chunks:
        output.extend(extractor.feed(chunk))
    output.extend(extractor.finish())
    return output, extractor


def test_stream_extractor_only_emits_final_reply() -> None:
    raw = (
        "<think>内部计划</think>\n"
        "【内心独白】\n这段不能给用户\n"
        "【最终回复】\n你好，世界！\n"
        "【表达方式】文字"
    )
    for size in (1, 2, 3, 7):
        output, extractor = _collect([raw[i : i + size] for i in range(0, len(raw), size)])
        text = "".join(output).strip()
        assert text == "你好，世界！", (size, repr(text))
        assert "内心独白" not in text
        assert "这段不能给用户" not in text
        assert extractor.reply_text == "你好，世界！"
        assert extractor.saw_start_marker


def test_stream_extractor_english_markers_and_sentinel() -> None:
    raw = (
        "[Inner Monologue]\nsecret\n"
        "[Final Reply]\nHello there.\n"
        "[Expression Mode]text"
    )
    chunks = ["__FEEL_DONE__"] + [raw[i : i + 2] for i in range(0, len(raw), 2)]
    output, extractor = _collect(chunks)
    assert "".join(output).strip() == "Hello there."
    assert extractor.reply_text == "Hello there."


def test_stream_extractor_fallback_without_markers() -> None:
    output, extractor = _collect(
        ["<think>hmm</think>\n", "直接回复，", "没有标记"]
    )
    assert "".join(output).strip() == "直接回复，没有标记"
    assert extractor.reply_text == "直接回复，没有标记"

    output, extractor = _collect(["__FEEL_DONE__"])
    assert output == []
    assert extractor.reply_text == ""


def test_openai_response_shapes() -> None:
    completion = completion_body(
        request_id="chatcmpl-test",
        created=1,
        model="luna",
        content="你好",
    )
    assert completion["object"] == "chat.completion"
    assert completion["choices"][0]["message"] == {
        "role": "assistant",
        "content": "你好",
    }
    assert completion["choices"][0]["finish_reason"] == "stop"

    chunk = stream_chunk_body(
        request_id="chatcmpl-test",
        created=1,
        model="luna",
        delta={"content": "你"},
    )
    assert chunk["object"] == "chat.completion.chunk"
    assert chunk["choices"][0]["delta"] == {"content": "你"}
    assert chunk["choices"][0]["finish_reason"] is None

    error = error_body(
        "bad",
        error_type="rate_limit_error",
        code="rate_limited",
        param="messages",
    )
    assert error == {
        "error": {
            "message": "bad",
            "type": "rate_limit_error",
            "param": "messages",
            "code": "rate_limited",
        }
    }
    assert is_openai_path("/v1/chat/completions")
    assert is_openai_path("/v1/models")
    assert not is_openai_path("/api/chat")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"\n{len(tests)} tests passed")
