# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenHer 原生输出协议的解析与度量。

引擎的真实输出格式（`vendor/OpenHer/engine/prompts/actor_single.md`）::

    【内心独白】
    你此刻脑海中真实浮现的念头。
    【最终回复】
    你实际说出口的话……纯文字可选多条拆分（此时回复须空行分段）。静默表示不说话。
    【表达方式】
    文字 / 语音 / 表情 / 照片 / 静默（可组合）

之前的验收脚本只让模型回一句话，等于剥夺了「多条拆分」这一能力，因此把「她说 7 字」
当成了结论 —— 那只是单段模式下的产物。

本模块负责：
1. `parse_output()` —— 把原始输出拆成 内心 / 多条回复 / 表达方式
2. `detail_hit()` —— 判断回复有没有接住输入里的**具体信息**（不只是长度达标）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MODALITIES = ("文字", "语音", "表情", "照片", "静默")

_BLOCK = re.compile(r"【(内心独白|最终回复|表达方式)】\s*(.*?)(?=【(?:内心独白|最终回复|表达方式)】|\Z)",
                    re.S)


@dataclass
class TurnOutput:
    monologue: str = ""
    reply_raw: str = ""
    segments: list[str] = field(default_factory=list)
    modality: list[str] = field(default_factory=list)
    parsed: bool = False

    @property
    def total_chars(self) -> int:
        return sum(len(s) for s in self.segments)

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def silent(self) -> bool:
        return not self.segments or "静默" in self.modality

    def to_dict(self) -> dict:
        return {
            "monologue": self.monologue,
            "segments": self.segments,
            "modality": self.modality,
            "total_chars": self.total_chars,
            "segment_count": self.segment_count,
            "silent": self.silent,
            "parsed": self.parsed,
        }


def split_segments(reply: str) -> list[str]:
    """按空行拆多条（引擎约定：纯文字多条拆分时用空行分段）。"""
    if not reply.strip():
        return []
    parts = re.split(r"\n\s*\n", reply.strip())
    return [p.strip() for p in parts if p.strip()]


def parse_output(raw: str) -> TurnOutput:
    """解析模型原始输出。协议不合规时退化为「整段当成一条回复」。"""
    text = (raw or "").strip()
    blocks = {name: body for name, body in _BLOCK.findall(text)}

    if not blocks:
        segments = split_segments(text)
        return TurnOutput(reply_raw=text, segments=segments or ([text] if text else []),
                          parsed=False)

    monologue = blocks.get("内心独白", "").strip()
    reply_raw = blocks.get("最终回复", "").strip()
    modality_text = blocks.get("表达方式", "").strip()

    modality = [m for m in MODALITIES if m in modality_text]
    segments = [] if "静默" in modality else split_segments(reply_raw)

    return TurnOutput(
        monologue=monologue,
        reply_raw=reply_raw,
        segments=segments,
        modality=modality,
        parsed=True,
    )


# ---------------------------------------------------------------- 接住没接住

# 高频虚字：不承载具体信息，不计入重合
_STOP = set("的了是我你在就都也很不和与说个这那上下着过把被给对要会能可以一个什么怎么这样那样"
            "他她它们今天还又再真太没多少但而且所以因为如果就是还有点些吧啊呢嘛哦呀")

_PUNCT = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")


def _content_chars(text: str) -> set[str]:
    cleaned = _PUNCT.sub("", text or "")
    return {c for c in cleaned if c not in _STOP}


def detail_hit(user_input: str, segments: list[str],
               *, min_overlap: int = 2) -> dict:
    """回复有没有接住输入里的具体信息。

    做法：取输入里去掉高频虚字后的实字集合，看回复覆盖了多少。
    覆盖 ≥ `min_overlap` 个才算是「接住了具体事」，而不只是「我很心疼你」这类空泛回应。
    """
    source = _content_chars(user_input)
    reply = _content_chars("".join(segments))
    if not source:
        return {"hit": False, "overlap": 0, "ratio": 0.0, "matched": []}
    matched = sorted(source & reply)
    return {
        "hit": len(matched) >= min_overlap,
        "overlap": len(matched),
        "ratio": round(len(matched) / len(source), 3),
        "matched": matched[:8],
    }


__all__ = [
    "MODALITIES", "TurnOutput", "parse_output", "split_segments", "detail_hit",
]
