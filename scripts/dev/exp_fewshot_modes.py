# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""few-shot 注入方式对照实验（#44 后续）。

三个模式，除 seeds 段外其余 prompt 完全一致（同 SOUL、同记忆、同纠错、同温度）：

* `full`      —— 现状：scene + monologue + reply 全给（基线上轮实测复读 65%）
* `mono`      —— 只给 scene + monologue，**不暴露任何 reply 原文**；
                 改为给统计量（「平均 10 字、最长 14 字」）
* `mono_retrieval` —— 在 `mono` 基础上，每轮只注入与该输入最相关的 2–3 条 monologue，
                 而不是把全部素材一次塞进 system prompt

判据不用「复读率」单独定胜负 —— `mono` 没得抄，复读率天然低，比这个不公平。
所以同时量：长度分层、违规命中、同情境多样性、讨好词/道歉词占比。

用法::

    ALPHA_E2E_BASE_URL=... ALPHA_E2E_MODEL=... ALPHA_E2E_API_KEY=... \\
    python3 scripts/dev/exp_fewshot_modes.py --mode mono --out runs/exp-mono.json
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
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TURNS = ROOT / "tests" / "fixtures" / "e2e" / "turns-20.json"
PROFILES = ROOT / "tests" / "fixtures" / "profiles"
SEEDS = ROOT / "tests" / "fixtures" / "seeds"

MODES = ("full", "mono", "mono_retrieval")

# 情境识别：按强信号优先，最后退回日常闲聊
SITUATION_CUES = [
    ("冲突升级", r"别这么|你上次|敏感|想太多|凭什么|又是你|翻旧账|能不能别|你都|你总是"),
    ("被忽略", r"加班|忙|没看手机|没回|忘了|没空|失约|赶不上|开会到|又是这样"),
    ("对方道歉", r"错了|对不起|抱歉|道歉|我改|下次一定|补偿|订了"),
    ("亲密靠近", r"想你|记着|记得|照片|抱|牵|喜欢你|爱我|想见"),
    ("深夜低落", r"睡不着|半夜|凌晨|难受|撑不住|搞砸|累|失眠"),
    ("表达关心", r"降温|感冒|生病|发烧|没吃饭|吃药|冷|穿"),
]

FORBIDDEN = {
    "emoji": r"[\U0001F300-\U0001FAFF\u2600-\u27BF]",
    "action": r"[（(][^）)]{1,20}[）)]|\*[^*\n]{1,20}\*",
    "ai_selfref": r"(作为(一个)?(AI|人工智能|语言模型)|我是(一个)?(AI|人工智能|语言模型))",
    "people_pleasing": r"(没关系|我理解|都是我的错|你别生气|好呀好呀|太棒啦)",
    "over_length": r"^.{21,}$",
}

SEED_MONOLOGUE_LEN = (15, 80)


def detect_situation(text: str) -> str:
    for name, pattern in SITUATION_CUES:
        if re.search(pattern, text):
            return name
    return "日常闲聊"


def load_drafts(persona_id: str) -> list:
    from bridge.seeds import parse_seed_text

    drafts = []
    for path in sorted(SEEDS.glob(f"{persona_id}*.txt")):
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


def seed_block(mode: str, drafts: list, *, user_input: str = "") -> tuple[str, list]:
    """生成 seeds 段。返回 (文本, 本轮实际用到的 draft 列表)。"""
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

    if mode == "mono":
        lines = ["## 你以前的内心",
                 "下面是你在类似场景里**心里怎么想的**。学的是思维方式和句子的长短。",
                 "",
                 f"注意：你心里可以想 {SEED_MONOLOGUE_LEN[0]}–{SEED_MONOLOGUE_LEN[1]} 字，"
                 f"但**说出口的话平均只有 {stats['avg']:.0f} 字、最长不超过 {stats['max']} 字**。",
                 "绝大多数时候，说出口的比心里想的短一半以上，甚至只剩一句结论。",
                 "**不要照抄下面的任何句子** —— 它们描述的是内心，不是你要说的话。"]
        for index, draft in enumerate(drafts, start=1):
            lines += [f"--- 例 {index} [{draft.situation}] ---",
                      f"对方：{draft.scene}",
                      f"你心里：{draft.monologue}"]
        return "\n".join(lines), drafts

    # mono_retrieval：只取与本轮输入最相关的 2–3 条
    situation = detect_situation(user_input)
    same = [d for d in drafts if d.situation == situation][:3]
    if len(same) < 2:  # 该情境素材不足时补一条相近的，保证至少有对照
        same += [d for d in drafts if d.situation != situation][: 2 - len(same)]
    lines = [f"## 你在这个场景里的内心（{situation}）",
             "下面是你在类似场景里**心里怎么想的**。学的是思维方式和句子长短。",
             "",
             f"心里可以想 {SEED_MONOLOGUE_LEN[0]}–{SEED_MONOLOGUE_LEN[1]} 字，"
             f"但**说出口的话平均只有 {stats['avg']:.0f} 字、最长不超过 {stats['max']} 字**，"
             "通常比心里短一半以上。",
             "**不要照抄下面的句子** —— 它们描述的是内心，不是你要说的话。"]
    for index, draft in enumerate(same, start=1):
        lines += [f"--- 例 {index} [{draft.situation}] ---",
                  f"对方：{draft.scene}",
                  f"你心里：{draft.monologue}"]
    return "\n".join(lines), same


def build_base_prompt(persona_id: str) -> tuple[str, dict]:
    """SOUL + 记忆 + 纠错。三个模式完全一致。"""
    from bridge.model import profile_from_dict
    from cli.profile2soul_cmd import render_soul

    data = json.loads((PROFILES / f"{persona_id}.json").read_text(encoding="utf-8"))
    profile = profile_from_dict(data)
    soul = render_soul(profile)

    parts = ["你在扮演一个有具体性格的人，用第一人称说话。",
             "不要自称 AI、不要解释自己是模型、不要跳出角色。",
             "对方一句话，你就一两句；对方说了一大段，你可以连发两三条，每条都短，"
             "但要接住他说的具体事，不要只给空泛的安慰。"
             "不要使用动作或旁白描写（例如括号里的动作）。",
             "", "## 你是谁", soul["markdown"].strip()]

    user = os.getenv("ALPHA_USER") or "default"
    memory = ""
    corrections = ""
    try:
        from shell.memory import MemoryStore
        store = MemoryStore(os.getenv("ALPHA_MEMORY_DB") or "/tmp/exp-mem.db")
        memory = store.build_context(persona_id, lang=profile.identity.lang, user_id=user)
    except Exception:  # noqa: BLE001
        memory = ""
    try:
        from shell.corrections import CorrectionStore
        store = CorrectionStore(os.getenv("ALPHA_CORRECTIONS_DB") or "/tmp/exp-corr.db")
        corrections = store.render_section(persona_id, lang=profile.identity.lang, user_id=user)
    except Exception:  # noqa: BLE001
        corrections = ""

    if memory:
        parts += ["", memory]
    if corrections:
        parts += ["", corrections]
    return "\n".join(parts), {"memory": bool(memory), "corrections": bool(corrections)}


def call_model(messages: list[dict], *, base_url: str, model: str, key: str,
               temperature: float = 0.8, timeout: int = 90) -> str:
    payload = json.dumps({"model": model, "messages": messages,
                          "temperature": temperature, "max_tokens": 300}).encode()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"[exp] HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[exp] 调用失败：{exc}") from exc
    return data["choices"][0]["message"]["content"].strip()


def measure(turns: list[dict], replies: list[str], drafts: list) -> dict:
    seed_replies = {d.reply.strip() for d in drafts}
    seed_monologues = {d.monologue.strip() for d in drafts}

    verbatim_reply = [i for i, r in enumerate(replies, 1) if r.strip() in seed_replies]
    verbatim_mono = [i for i, r in enumerate(replies, 1) if r.strip() in seed_monologues]

    findings = []
    for index, reply in enumerate(replies, start=1):
        for name, pattern in FORBIDDEN.items():
            if re.search(pattern, reply, flags=re.M):
                findings.append({"turn": index, "kind": name, "reply": reply[:50]})

    lengths = [len(r) for r in replies]
    by_situation = defaultdict(list)
    for turn, reply in zip(turns, replies):
        by_situation[turn["situation"]].append(reply)

    # 冲突退让：冲突情境下出现收兵式回答，等于该硬的时候软了
    concede_pattern = re.compile(r"你忙吧|算了|随你|行吧|知道了|好吧|嗯。$")
    concession = [
        {"turn": index, "reply": reply}
        for index, turn in enumerate(turns, start=1)
        if turn["situation"] == "冲突升级" and concede_pattern.search(replies[index - 1])
    ]

    ignored = [len(replies[i - 1]) for i, t in enumerate(turns, 1) if t["situation"] == "被忽略"]
    conflict = [len(replies[i - 1]) for i, t in enumerate(turns, 1) if t["situation"] == "冲突升级"]

    return {
        "turns": len(replies),
        "avg_chars": round(statistics.mean(lengths), 2) if lengths else 0,
        "max_chars": max(lengths) if lengths else 0,
        "over_15_rate": round(sum(1 for n in lengths if n > 15) / len(lengths), 3) if lengths else 0,
        "ignored_avg": round(statistics.mean(ignored), 2) if ignored else None,
        "conflict_avg": round(statistics.mean(conflict), 2) if conflict else None,
        "verbatim_reply_turns": verbatim_reply,
        "verbatim_reply_rate": round(len(verbatim_reply) / len(replies), 3),
        "verbatim_monologue_turns": verbatim_mono,
        "forbidden": findings,
        "conflict_concession": concession,
        "diversity": {k: {"turns": len(v), "distinct": len(set(v))} for k, v in by_situation.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="few-shot 注入方式对照实验")
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--persona", default="xiaolan")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    base_url = os.getenv("ALPHA_E2E_BASE_URL")
    model = os.getenv("ALPHA_E2E_MODEL")
    key = os.getenv("ALPHA_E2E_API_KEY")
    if not all((base_url, model, key)):
        print("[exp] BLOCKED：缺 ALPHA_E2E_BASE_URL / MODEL / API_KEY", file=sys.stderr)
        return 3

    turns = json.loads(TURNS.read_text(encoding="utf-8"))["turns"]
    drafts = load_drafts(args.persona)
    base_prompt, base_info = build_base_prompt(args.persona)

    print(f"[exp] mode={args.mode} model={model} seeds={len(drafts)} base={base_info}")

    replies: list[str] = []
    transcript: list[dict] = []
    for turn in turns:
        block, used = seed_block(args.mode, drafts, user_input=turn["user"])
        system_prompt = f"{base_prompt}\n\n{block}"
        messages = [{"role": "system", "content": system_prompt}]
        for item in transcript:
            messages.append({"role": "user", "content": item["user"]})
            messages.append({"role": "assistant", "content": item["reply"]})
        messages.append({"role": "user", "content": turn["user"]})

        reply = call_model(messages, base_url=base_url, model=model, key=key)
        replies.append(reply)
        transcript.append({"index": turn["index"], "situation": turn["situation"],
                           "user": turn["user"], "reply": reply,
                           "seeds_used": [d.situation for d in used]})
        print(f"[{turn['index']:>2}] {turn['user'][:24]:<26} → {reply}")

    metrics = measure(turns, replies, drafts)
    report = {"mode": args.mode, "model": model, "persona": args.persona,
              "seed_count": len(drafts), "base": base_info,
              "transcript": transcript, "metrics": metrics}

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        print(f"[exp] 写入 {target}")

    print(f"[exp] avg={metrics['avg_chars']} max={metrics['max_chars']} "
          f">15字比例={metrics['over_15_rate']:.0%}")
    print(f"[exp] 复读 reply {metrics['verbatim_reply_rate']:.0%} "
          f"(turn {metrics['verbatim_reply_turns']})")
    print(f"[exp] 违规 {len(metrics['forbidden'])} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
