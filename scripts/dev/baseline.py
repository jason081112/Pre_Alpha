"""#17/#18 基线：luna + iris 各 5 轮，记录时延、token 与回复。"""

import json
import os
import time
from pathlib import Path

import httpx

BASE = os.getenv("ALPHA_BASE", "http://127.0.0.1:8000")
OUT = Path(os.getenv("ALPHA_BASELINE_OUT", "/tmp/baseline.json"))
USER = f"baseline-{int(time.time())}"
PROMPTS = {
    "luna": [
        "今天有点累，不太想说话。",
        "那你陪我一会吧。",
        "你觉得我今天状态怎么样？",
        "我有点想你了。",
        "晚安。",
    ],
    "iris": [
        "在吗？",
        "今天发生了一件小事，想跟你说。",
        "你会记得我说过的话吗？",
        "夸夸我。",
        "明天见。",
    ],
}


def main():
    client = httpx.Client(timeout=180)
    headers = {"X-Alpha-User": USER}

    r = client.put(
        BASE + "/api/me/routing",
        json={"provider": "ollama-cloud", "model": "gemma4:31b", "fallback": []},
        headers=headers,
    )
    r.raise_for_status()
    print(f"user={USER} route=ollama-cloud:gemma4:31b", flush=True)

    rows = []
    for persona, prompts in PROMPTS.items():
        session_id = None
        for index, message in enumerate(prompts, 1):
            payload = {
                "message": message,
                "persona_id": persona,
                "user_name": USER,
                "client_id": "baseline",
            }
            if session_id:
                payload["session_id"] = session_id
            started = time.monotonic()
            r = client.post(BASE + "/api/chat", json=payload, headers=headers)
            wall_ms = int((time.monotonic() - started) * 1000)
            data = r.json()
            session_id = data.get("session_id") or session_id
            reply = str(data.get("response", ""))
            usage = data.get("usage") or {}
            rows.append(
                {
                    "persona": persona,
                    "round": index,
                    "message": message,
                    "reply": reply,
                    "reply_chars": len(reply),
                    "latency_ms": data.get("latency_ms", wall_ms),
                    "wall_ms": wall_ms,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "route_used": data.get("route_used"),
                    "session_id": session_id,
                    "relationship": data.get("relationship"),
                    "mood": data.get("mood"),
                }
            )
            print(
                f"{persona} r{index} {rows[-1]['latency_ms']}ms tok={rows[-1]['total_tokens']} "
                f"chars={rows[-1]['reply_chars']}",
                flush=True,
            )
            time.sleep(1)

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"user": USER, "rows": rows}, fh, ensure_ascii=False, indent=2)

    print("\npersona  rounds  avg_ms  avg_tokens", flush=True)
    for persona in PROMPTS:
        subset = [r for r in rows if r["persona"] == persona]
        avg_ms = sum(r["latency_ms"] for r in subset) // len(subset)
        toks = [r["total_tokens"] for r in subset if r["total_tokens"] is not None]
        avg_tok = sum(toks) // len(toks) if toks else "n/a"
        print(f"{persona:8} {len(subset):6} {avg_ms:7} {avg_tok:>11}", flush=True)
    missing = [r for r in rows if r["total_tokens"] is None]
    print(f"tokens_missing={len(missing)}", flush=True)


if __name__ == "__main__":
    main()
