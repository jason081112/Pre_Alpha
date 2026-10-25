# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenAI 兼容端点的纯格式层。

不导入 FastAPI，方便零依赖测试；HTTP 接线在 `shell.openai_api`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

_REPLY_STARTS = ("【最终回复】", "[Final Reply]")
_REPLY_ENDS = ("【表达方式】", "[Expression Mode]")
_MAX_MARKER_LEN = max(
    max(len(marker) for marker in _REPLY_STARTS),
    max(len(marker) for marker in _REPLY_ENDS),
)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class OpenAIInputError(ValueError):
    """可以直接映射成 OpenAI 400 的输入错误。"""

    def __init__(self, message: str, *, param: str | None = None, code: str = "invalid_request"):
        super().__init__(message)
        self.message = message
        self.param = param
        self.code = code


@dataclass(frozen=True)
class ModelAlias:
    """`model` 字段解析结果。

    `route_key` 为空表示沿用账号已保存的路由；非空表示用 `provider/model`
    覆盖本次请求的路由。人格与路由是分开的：人格决定聊天对象，路由决定
    本次调用走哪个 provider。
    """

    persona_id: str
    route_key: str | None
    requested: str


def is_openai_path(path: str | None) -> bool:
    value = path or ""
    return value == "/v1" or value.startswith("/v1/")


def error_body(
    message: str,
    *,
    error_type: str = "invalid_request_error",
    code: str | None = None,
    param: str | None = None,
) -> dict:
    """OpenAI 标准错误包络。"""
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": param,
            "code": code,
        }
    }


def resolve_model_alias(
    requested: str | None,
    *,
    persona_ids: Sequence[str],
    default_persona: str,
) -> ModelAlias:
    """把 OpenAI 的 `model` 映射到人格或 `provider/model` 路由。

    - 人格 ID：`luna`
    - 默认别名：`alpha` / `default` / 空值
    - 显式路由：`openai/gpt-4o`
    - 其他模型名（例如 SDK 默认的 `gpt-4o`）沿用账号路由与默认人格，
      避免客户端因为模型名不匹配而完全不可用。
    """
    value = (requested or "").strip()
    if value and value in persona_ids:
        return ModelAlias(persona_id=value, route_key=None, requested=value)
    if value.count("/") == 1:
        provider, model = value.split("/", 1)
        if provider and model:
            return ModelAlias(
                persona_id=default_persona,
                route_key=value,
                requested=value,
            )
    return ModelAlias(
        persona_id=default_persona,
        route_key=None,
        requested=value or "alpha",
    )


def _field(message: Any, name: str) -> Any:
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, (list, tuple)):
        return str(content)

    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
            continue
        kind = _field(part, "type")
        if kind == "text":
            text = _field(part, "text")
            if text:
                parts.append(str(text))
            continue
        if kind in {"image_url", "image"}:
            parts.append("[图片]")
            continue
        if kind in {"input_audio", "audio"}:
            parts.append("[音频]")
            continue
        # 未知 part 不阻塞文本对话，但也不把二进制塞进 prompt。
        text = _field(part, "text")
        if text:
            parts.append(str(text))
    return "".join(parts)


def extract_last_user_text(messages: Iterable[Any]) -> str:
    """取最后一条 user 消息的文本；更早的消息交给服务端会话记忆。"""
    ordered = list(messages)
    for message in reversed(ordered):
        role = str(_field(message, "role") or "").strip().lower()
        if role != "user":
            continue
        text = _content_text(_field(message, "content")).strip()
        if text:
            return text
        raise OpenAIInputError(
            "最后一条 user 消息内容为空",
            param="messages",
            code="invalid_messages",
        )
    raise OpenAIInputError(
        "messages 中必须包含至少一条 user 消息",
        param="messages",
        code="invalid_messages",
    )


def _find_marker(buffer: str, markers: Sequence[str]) -> tuple[int, str] | None:
    found: list[tuple[int, str]] = []
    for marker in markers:
        index = buffer.find(marker)
        if index >= 0:
            found.append((index, marker))
    if not found:
        return None
    return min(found, key=lambda item: (item[0], -len(item[1])))


class ReplyStreamExtractor:
    """从上游原始流中只提取「最终回复」段。

    上游 `ChatAgent.chat_stream()` 会先吐 `__FEEL_DONE__`，随后是包含
    `【内心独白】/【最终回复】/【表达方式】` 的原始模型输出。这里做增量
    提取，并保留尾部若干字符以处理跨 chunk 的标记。
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._raw: list[str] = []
        self._in_reply = False
        self._done = False
        self._saw_start = False
        self._any_output = False
        self._parts: list[str] = []

    @property
    def reply_text(self) -> str:
        return "".join(self._parts).strip()

    @property
    def saw_start_marker(self) -> bool:
        return self._saw_start

    def feed(self, chunk: str) -> list[str]:
        if not chunk or chunk == "__FEEL_DONE__":
            return []
        self._raw.append(chunk)
        if self._done:
            return []

        self._buffer += chunk
        output: list[str] = []

        if not self._in_reply:
            found = _find_marker(self._buffer, _REPLY_STARTS)
            if found is None:
                # 只保留尾部，防止标记被 chunk 切断；完整原文另存在 _raw。
                if len(self._buffer) > _MAX_MARKER_LEN * 2:
                    self._buffer = self._buffer[-_MAX_MARKER_LEN * 2 :]
                return []
            index, marker = found
            self._in_reply = True
            self._saw_start = True
            self._buffer = self._buffer[index + len(marker) :]

        found_end = _find_marker(self._buffer, _REPLY_ENDS)
        if found_end is not None:
            index, _marker = found_end
            piece = self._buffer[:index]
            self._buffer = ""
            self._done = True
            self._emit(piece, output)
            return output

        if len(self._buffer) > _MAX_MARKER_LEN:
            piece = self._buffer[:-_MAX_MARKER_LEN]
            self._buffer = self._buffer[-_MAX_MARKER_LEN :]
            self._emit(piece, output)
        return output

    def finish(self) -> list[str]:
        """流结束时补齐尾部；没有标记时回退到全文（去掉 think 块）。"""
        if self._done:
            return []
        output: list[str] = []
        if self._in_reply:
            self._done = True
            piece = self._buffer
            self._buffer = ""
            self._emit(piece, output)
            return output

        raw = _THINK_RE.sub("", "".join(self._raw)).strip()
        if raw:
            self._done = True
            self._emit(raw, output, clean_leading=False)
        return output

    def _emit(
        self,
        piece: str,
        output: list[str],
        *,
        clean_leading: bool = True,
    ) -> None:
        if not piece:
            return
        if clean_leading and not self._any_output:
            piece = piece.lstrip()
        if not piece:
            return
        self._any_output = True
        self._parts.append(piece)
        output.append(piece)


def completion_body(
    *,
    request_id: str,
    created: int,
    model: str,
    content: str,
) -> dict:
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def stream_chunk_body(
    *,
    request_id: str,
    created: int,
    model: str,
    delta: dict | None = None,
    finish_reason: str | None = None,
) -> dict:
    return {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta or {},
                "finish_reason": finish_reason,
            }
        ],
    }
