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
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TURNS = ROOT / "tests" / "fixtures" / "e2e" / "turns-20.json"

# 引擎原生输出协议，原文照抄 vendor/OpenHer/engine/prompts/actor_single.md 的【输出格式】段。
# 不自己改写措辞 —— 协议是对接契约，不是风格文案。
OPENHER_PROTOCOL = """

[输出格式]
按以下格式输出：
【内心独白】
你此刻脑海中真实浮现的念头。
【最终回复】
你实际说出口的话，不一定等于内心想法，只写对话文字。
纯文字可选多条拆分（此时回复须空行分段）。静默表示不说话。
【表达方式】
文字 / 语音 / 表情 / 照片 / 静默（可组合）"""
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


def build_system_prompt(persona_id: str, seed_mode: str = "mono",
                        protocol: str = "openher") -> tuple[str, dict]:
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

    # seeds 段交给 bridge.fewshot 统一生成。默认 mono（只给内心，不给 reply 原句）：
    # #44 实测 full 模式复读率 65%，mono 降到 15%（docs/exp-fewshot-modes.md）。
    from bridge.fewshot import load_drafts, reply_stats, seed_block

    drafts = load_drafts(persona_id)
    seed_count = 0
    used: list = []
    if drafts:
        block, used = seed_block(seed_mode, drafts)
        parts += ["", block]
        seed_count = len(used)

    # 复读检测始终对照**全量** reply（mono 模式没注入 reply，但模型可能自己"猜"回来）
    seed_replies = [d.reply.strip() for d in drafts]
    seed_stats = reply_stats(drafts) if drafts else {}

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

    if protocol == "openher":
        parts.append(OPENHER_PROTOCOL)

    return "\n".join(parts), {
        "protocol": protocol,
        "soul_sections": sorted(body),
        "seed_replies": seed_replies,
        "seed_examples": seed_count,
        "seed_mode": seed_mode,
        "seeds_used": [d.situation for d in used],
        "reply_stats": seed_stats,
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
    # 网络抖动/限流很常见（实测 ollama cloud 偶发 SSL handshake timeout），
    # 单次失败不该让 20 轮全废 —— 退避重试 3 次。
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode())
            break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:300]
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(4 * (attempt + 1))
                last_error = exc
                continue
            raise SystemExit(f"[e2e] 模型返回 HTTP {exc.code}: {body}") from exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                time.sleep(4 * (attempt + 1))
                continue
            raise SystemExit(f"[e2e] 调用模型失败（已重试 3 次）：{exc}") from exc
    else:
        raise SystemExit(f"[e2e] 调用模型失败（已重试 3 次）：{last_error}")
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:
        raise SystemExit(f"[e2e] 响应结构异常：{json.dumps(data)[:300]}") from exc


def evaluate(turns: list[dict], replies: list[str], persona_id: str,
             seed_replies: list[str] | None = None,
             transcript: list[dict] | None = None) -> dict:
    """统计客观量，不预设「多长才算对」—— 长度阈值待定，先在报告里摊开数据。"""
    import statistics

    rows = transcript or []
    lengths = [len(reply) for reply in replies]

    findings: list[dict] = []
    for index, reply in enumerate(replies, start=1):
        for name, pattern in FORBIDDEN_PATTERNS.items():
            match = re.search(pattern, reply)
            if match:
                findings.append({
                    "turn": index, "kind": name, "evidence": match.group(0)[:40],
                    "reply": reply[:60],
                })

    seed_set = {r.strip() for r in (seed_replies or [])}
    verbatim = [{"turn": i, "reply": r} for i, r in enumerate(replies, start=1)
                if r.strip() in seed_set]
    duplicate_rate = round(len(verbatim) / len(replies), 3) if replies else 0

    # 按输入长度分档：短问 vs 长倾诉，两者该有不同的回复形态
    short = [r for r in rows if r["input_chars"] < 20]
    long_ = [r for r in rows if r["input_chars"] >= 60]

    def band(items: list[dict]) -> dict:
        if not items:
            return {"n": 0}
        return {
            "n": len(items),
            "avg_input": round(statistics.mean(i["input_chars"] for i in items), 1),
            "avg_segments": round(statistics.mean(i["segment_count"] for i in items), 2),
            "avg_total_chars": round(statistics.mean(i["chars"] for i in items), 1),
            "avg_seg_chars": round(statistics.mean(
                i["chars"] / i["segment_count"] if i["segment_count"] else 0
                for i in items), 1),
            "max_segments": max(i["segment_count"] for i in items),
            "detail_hit_rate": round(
                sum(1 for i in items if i["detail"]["hit"]) / len(items), 2),
        }

    conflict = [r for r in rows if r.get("situation") == "冲突升级"]
    ignored = [r for r in rows if r.get("situation") == "被忽略"]

    return {
        "turns": len(replies),
        "avg_chars": round(statistics.mean(lengths), 2) if lengths else 0,
        "max_chars": max(lengths) if lengths else 0,
        "avg_segments": round(statistics.mean(r["segment_count"] for r in rows), 2) if rows else 0,
        "avg_seg_chars": round(statistics.mean(
            r["chars"] / r["segment_count"] if r["segment_count"] else 0 for r in rows), 1)
        if rows else 0,
        "band_short": band(short),
        "band_long": band(long_),
        "detail_hit_rate": round(sum(1 for r in rows if r["detail"]["hit"]) / len(rows), 2)
        if rows else 0,
        "conflict_avg_chars": round(statistics.mean(len(r["reply"]) for r in conflict), 2)
        if conflict else None,
        "ignored_avg_chars": round(statistics.mean(len(r["reply"]) for r in ignored), 2)
        if ignored else None,
        "verbatim_seed_replies": verbatim,
        "verbatim_rate": duplicate_rate,
        "forbidden_findings": findings,
        # 长度维度暂不参与判定：阈值等口径定了再启用
        "verdict": "pass" if (not findings and duplicate_rate <= 0.4) else "review",
        "verdict_reason": "形式检查（违规/复读）在阈值内；长度与接住率仅统计，未参与判定",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="20 轮端到端验收（#44）")
    parser.add_argument("--persona", default="xiaolan")
    parser.add_argument("--out", help="结果写入 JSON 路径")
    parser.add_argument("--turns", help="对话题本路径（默认 20 轮）")
    parser.add_argument("--limit", type=int, help="只跑前 N 轮（先小样本验证）")
    parser.add_argument("--protocol", default="openher", choices=["openher", "raw"],
                        help="openher=按引擎原生协议输出（内心/多段回复/表达方式）；raw=单段")
    parser.add_argument("--seed-mode", default="mono", choices=["full", "mono", "mono_retrieval"],
                        help="few-shot 注入方式，默认 mono（见 docs/exp-fewshot-modes.md）")
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

    system_prompt, composition = build_system_prompt(args.persona, args.seed_mode, args.protocol)
    persona_name = args.persona
    messages = [
        {"role": "system", "content": system_prompt},
    ]

    print(f"[e2e] persona={persona_name} model={model} turns={len(turns)} "
          f"seed_mode={args.seed_mode}")
    print(f"[e2e] prompt 组成: {composition}")

    from bridge.protocol import detail_hit, parse_output

    replies: list[str] = []
    transcript: list[dict] = []
    for turn in turns:
        messages.append({"role": "user", "content": turn["user"]})
        raw = call_model(messages, base_url=base_url, model=model, key=key)
        out = parse_output(raw)
        # 后续轮次把原始输出（含内心独白）回填，保持与引擎一致的多轮形态
        messages.append({"role": "assistant", "content": raw})
        replies.append("\n\n".join(out.segments))
        transcript.append({
            "index": turn["index"], "situation": turn.get("situation"),
            "expect": turn.get("expect"), "user": turn["user"],
            "reply": "\n\n".join(out.segments),
            "segments": out.segments,
            "monologue": out.monologue,
            "modality": out.modality,
            "chars": out.total_chars,
            "segment_count": out.segment_count,
            "input_chars": len(turn["user"]),
            "detail": detail_hit(turn["user"], out.segments),
            "raw": raw,
        })
        joined = " / ".join(out.segments)
        print(f"[{turn['index']:>2}] ({len(turn['user']):>3}字→{out.segment_count}段{out.total_chars}字) "
              f"{turn['user'][:16]:<18} → {joined[:44]}")

    evaluation = evaluate(turns, replies, args.persona,
                          seed_replies=composition.get("seed_replies"),
                          transcript=transcript)
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

    print(f"[e2e] 平均 {evaluation['avg_chars']} 字 / {evaluation['avg_segments']} 段"
          f"（每段约 {evaluation['avg_seg_chars']} 字），最长 {evaluation['max_chars']} 字")
    for name, key in (("短问(<20字)", "band_short"), ("长倾诉(>=60字)", "band_long")):
        b = evaluation[key]
        if b.get("n"):
            print(f"[e2e] {name}: {b['n']} 轮 | 输入均 {b['avg_input']} 字 → "
                  f"{b['avg_segments']} 段 / {b['avg_total_chars']} 字 | "
                  f"细节命中 {b['detail_hit_rate']:.0%}")
    print(f"[e2e] 违规命中 {len(evaluation['forbidden_findings'])} 处")
    for item in evaluation["forbidden_findings"]:
        print(f"       turn {item['turn']} {item['kind']}: {item['evidence']}")
    print(f"[e2e] 结论: {evaluation['verdict']}")
    return 0 if evaluation["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
