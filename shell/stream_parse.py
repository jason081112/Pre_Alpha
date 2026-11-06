# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""增量解析引擎输出，边流边吐「最终回复」正文（#94）。

上游 `agent/output_router.stream_to_ws` 收了 `ws_send` 却从不调用，
所以 WS 上拿不到任何中间内容。本模块补这一层：把 `chat_stream()` 吐出的
原始分片，按引擎协议切成「可对外发送的正文」。

协议（vendor/OpenHer/engine/prompts/actor_single.md）：

    【内心独白】…
    【最终回复】…
    【表达方式】文字/语音/表情/照片/静默

要点：

- 标记可能被切在两个 chunk 之间（「【最终回」+「复】」），必须跨块安全
- 正文里也可能出现「【表达方式】」字样（角色在聊格式），只认第一次
- 没有标记时（模型不守协议）不在中途乱发，交给 finish() 一次性兜底
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "REPLY_STARTS",
    "REPLY_ENDS",
    "ReplyExtractor",
]

REPLY_STARTS = ("【最终回复】", "[Final Reply]")
REPLY_ENDS = ("【表达方式】", "[Expression Mode]")

_MAX_START = max(len(m) for m in REPLY_STARTS)
_MAX_END = max(len(m) for m in REPLY_ENDS)


def _find(buf: str, markers: tuple[str, ...]) -> tuple[int, int] | None:
    """返回最早出现的标记 (位置, 长度)；没有则 None。"""
    best: tuple[int, int] | None = None
    for m in markers:
        idx = buf.find(m)
        if idx == -1:
            continue
        if best is None or idx < best[0]:
            best = (idx, len(m))
    return best


def _pending_prefix(buf: str, markers: tuple[str, ...]) -> int:
    """buf 末尾有多少字符可能是某个标记的前缀，必须留着等下一块。"""
    keep = 0
    upper = min(len(buf), max(len(m) for m in markers) - 1)
    for n in range(upper, 0, -1):
        tail = buf[-n:]
        if any(m.startswith(tail) for m in markers):
            keep = max(keep, n)
    return keep


@dataclass
class ReplyExtractor:
    """把原始流切成可发送的正文片段。

    用法：
        ex = ReplyExtractor()
        async for chunk in agent.chat_stream(text):
            piece = ex.feed(chunk)
            if piece:
                await ws.send_json({"type": "chunk", "content": piece})
        # 收尾：不守协议时兜底一次
        tail = ex.finish()
    """

    state: str = "before"  # before | in_reply | after | empty
    buf: str = ""
    parts: list[str] = field(default_factory=list)
    raw_parts: list[str] = field(default_factory=list)
    saw_marker: bool = False

    def feed(self, chunk: str) -> str:
        """吃一块原始输出，返回本次该发出去的文本（可能是空串）。"""
        if not chunk:
            return ""
        self.raw_parts.append(chunk)

        if self.state == "after":
            return ""

        if self.state == "before":
            self.buf += chunk
            hit = _find(self.buf, REPLY_STARTS)
            if hit is None:
                keep = _pending_prefix(self.buf, REPLY_STARTS)
                self.buf = self.buf[-keep:] if keep else ""
                return ""
            idx, length = hit
            self.saw_marker = True
            self.state = "in_reply"
            self.buf = self.buf[idx + length :]
        elif self.state == "in_reply":
            self.buf += chunk
        else:  # after / empty
            return ""

        # state == in_reply：找结束标记
        hit = _find(self.buf, REPLY_ENDS)
        if hit is not None:
            idx, length = hit
            out = self.buf[:idx]
            self.buf = ""
            self.state = "after"
            if out:
                self.parts.append(out)
            return out

        keep = _pending_prefix(self.buf, REPLY_ENDS)
        if keep:
            out, self.buf = self.buf[:-keep], self.buf[-keep:]
        else:
            out, self.buf = self.buf, ""
        if out:
            self.parts.append(out)
        return out

    def finish(self) -> str:
        """流结束。若整段都没见到标记，把残留原文一次性当正文发出（兜底）。"""
        if self.state == "in_reply":
            tail, self.buf = self.buf, ""
            if tail:
                self.parts.append(tail)
            return tail

        if self.state == "before" and not self.saw_marker:
            # 模型没按协议输出 —— 整段原文可能就是回复本身
            self.state = "empty"
            self.buf = ""
            return ""

        self.state = "after"
        self.buf = ""
        return ""

    @property
    def reply_so_far(self) -> str:
        return "".join(self.parts)

    @property
    def raw(self) -> str:
        return "".join(self.raw_parts)


# ── 分段（#96）─────────────────────────────────────────────────────
# 引擎协议要求「回复须空行分段」，一段 = 一个气泡。上游 render 时逐段发、段间停 300ms，
# 才有「连发微信」的感觉；#94 为了单条不空等把多段合并成一次给，丢了那个节奏。
# 这里在流式过程中按空行切段，切出一段就推一段。

SEGMENT_BREAKS = ("\r\n\r\n", "\n\n")


def _find_break(buf: str) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    for brk in SEGMENT_BREAKS:
        idx = buf.find(brk)
        if idx == -1:
            continue
        if best is None or idx < best[0]:
            best = (idx, len(brk))
    return best


def _tail_keep(buf: str) -> int:
    """尾部可能是分段符的前缀（如单个 \n），留着等下一块。"""
    for n in (2, 1):
        if len(buf) >= n and any(b.startswith(buf[-n:]) for b in SEGMENT_BREAKS):
            return n
    return 0


@dataclass
class SegmentEvent:
    kind: str          # "chunk" | "segment_end"
    segment: int
    text: str = ""     # kind=chunk 时是本片文本；segment_end 时为整段全文
    index: int = 0     # 段内序号（chunk 用）


@dataclass
class Segmenter:
    """把正文流按空行切成若干段，边切边发。

    用法：
        for ev in seg.feed(piece):
            if ev.kind == "chunk":  send chunk(ev.text, index=ev.index, segment=ev.segment)
            else:                   send segment_end(ev.segment, ev.text); sleep(pause)
        for ev in seg.finish(): ...
    """

    buf: str = ""
    segment: int = 0
    index: int = 0
    parts: list[list[str]] = field(default_factory=lambda: [[]])

    def feed(self, text: str) -> list[SegmentEvent]:
        if not text:
            return []
        self.buf += text
        out: list[SegmentEvent] = []

        while True:
            hit = _find_break(self.buf)
            if hit is None:
                break
            idx, ln = hit
            body, self.buf = self.buf[:idx], self.buf[idx + ln :]
            out.extend(self._emit(body))
            out.extend(self._close_segment_if_any())

        keep = _tail_keep(self.buf)
        if keep:
            body, self.buf = self.buf[:-keep], self.buf[-keep:]
        else:
            body, self.buf = self.buf, ""
        out.extend(self._emit(body))
        return out

    def finish(self) -> list[SegmentEvent]:
        out: list[SegmentEvent] = []
        out.extend(self._emit(self.buf))
        self.buf = ""
        out.extend(self._close_segment_if_any())
        return out

    def _emit(self, body: str) -> list[SegmentEvent]:
        if not body:
            return []
        self.parts[self.segment].append(body)
        ev = SegmentEvent(kind="chunk", segment=self.segment, text=body, index=self.index)
        self.index += 1
        return [ev]

    def _close_segment_if_any(self) -> list[SegmentEvent]:
        """当前段有内容才收尾；空段（连续空行）直接丢弃，不产生空气泡。"""
        content = "".join(self.parts[self.segment])
        if not content.strip():
            self.parts[self.segment] = []
            self.index = 0
            return []
        ev = SegmentEvent(
            kind="segment_end",
            segment=self.segment,
            text=content,
            index=self.index,
        )
        self.segment += 1
        self.index = 0
        self.parts.append([])
        return [ev]

    @property
    def segments(self) -> list[str]:
        return ["".join(p).strip() for p in self.parts if "".join(p).strip()]
