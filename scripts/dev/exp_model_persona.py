#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""模型 × 人格表现对照实验（#99）。

假设要检验的问题：**"活人感不足 / 有点傻"是模型能力问题，还是人格约束问题？**

做法：同一个人格、同一段对话，换不同模型跑，比回复的「表演度」。

    python3 scripts/dev/exp_model_persona.py
    python3 scripts/dev/exp_model_persona.py --models gemma4:31b glm-5.3 kimi-k3
    python3 scripts/dev/exp_model_persona.py --ask "好累" --persona luna

指标（可自动算，也可人工读）：

| 指标 | 含义 | 越高越糟 |
|---|---|---|
| 感叹号密度 | `!！` 数 / 字数 | 表演过度 |
| 反问句 | `吗？/吧？/呢？` 结尾的句子数 | 把话题推回用户 |
| 长度 | 平均每段字数 | 与"短句陪伴"背道而驰 |
| 索要回报 | 命中「红包/请我/补偿/奖励/夸我」 | 讨好型助手口吻 |
| 表演词 | 命中「天呐/简直/跳起来/致命/震惊」 | 夸张反应 |

注意：单次采样噪声大。要下结论至少各跑 3 次，看分布而不是单句。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_MODELS = ["ollama-cloud/gemma4:31b", "zhipu/glm-4.7-flash"]
DEFAULT_TURNS = [
    "好的 怎么感觉你一股人机感",
]

PRAISE_WORDS = ("红包", "请我", "补偿", "奖励", "夸我", "表扬")
ACTING_WORDS = ("天呐", "简直", "跳起来", "致命", "震惊", "我的天")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def normalize_route(model: str) -> str:
    """`gemma4:31b` → `ollama-cloud/gemma4:31b`；已带 provider 的原样返回。"""
    return model if "/" in model else f"ollama-cloud/{model}"


def start_app(tmp: Path, model: str) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = {
        **os.environ,
        "ALPHA_AUTH_DISABLED": "1",
        "ALPHA_DATA_DIR": str(tmp),
        "ALPHA_ROUTING_DB": str(tmp / "r.db"),
        "ALPHA_AUTH_DB": str(tmp / "a.db"),
        "ALPHA_LIMITS_DB": str(tmp / "l.db"),
        "ALPHA_AGENT_EXTRA_DB": str(tmp / "e.db"),
        "ALPHA_DEV_ROUTE": model,
        "ALPHA_PORT": str(port),
        "ALPHA_HOST": "127.0.0.1",
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "shell.app"],
        cwd=str(ROOT),
        env=env,
        stdout=(tmp / "log").open("w"),
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        time.sleep(1)
        if proc.poll() is not None:
            raise RuntimeError(f"{model} 启动失败，见 {tmp / 'log'}")
        try:
            if httpx.get(base + "/api/auth/config", timeout=1.0).status_code == 200:
                return proc, base
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError(f"{model} 120 秒未就绪")


def score(text: str) -> dict:
    chars = len(re.sub(r"\s", "", text))
    bangs = len(re.findall(r"[!！]", text))
    questions = len(re.findall(r"(吗？|吧？|呢？)", text))
    paras = [p for p in text.split("\n") if p.strip()]
    return {
        "chars": chars,
        "paras": len(paras),
        "avg_para_chars": round(chars / max(len(paras), 1), 1),
        "bangs": bangs,
        "bang_density": round(bangs / max(chars, 1) * 100, 2),
        "questions": questions,
        "praise": [w for w in PRAISE_WORDS if w in text],
        "acting": [w for w in ACTING_WORDS if w in text],
    }


def post_chat(base: str, payload: dict, tries: int = 4) -> httpx.Response:
    """429/5xx 重试。智谱免费档限流很严（一轮对话引擎要调 5+ 次），退避得够长。"""
    delay = 15.0
    resp = None
    for i in range(tries):
        resp = httpx.post(base + "/v1/chat/completions", json=payload, timeout=300.0)
        if resp.status_code == 200:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504) and i < tries - 1:
            print(f"    {resp.status_code} 重试 {i + 1}/{tries - 1}（等 {delay:.0f}s）", flush=True)
            time.sleep(delay)
            delay *= 2
            continue
        return resp
    return resp


def run_model(model: str, persona: str, turns: list[str], repeat: int) -> list[dict]:
    tmp = Path(f"/tmp/exp-{model.replace(':', '-').replace('/', '-')}")
    tmp.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    proc, base = start_app(tmp, model)
    try:
        for r in range(repeat):
            messages = []
            for t in turns:
                messages.append({"role": "user", "content": t})
                resp = post_chat(base, {"model": persona, "messages": messages})
                if resp.status_code != 200:
                    results.append(
                        {"model": model, "round": r, "error": resp.text[:200]}
                    )
                    return results
                reply = resp.json()["choices"][0]["message"]["content"]
                messages.append({"role": "assistant", "content": reply})
                results.append(
                    {
                        "model": model,
                        "round": r,
                        "ask": t,
                        "reply": reply,
                        **score(reply),
                    }
                )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    return results


def summarize(rows: list[dict]) -> None:
    ok = [r for r in rows if "reply" in r]
    print(f"\n{'模型':<22}{'字数':>6}{'段数':>6}{'段均':>6}{'感叹号':>7}{'密度%':>7}{'反问':>6}")
    for r in ok:
        print(
            f"{r['model']:<22}{r['chars']:>6}{r['paras']:>6}{r['avg_para_chars']:>6}"
            f"{r['bangs']:>7}{r['bang_density']:>7}{r['questions']:>6}"
        )
    if ok:
        print(
            "\n感叹号密度均值 "
            + ", ".join(
                f"{m}="
                + str(
                    round(
                        statistics.mean(
                            [r["bang_density"] for r in ok if r["model"] == m]
                        ),
                        2,
                    )
                )
                for m in {r["model"] for r in ok}
            )
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--persona", default="luna")
    ap.add_argument("--ask", nargs="*", default=DEFAULT_TURNS)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", default="/tmp/exp-model-persona.json")
    args = ap.parse_args()

    all_rows: list[dict] = []
    for model in args.models:
        route = normalize_route(model)
        print(f"→ {route} ...", flush=True)
        try:
            all_rows.extend(run_model(route, args.persona, args.ask, args.repeat))
        except Exception as exc:  # noqa: BLE001
            print(f"  {model} 失败：{type(exc).__name__}: {exc}")

    for r in all_rows:
        if "reply" in r:
            print(f"\n===== {r['model']} #{r['round']} =====")
            print(f"问：{r['ask']}")
            print(f"答：{r['reply']}")
            print(
                f"指标：{r['chars']}字 / {r['paras']}段 / 感叹号{r['bangs']} "
                f"/ 密度{r['bang_density']}% / 反问{r['questions']}"
                + (f" / 索要{i}" if (i := r["praise"]) else "")
                + (f" / 表演{i}" if (i := r["acting"]) else "")
            )

    summarize(all_rows)
    Path(args.out).write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
