#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#30 B 部分验收：走 OpenHer /api/chat 真实跑若干轮，检查风格与结构标记泄漏。

用法（需先在 Codespace/CI 启动 OpenHer）：

    scripts/dev/verify_persona_e2e.py --base http://127.0.0.1:8000 --persona xiaolan --turns 3

输出 JSON 到 stdout，含每轮问答、泄漏标记命中、句长指标，供文档回填。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

# 结构标记：出现即视为泄漏（Layer N 用正则，其余精确）
LEAK_PATTERNS = {
    "layer_marker": re.compile(r"Layer\s*\d"),
    "inner_monologue": re.compile(r"【内心独白】"),
    "expression_marker": re.compile(r"【表达方式】"),
    "insufficient": re.compile(r"insufficient source material", re.IGNORECASE),
    "correction_log": re.compile(r"Correction Log"),
    "section_header": re.compile(r"^##\s", re.MULTILINE),
}

DEFAULT_TURNS = [
    "我今晚加班，可能又赶不上吃饭了。",
    "我不是故意的。",
    "我到家了，你路上小心。",
]


def post_json(url: str, payload: dict, timeout: int = 90) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(url: str, timeout: int = 20) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sentence_stats(text: str) -> dict:
    parts = [p for p in re.split(r"[。！？!?\n]+", text) if p.strip()]
    lengths = [len(p.strip()) for p in parts]
    return {
        "sentences": len(parts),
        "avg_len": round(statistics.mean(lengths), 1) if lengths else 0,
        "max_len": max(lengths) if lengths else 0,
        "chars": len(text),
    }


def leaks(text: str) -> list[str]:
    hits = []
    for name, pattern in LEAK_PATTERNS.items():
        match = pattern.search(text)
        if match:
            hits.append(f"{name}:{match.group(0)[:20]}")
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--persona", required=True)
    parser.add_argument("--user", default="xinyu")
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--session")
    parser.add_argument("--message", action="append", default=[])
    args = parser.parse_args()

    base = args.base.rstrip("/")
    personas = get_json(f"{base}/api/personas")
    listing = personas if isinstance(personas, list) else personas.get("personas", [])
    match = [p for p in listing if p.get("persona_id") == args.persona]
    if not match:
        print(json.dumps({"ok": False, "error": f"persona {args.persona} 未加载"}, ensure_ascii=False))
        return 2
    persona_meta = match[0]

    messages = args.message or DEFAULT_TURNS[: args.turns]
    session_id = args.session
    rounds = []
    all_leaks = []

    for index, message in enumerate(messages, start=1):
        payload = {
            "message": message,
            "persona_id": args.persona,
            "user_name": args.user,
            "client_id": "verify-e2e",
        }
        if session_id:
            payload["session_id"] = session_id
        started = time.time()
        try:
            result = post_json(f"{base}/api/chat", payload)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            rounds.append({"round": index, "user": message, "error": f"HTTP {exc.code}: {body}"})
            continue
        elapsed = round(time.time() - started, 2)
        session_id = result.get("session_id", session_id)
        reply = (result.get("reply") or "").strip()
        hits = leaks(reply)
        all_leaks.extend(hits)
        rounds.append(
            {
                "round": index,
                "user": message,
                "assistant": reply,
                "elapsed_s": elapsed,
                "leaks": hits,
                "metrics": sentence_stats(reply),
                "modality": result.get("modality"),
            }
        )

    measured = [r for r in rounds if "metrics" in r]
    ok = bool(rounds) and len(measured) == len(rounds) and not all_leaks
    report = {
        "ok": ok,
        "base": base,
        "persona_id": args.persona,
        "persona_meta": {
            k: persona_meta.get(k)
            for k in ("name", "name_zh", "gender", "age", "mbti", "tags_zh", "description")
        },
        "session_id": session_id,
        "rounds": rounds,
        "leak_hits": sorted(set(all_leaks)),
        "summary": {
            "rounds": len(rounds),
            "errors": len(rounds) - len(measured),
            "avg_chars": round(statistics.mean([r["metrics"]["chars"] for r in measured]), 1)
            if measured
            else 0,
            "avg_sentences": round(
                statistics.mean([r["metrics"]["sentences"] for r in measured]), 1
            )
            if measured
            else 0,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
