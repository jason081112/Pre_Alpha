# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""few-shot 注入方式（#44 后续实验的落地）。

三种模式，见 `docs/exp-fewshot-modes.md` 的实测对比：

* `full`            —— scene + monologue + reply 全给。**复读率 65%，已废弃**
* `mono`（默认）    —— 只给 scene + monologue + reply 的**统计量**（平均/最长字数）
* `mono_retrieval`  —— `mono` 基础上按情境只取 2–3 条。22 条素材下实测无收益，
                       且会掉人格一致性（冲突回合容易退让），暂不作为默认

为什么 `mono` 有效：模型没得抄，只能学「思维方式 + 句子该多短」。
为什么不能只靠加素材：素材越多，`full` 模式的可抄池越大，复读反而更严重。
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path

SEEDS_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "seeds"

MODES = ("full", "mono", "mono_retrieval")
DEFAULT_MODE = "mono"
MONOLOGUE_LEN = (15, 80)

# 情境识别（仅 mono_retrieval 用）：强信号优先，最后退回日常闲聊
SITUATION_CUES = (
    ("冲突升级", r"别这么|你上次|敏感|想太多|凭什么|又是你|翻旧账|能不能别|你都|你总是"),
    ("被忽略", r"加班|忙|没看手机|没回|忘了|没空|失约|赶不上|开会到|又是这样"),
    ("对方道歉", r"错了|对不起|抱歉|道歉|我改|下次一定|补偿|订了"),
    ("亲密靠近", r"想你|记着|记得|照片|抱|牵|喜欢你|爱我|想见"),
    ("深夜低落", r"睡不着|半夜|凌晨|难受|撑不住|搞砸|累|失眠"),
    ("表达关心", r"降温|感冒|生病|发烧|没吃饭|吃药|冷|穿"),
)


def load_drafts(persona_id: str, seeds_dir: Path | None = None) -> list:
    """加载该人格的全部素材：`<id>.txt` 与 `<id>.*.txt`（如 variants），按文件名排序。"""
    from bridge.seeds import parse_seed_text

    root = seeds_dir or SEEDS_DIR
    drafts = []
    for path in sorted(root.glob(f"{persona_id}*.txt")):
        parsed, _ = parse_seed_text(path.read_text(encoding="utf-8"))
        drafts.extend(parsed)
    return drafts


def reply_stats(drafts: list) -> dict:
    lengths = [len(d.reply) for d in drafts]
    return {
        "count": len(drafts),
        "avg": round(statistics.mean(lengths), 1) if lengths else 0,
        "max": max(lengths) if lengths else 0,
        "min": min(lengths) if lengths else 0,
    }


def detect_situation(text: str) -> str:
    for name, pattern in SITUATION_CUES:
        if re.search(pattern, text or ""):
            return name
    return "日常闲聊"


def seed_block(mode: str, drafts: list, *, user_input: str = "") -> tuple[str, list]:
    """生成注入 prompt 的 seeds 段。返回 (文本, 本段用到的 draft 列表)。"""
    if mode not in MODES:
        raise ValueError(f"未知 mode: {mode}（可选 {MODES}）")

    if mode == "full":
        lines = ["## 你以前的真实反应",
                 "这些只是语气样本：学句子的长短、停顿的位置、话说到哪儿就停。",
                 "**不要照抄里面的句子**，也不要把它们当成这类场景的标准答案。"]
        for index, draft in enumerate(drafts, start=1):
            lines += [f"--- 例 {index} [{draft.situation}] ---",
                      f"对方：{draft.scene}",
                      f"你心里：{draft.monologue}",
                      f"你说出口：{draft.reply}"]
        return "\n".join(lines), drafts

    stats = reply_stats(drafts)

    def header(title: str) -> list[str]:
        return [
            f"## {title}",
            "下面是你在类似场景里**心里怎么想的**。学的是思维方式和句子的长短。",
            "",
            f"注意：心里可以想 {MONOLOGUE_LEN[0]}–{MONOLOGUE_LEN[1]} 字，"
            f"但**说出口的话平均只有 {stats['avg']:.0f} 字、最长不超过 {stats['max']} 字**。",
            "绝大多数时候，说出口的比心里想的短一半以上，甚至只剩一句结论。",
            "**不要照抄下面的任何句子** —— 它们描述的是内心，不是你要说的话。",
        ]

    if mode == "mono":
        lines = header("你以前的内心")
        for index, draft in enumerate(drafts, start=1):
            lines += [f"--- 例 {index} [{draft.situation}] ---",
                      f"对方：{draft.scene}",
                      f"你心里：{draft.monologue}"]
        return "\n".join(lines), drafts

    situation = detect_situation(user_input)
    same = [d for d in drafts if d.situation == situation][:3]
    if len(same) < 2:
        same += [d for d in drafts if d.situation != situation][: 2 - len(same)]
    lines = header(f"你在这个场景里的内心（{situation}）")
    for index, draft in enumerate(same, start=1):
        lines += [f"--- 例 {index} [{draft.situation}] ---",
                  f"对方：{draft.scene}",
                  f"你心里：{draft.monologue}"]
    return "\n".join(lines), same


def reply_leak_warnings(drafts: list) -> list[str]:
    """检测 monologue 是否「预告了台词」—— 复读的根因（实测见 docs/exp-fewshot-modes.md）。

    判定：reply 里长度 ≥2 的片段出现在 monologue 中，或 monologue 里出现
    「我要说 / 会说 / 该说」这类直接规划台词的表述。
    """
    warnings: list[str] = []
    strategic = re.compile(r"(我要说|我会说|就该说|得说|要是说)")
    for index, draft in enumerate(drafts, start=1):
        for piece in re.split(r"[，。？！、；]", draft.reply):
            piece = piece.strip()
            if len(piece) >= 2 and piece in draft.monologue:
                warnings.append(f"例{index}【{draft.situation}】monologue 含回复原词「{piece}」")
        match = strategic.search(draft.monologue)
        if match:
            warnings.append(
                f"例{index}【{draft.situation}】monologue 出现「{match.group(0)}」，"
                f"等于在规划台词"
            )
    return warnings


__all__ = [
    "MODES", "DEFAULT_MODE", "MONOLOGUE_LEN", "SITUATION_CUES",
    "load_drafts", "reply_stats", "detect_situation", "seed_block", "reply_leak_warnings",
]
