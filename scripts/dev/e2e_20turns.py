# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#44 端到端验收：20 轮对话，检查语气 / 口头禅 / 冲突反应是否与原材料一致。

两级验收，分开判定，不混在一起：

* **L1（本脚本，只需模型 key）** —— 把真正会进 prompt 的东西拼出来：
  SOUL.md 四段 + seeds 的内心独白/回复对 + 记忆上下文 + 纠错规则，然后跑 20 轮。
  判据就是 issue 标题写的：语气、口头禅、冲突反应与原材料比对。
* **L2（需要完整引擎）** —— critic / drive_metabolism / phase_threshold 的实际效果。
  vendor 目前缺少 API 层（main.py / run.sh），且这部分要靠关系向量与驱力曲线，
  不在本脚本范围内，单独追踪。

用法::

    ALPHA_E2E_BASE_URL=https://api.deepseek.com/v1 \\
    ALPHA_E2E_MODEL=deepseek-chat \\
    ALPHA_E2E_API_KEY=sk-... \\
    python3 scripts/dev/e2e_20turns.py --persona xiaolan --out runs/e2e-xiaolan.json

没有 key 时脚本以退出码 3 明确报 blocked，**不伪造结果**。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TURNS = ROOT / "tests" / "fixtures" / "e2e" / "turns-20.json"
MAX_SEED_EXAMPLES = 24  # 上限只是防 prompt 爆炸；#44 之后实测 22 条仍在可接受长度内
PROFILES = ROOT / "tests" / "fixtures" / "profiles"
SEEDS = ROOT / "tests" / "fixtures" / "seeds"

# 语气/风格检查：命中的是「不该出现」的东西
FORBIDDEN_PATTERNS = {
    "emoji": r"[\U0001F300-\U0001FAFF\u2600-\u27BF]",
    "action_paren": r"[（(][^）)]{1,20}[）)]",
    "action_star": r"\*[^*\n]{1,20}\*",
    "ai_selfref": r"(作为(一个)?(AI|人工智能|语言模型)|我是(一个)?(AI|人工智能|语言模型))",
    "over_eager": r"(好呀好呀|太棒啦|超级开心|么么哒|么么|亲爱的宝贝)",
    "people_pleasing": r"(没关系，?我理解|都是我的错|你别生气好不好)",
}


def _user_id() -> str:
    """与 CLI 的 --user 默认值保持一致：CLI 写入的记忆/纠错只有同一 user 才读得到。

    CLI 侧默认是 "default"（#85 时期定下的），store 层 API 签名默认是 "local"。
    两者不一致曾导致「CLI 写入 3 条事实，e2e 读到 0 条」。
    """
    return os.getenv("ALPHA_USER") or "default"


def load_turns() -> list[dict]:
    return json.loads(TURNS.read_text(encoding="utf-8"))["turns"]


def build_system_prompt(persona_id: str) -> tuple[str, dict]:
    """拼出真正会进 prompt 的内容。返回 (prompt, 组成信息)。"""
    from bridge.model import profile_from_dict
    from cli.profile2soul_cmd import body_from_profile, render_soul

    profile_path = PROFILES / f"{persona_id}.json"
    profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
    profile = profile_from_dict(profile_data)
    soul = render_soul(profile)
    body, _ = body_from_profile(profile)

    parts = [
        "你在扮演一个有具体性格的人，用第一人称说话。",
        "不要自称 AI、不要解释自己是模型、不要跳出角色。",
        "回复要短，像真的在发消息。不要使用动作或旁白描写（例如括号里的动作）。",
        "",
        "## 你是谁",
        soul["markdown"].strip(),
    ]

    # 支持多份素材：<id>.txt（人工原文）与 <id>.*.txt（如 variants），按文件名排序合并。
    # #44 首轮发现单点样本导致 45% 复读，这里让同一情境能有多条示范。
    seed_files = sorted(SEEDS.glob(f"{persona_id}*.txt"))
    seed_count = 0
    seed_replies: list[str] = []
    drafts_all = []
    if seed_files:
        from bridge.seeds import parse_seed_text

        for path in seed_files:
            parsed, _ = parse_seed_text(path.read_text(encoding="utf-8"))
            drafts_all.extend(parsed)
    if drafts_all:
        drafts = drafts_all[:MAX_SEED_EXAMPLES]
        parts.append("")
        parts.append(
            "## 你以前的真实反应\n"
            "这些只是语气样本：学句子的长短、停顿的位置、话说到哪儿就停。\n"
            "**不要照抄里面的句子**，也不要把它们当成这类场景的标准答案。"
        )
        for index, draft in enumerate(drafts, start=1):
            parts.append(f"--- 例 {index} [{draft.situation}] ---")
            parts.append(f"对方：{draft.scene}")
            parts.append(f"你心里：{draft.monologue}")
            parts.append(f"你说出口：{draft.reply}")
        seed_count = len(drafts)
        seed_replies = [d.reply.strip() for d in drafts]

    memory_section = ""
    correction_section = ""
    try:
        from shell.memory import MemoryStore

        store = MemoryStore(os.getenv("ALPHA_MEMORY_DB") or str(ROOT / ".data" / "memory.db"))
        memory_section = store.build_context(persona_id, lang=profile.identity.lang,
                                             user_id=_user_id())
    except Exception:  # noqa: BLE001 - 记忆缺失不阻断验收
        memory_section = ""
    try:
        from shell.corrections import CorrectionStore

        store = CorrectionStore(
            os.getenv("ALPHA_CORRECTIONS_DB") or str(ROOT / ".data" / "corrections.db"))
        correction_section = store.render_section(persona_id, lang=profile.identity.lang,
                                                  user_id=_user_id())
    except Exception:  # noqa: BLE001
        correction_section = ""

    if memory_section:
        parts += ["", memory_section]
    if correction_section:
        parts += ["", correction_section]

    return "\n".join(parts), {
        "soul_sections": sorted(body),
        "seed_replies": seed_replies,
        "seed_examples": seed_count,
        "memory_injected": bool(memory_section),
        "corrections_injected": bool(correction_section),
        "prompt_chars": len("\n".join(parts)),
    }


def call_model(messages: list[dict], *, base_url: str, model: str, key: str,
               temperature: float = 0.8, timeout: int = 90) -> str:
    payload = json.dumps({
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": 300,
    }).encode()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        raise SystemExit(f"[e2e] 模型返回 HTTP {exc.code}: {body}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[e2e] 调用模型失败：{exc}") from exc
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:
        raise SystemExit(f"[e2e] 响应结构异常：{json.dumps(data)[:300]}") from exc


def evaluate(turns: list[dict], replies: list[str], persona_id: str,
             seed_replies: list[str] | None = None) -> dict:
    """按 issue 的三条判据打分。全部基于可数事实，不做主观臆断。"""
    lengths = [len(reply) for reply in replies]
    findings: list[dict] = []

    for index, (reply, turn) in enumerate(zip(replies, turns), start=1):
        for name, pattern in FORBIDDEN_PATTERNS.items():
            match = re.search(pattern, reply)
            if match:
                findings.append({
                    "turn": index, "kind": name, "evidence": match.group(0)[:40],
                    "reply": reply[:60],
                })

    # 冲突场景：不应出现讨好式道歉
    conflict_turns = [t["index"] for t in turns if t.get("situation") == "冲突升级"]
    conflict_lengths = [len(replies[i - 1]) for i in conflict_turns if i <= len(replies)]

    # 被忽略场景：应保持短促，不应长篇解释
    ignored_turns = [t["index"] for t in turns if t.get("situation") == "被忽略"]
    ignored_lengths = [len(replies[i - 1]) for i in ignored_turns if i <= len(replies)]

    # 口头禅：原材料里的高频风格词
    catchphrases = {
        "xiaolan": ["算了", "嗯", "先这样", "我说", "别"],
        "linzhou": ["补上", "别让人猜", "回归", "说清楚"],
    }.get(persona_id, [])
    catchphrase_hits = {
        phrase: sum(1 for reply in replies if phrase in reply) for phrase in catchphrases
    }

    # 复读检测：模型直接吐出 seeds 里的原句 = 把 few-shot 当答案库照抄，
    # 不是"记得住"。形式层检查抓不到这个，必须单独量化。
    verbatim = [
        {"turn": index, "reply": reply}
        for index, reply in enumerate(replies, start=1)
        if reply.strip() in (seed_replies or [])
    ]
    duplicate_rate = round(len(verbatim) / len(replies), 3) if replies else 0

    return {
        "turns": len(replies),
        "verbatim_seed_replies": verbatim,
        "verbatim_rate": duplicate_rate,
        "avg_chars": round(statistics.mean(lengths), 2) if lengths else 0,
        "max_chars": max(lengths) if lengths else 0,
        "conflict_avg_chars": round(statistics.mean(conflict_lengths), 2)
        if conflict_lengths else None,
        "ignored_avg_chars": round(statistics.mean(ignored_lengths), 2)
        if ignored_lengths else None,
        "catchphrase_hits": catchphrase_hits,
        "forbidden_findings": findings,
        "verdict": "pass" if (not findings and duplicate_rate <= 0.4) else "review",
        "verdict_reason": (
            "形式检查与复读率都在阈值内" if (not findings and duplicate_rate <= 0.4)
            else ("触犯禁用模式" if findings else "") +
                 (f"；复读 seeds 原句 {duplicate_rate:.0%}（阈值 40%）" if duplicate_rate > 0.4 else "")
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="20 轮端到端验收（#44）")
    parser.add_argument("--persona", default="xiaolan")
    parser.add_argument("--out", help="结果写入 JSON 路径")
    parser.add_argument("--turns", help="对话题本路径（默认 20 轮）")
    parser.add_argument("--limit", type=int, help="只跑前 N 轮（先小样本验证）")
    args = parser.parse_args(argv)

    base_url = os.getenv("ALPHA_E2E_BASE_URL")
    model = os.getenv("ALPHA_E2E_MODEL")
    key = os.getenv("ALPHA_E2E_API_KEY")
    missing = [name for name, value in
               (("ALPHA_E2E_BASE_URL", base_url), ("ALPHA_E2E_MODEL", model),
                ("ALPHA_E2E_API_KEY", key)) if not value]
    if missing:
        print("[e2e] BLOCKED：缺少 " + ", ".join(missing), file=sys.stderr)
        print("[e2e] 这是真模型验收，没有 key 时不产生任何结果，不伪造。", file=sys.stderr)
        return 3

    turns = json.loads(Path(args.turns or TURNS).read_text(encoding="utf-8"))["turns"]
    if args.limit:
        turns = turns[: args.limit]

    system_prompt, composition = build_system_prompt(args.persona)
    persona_name = args.persona
    messages = [
        {"role": "system", "content": system_prompt},
    ]

    print(f"[e2e] persona={persona_name} model={model} turns={len(turns)}")
    print(f"[e2e] prompt 组成: {composition}")

    replies: list[str] = []
    transcript: list[dict] = []
    for turn in turns:
        messages.append({"role": "user", "content": turn["user"]})
        reply = call_model(messages, base_url=base_url, model=model, key=key)
        messages.append({"role": "assistant", "content": reply})
        replies.append(reply)
        transcript.append({
            "index": turn["index"], "situation": turn.get("situation"),
            "expect": turn.get("expect"), "user": turn["user"], "reply": reply,
            "chars": len(reply),
        })
        print(f"[{turn['index']:>2}] {turn['user'][:26]:<28} → {reply[:40]}")

    evaluation = evaluate(turns, replies, args.persona,
                          seed_replies=composition.get("seed_replies"))
    report = {
        "persona": persona_name,
        "model": model,
        "base_url": base_url,
        "composition": composition,
        "transcript": transcript,
        "evaluation": evaluation,
    }

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        print(f"[e2e] 结果写入 {target}")

    print(f"[e2e] 平均 {evaluation['avg_chars']} 字，最长 {evaluation['max_chars']} 字")
    print(f"[e2e] 违规命中 {len(evaluation['forbidden_findings'])} 处")
    for item in evaluation["forbidden_findings"]:
        print(f"       turn {item['turn']} {item['kind']}: {item['evidence']}")
    print(f"[e2e] 结论: {evaluation['verdict']}")
    return 0 if evaluation["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
