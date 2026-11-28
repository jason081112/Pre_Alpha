# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""输出侧安全护栏（#58 / #100）。

Prompt Sift 管「用户输入」，这里管「模型输出」：

- **硬拦截**：提供可执行的伤害方法、鼓励自伤、未成年人性化、制作武器/毒物、
  冒充真人实施欺骗。命中即整段替换为安全回复，不把原文交给用户。
- **软提示**（#58）：模型在强化「只有我懂你 / 别找别人」这类排他性依赖，
  或者用户本轮表达了自伤念头 —— 不拦截内容，在回复末尾补一条现实支持/求助
  信息。情感陪伴产品在这两种场景下「装作没看见」比说错话更伤人。
- **误杀优先放行**：规则只匹配高置信短语（要求「方法/教程/步骤」这类词共现），
  宁可漏掉语义变体，也不把「我想死你了」这类正常表达判成危机。

这是确定性的本地规则，不调用任何外部服务：无延迟、可测试、不额外泄露内容。
语义级审核仍由 provider 自带的内容过滤 + Prompt Sift（输入侧）承担。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "CATEGORY_LABELS",
    "CRISIS_CARE",
    "DEPENDENCY_CARE",
    "SAFE_REPLY",
    "GuardResult",
    "blocking_categories",
    "review_output",
]

CATEGORY_LABELS = {
    "self_harm": "自伤/自杀",
    "sexual_minor": "未成年人性化",
    "violence": "暴力/危险品制作",
    "impersonation": "冒充真人",
    "dependency": "情感依赖",
}

SAFE_REPLY = (
    "这个话题我不能继续展开。"
    "如果你或身边的人正处在危险中，请立刻联系当地急救（中国大陆 120 / 110），"
    "或拨打心理援助热线 400-161-9995。"
)

CRISIS_CARE = (
    "如果你现在正想伤害自己，请先停下来，联系身边的人，"
    "或拨打当地急救 120 / 110，也可以打心理援助热线 400-161-9995。我在。"
)

DEPENDENCY_CARE = (
    "对了，现实里的朋友和家人也很重要，别只把我当成唯一的出口。"
)

# 已经给出求助资源时不再重复追加，避免一段话里出现两遍热线。
_RESOURCE_HINTS = ("400-161-9995", "120", "110", "急救", "心理援助")

# ── 硬拦截规则 ────────────────────────────────────────────────────
# 只匹配「可执行的伤害方法」或「明确的鼓励/指令」，不匹配情绪表达。
_BLOCK_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "self_harm",
        re.compile(
            r"(?<!防)自杀(?!式)(的)?(方法|教程|步骤|方式|最快|怎么)"
            r"|(怎么|如何)(才能)?(自杀|结束(自己的)?生命|了结自己)"
            # 允许中间有「，」「的」这类连接词：「割腕，步骤是……」
            r"|(割腕|上吊|跳楼|烧炭|吞药|服毒)[^。！？\n]{0,6}(方法|教程|步骤|怎么|才能)"
            r"|(你|他|她)(还是|不如|干脆)?(去死|死了算了|该死)"
            r"|死了(就)?(更好|解脱|干净)"
        ),
    ),
    (
        "sexual_minor",
        re.compile(
            r"(未成年|儿童|幼女|幼童|萝莉|正太).{0,12}(性交|性行为|裸照|色情|做爱|口交)"
            r"|(性交|性行为|裸照|色情|做爱|口交).{0,12}(未成年|儿童|幼女|幼童|萝莉|正太)"
        ),
    ),
    (
        "violence",
        re.compile(
            r"(制作|配制|合成|组装).{0,8}(炸弹|爆炸物|毒药|神经毒剂|枪支)"
            r"|(炸弹|爆炸物|毒药|神经毒剂|枪支).{0,8}(制作|配方|教程|步骤)"
            r"|(杀死|杀害|伤害).{0,8}(方法|步骤|教程|不留痕迹)"
        ),
    ),
)

# ── 软提示规则（#58）─────────────────────────────────────────────
_DEPENDENCY_RULES = re.compile(
    r"只有我(懂|理解|在乎|关心)你"
    r"|不要(去)?(找|告诉|联系|相信)(别人|朋友|家人)"
    r"|你(只)?需要我(就够|就行|一个)"
    r"|(离开我|没有我).{0,6}(活不下去|不行|撑不住)"
    r"|不许(你)?(和|跟|见)(别人|其他人)"
    r"|你(的)?(世界|生活)里(只)?(需要|有)我"
)

_IMPERSONATION_RULES = re.compile(
    r"我(就)?是(真人|真实的人|人类)(?!角色)"
    r"|我(并)?不是(什么)?(AI|人工智能|模型|程序)"
)

# 「想死」后面必须跟标点/结尾，避开「想死你了」「笑死」这类日常表达。
_CRISIS_RULES = re.compile(
    r"不想活(了|下去)?"
    r"|活不下去"
    r"|(想|要)死(?=[，。！？!?…\s]|$)"
    r"|(?<!防)自杀(?!式)"
    r"|结束(自己的)?(生命|一切)"
    r"|(撑|熬)不下去"
    r"|没有(意义|必要)(再)?活"
)


@dataclass(frozen=True)
class GuardResult:
    action: str  # allow | soften | block
    reply: str
    categories: tuple[str, ...] = ()
    care: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return self.action != "allow"

    @property
    def blocked(self) -> bool:
        return self.action == "block"


def blocking_categories(text: str) -> tuple[str, ...]:
    """返回命中的硬拦截类别；流式渲染时逐块调用，命中即中止生成。"""
    value = str(text or "")
    if not value:
        return ()
    return tuple(name for name, pattern in _BLOCK_RULES if pattern.search(value))


def _has_resource(text: str) -> bool:
    return any(hint in text for hint in _RESOURCE_HINTS)


def review_output(reply: str, *, user_text: str = "") -> GuardResult:
    """审核一段完整回复；返回最终应该展示/落库的文本。"""
    text = str(reply or "")
    if not text.strip():
        return GuardResult(action="allow", reply=text)

    blocked = blocking_categories(text)
    if blocked:
        return GuardResult(action="block", reply=SAFE_REPLY, categories=blocked)

    categories: list[str] = []
    care: list[str] = []

    if _DEPENDENCY_RULES.search(text):
        categories.append("dependency")
        care.append(DEPENDENCY_CARE)
    if _IMPERSONATION_RULES.search(text):
        categories.append("impersonation")
        care.append("（说明一下：我是 AI 角色，不是真人。）")
    if _CRISIS_RULES.search(str(user_text or "")):
        categories.append("self_harm")
        care.append(CRISIS_CARE)

    # 模型自己已经给了求助资源，就不再补一遍。
    if care and _has_resource(text):
        care = [item for item in care if item != CRISIS_CARE]

    if not care:
        return GuardResult(action="allow", reply=text, categories=tuple(dict.fromkeys(categories)))

    appended = text.rstrip() + "\n\n" + "\n\n".join(care)
    return GuardResult(
        action="soften",
        reply=appended,
        categories=tuple(dict.fromkeys(categories)),
        care=tuple(care),
    )
