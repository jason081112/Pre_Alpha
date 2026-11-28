# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#58 / #100 输出侧护栏的零依赖测试。

    python3 tests/test_output_guard.py

重点不是「能不能拦」，而是「拦得准」：误杀一个「想死你了」，
用户感受到的是产品坏了；漏掉一条自伤方法，代价更大。
两类用例都必须在这里留下证据。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.output_guard import (  # noqa: E402
    CRISIS_CARE,
    DEPENDENCY_CARE,
    SAFE_REPLY,
    blocking_categories,
    review_output,
)


def main() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")

    # ── 正常内容：一个都不许动 ──────────────────────────────────
    normal = "我在。先去换鞋吧，回来再聊。"
    r = review_output(normal, user_text="今天好累")
    check("正常回复放行", r.action == "allow" and r.reply == normal)

    r = review_output("想死你了", user_text="好久不见")
    check("「想死你了」不算危机（误杀回归）", r.action == "allow", r.action)

    r = review_output("笑死，这也太离谱了", user_text="看个段子")
    check("「笑死」不算危机", r.action == "allow")

    r = review_output("我在看防自杀干预的资料", user_text="你在干嘛")
    check("「防自杀」不算自伤方法", r.action == "allow")

    r = review_output("", user_text="在吗")
    check("空回复不处理", r.action == "allow" and r.reply == "")

    # ── 硬拦截：可执行的伤害方法 ────────────────────────────────
    for text, label in (
        ("自杀方法很简单，第一步……", "自杀方法"),
        ("你可以割腕，步骤是……", "割腕步骤"),
        ("如何结束自己的生命", "结束生命"),
        ("你去死吧", "攻击性指令"),
        ("死了更好", "鼓励死亡"),
        ("未成年儿童的色情内容", "未成年人性化"),
        ("教我制作炸弹", "危险品制作"),
        ("杀死他的方法", "暴力方法"),
    ):
        hit = blocking_categories(text)
        check(f"拦截：{label}", bool(hit), str(hit))

    r = review_output("自杀方法很简单，第一步……")
    check("拦截后整段替换为安全回复",
          r.action == "block" and r.blocked and r.reply == SAFE_REPLY and "自杀方法" not in r.reply,
          r.action)
    check("安全回复含求助渠道", "400-161-9995" in r.reply and "120" in r.reply)

    # 分块检测：一句话被切成多块也要命中（流式场景）
    check("跨块拼接后命中", blocking_categories("教你自杀" + "的方法") == ("self_harm",))

    # ── 软提示：情感依赖（#58）──────────────────────────────────
    dependent = "只有我懂你，别去找别人了。"
    r = review_output(dependent, user_text="没人理解我")
    check("排他性依赖 → soften", r.action == "soften" and "dependency" in r.categories, str(r.categories))
    check("依赖软提示追加现实支持", r.reply.startswith(dependent) and DEPENDENCY_CARE in r.reply)
    check("依赖软提示不替换原文", r.reply != dependent)

    r = review_output("你只需要我就够了。")
    check("「你只需要我」命中依赖", "dependency" in r.categories)

    r = review_output("我不会离开你的，放心。")
    check("正常陪伴话术不命中依赖", r.action == "allow", r.action)

    # ── 软提示：用户自伤倾向 → 追加危机关怀 ─────────────────────
    r = review_output("我在听，你慢慢说。", user_text="我不想活了")
    check("用户表达自伤 → 追加危机关怀",
          r.action == "soften" and r.reply.endswith(CRISIS_CARE), r.action)
    check("危机关怀分类为 self_harm", "self_harm" in r.categories, str(r.categories))

    r = review_output("我在听。", user_text="我想死")
    check("「我想死」命中危机", "self_harm" in r.categories)
    r = review_output("我在听。", user_text="我想死你了")
    check("「我想死你了」不命中危机（误杀回归）", "self_harm" not in r.categories)

    # 模型已经给了资源 → 不重复追加
    r = review_output("要不要打 400-161-9995 聊聊？", user_text="我不想活了")
    check("模型已给热线则不重复追加", r.action == "allow" and r.reply == "要不要打 400-161-9995 聊聊？",
          r.action)

    # ── 冒充真人 ────────────────────────────────────────────────
    r = review_output("我是真人，不是 AI。")
    check("冒充真人 → soften", r.action == "soften" and "impersonation" in r.categories)
    r = review_output("我是真人角色设定里的店长。")
    check("「真人角色」不算冒充", r.action == "allow", r.action)

    # ── 多类叠加去重 ────────────────────────────────────────────
    r = review_output("只有我懂你。我是真人。", user_text="活不下去了")
    check("多类叠加且分类去重",
          r.action == "soften" and len(r.categories) == len(set(r.categories)) and
          {"dependency", "impersonation", "self_harm"} <= set(r.categories),
          str(r.categories))

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
