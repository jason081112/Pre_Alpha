#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""pipeline 驱动的 Distilly 蒸馏 runner（#29 口径 A）。

Distilly 本身是给 agent 用的 skill 包（`bin/distilly.mjs` 只是安装器），没有自动
pipeline。本脚本把 `prompts/flows/create.md` 的步骤程序化：

    intake → work_analyzer / persona_analyzer → work_builder / persona_builder
    → skill_writer 组装

每次运行产出一套完整 Skill（7 产物 + PROVENANCE），用于重复性实验。

用法:

    run_distilly_pipeline.py --fixture tests/fixtures/chats/colleague_zh.canonical.json \
        --distilly vendor/distilly --model gemma4:31b --out runs/gemma4-1

注意：gpt-oss:120b 一类 reasoning 模型会先输出 reasoning，需给足 max_tokens。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

FAMILY_PROMPTS = {
    "intake": "prompts/{character}/intake.md",
    "persona_analyzer": "prompts/{character}/persona_analyzer.md",
    "persona_builder": "prompts/{character}/persona_builder.md",
}
SHARED_PROMPTS = {
    "work_analyzer": "prompts/shared/work_analyzer.md",
    "work_builder": "prompts/shared/work_builder.md",
}


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def chat(base_url: str, api_key: str, model: str, system: str, user: str,
         max_tokens: int, temperature: float, timeout: int = 300) -> tuple[str, dict]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    message = body["choices"][0]["message"]
    content = (message.get("content") or "").strip()
    usage = body.get("usage", {})
    return content, usage


def step(name: str, prompt_path: Path, material: str, args, stats: list[dict]) -> str:
    system = prompt_path.read_text(encoding="utf-8")
    started = time.time()
    for attempt in range(2):
        try:
            content, usage = chat(
                args.base_url, os.environ[args.api_key_env], args.model,
                system, material, args.max_tokens, args.temperature, args.timeout,
            )
            if content:
                break
            log(f"  {name}: 空回复（attempt {attempt + 1}）")
        except (urllib.error.URLError, TimeoutError, KeyError) as exc:
            log(f"  {name}: 失败 {str(exc)[:80]}（attempt {attempt + 1}）")
            content = ""
        time.sleep(2)
    elapsed = round(time.time() - started, 1)
    stats.append({
        "step": name,
        "prompt": str(prompt_path.relative_to(args.distilly)),
        "seconds": elapsed,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "chars": len(content),
    })
    log(f"  {name}: {len(content)} 字符 / {elapsed}s")
    if not content:
        raise SystemExit(f"ERROR: step {name} 无输出")
    return content


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--distilly", default="vendor/distilly", type=Path)
    parser.add_argument("--character", default="colleague")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", default="林舟")
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default="https://ollama.com/v1")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    if args.api_key_env not in os.environ:
        print(f"ERROR: 缺少环境变量 {args.api_key_env}", file=sys.stderr)
        return 2

    distilly: Path = args.distilly
    args.out.mkdir(parents=True, exist_ok=True)
    work_dir = args.out / "_work"
    work_dir.mkdir(exist_ok=True)

    fixture_text = args.fixture.read_text(encoding="utf-8")
    log(f"run: model={args.model} character={args.character} out={args.out}")
    log(f"fixture: {args.fixture} ({len(fixture_text)} 字符)")

    stats: list[dict] = []
    started = time.time()

    def family(name: str) -> Path:
        return distilly / FAMILY_PROMPTS[name].format(character=args.character)

    # Step 1 · intake
    intake = step("intake", family("intake"),
                  f"以下是待蒸馏的聊天记录（JSON 或文本）：\n\n{fixture_text}", args, stats)
    (work_dir / "01-intake.md").write_text(intake, encoding="utf-8")

    # Step 2 · 两条分析轨道
    material = f"## intake 结果\n\n{intake}\n\n## 原始材料\n\n{fixture_text}"
    work_analysis = step("work_analyzer", distilly / SHARED_PROMPTS["work_analyzer"], material, args, stats)
    (work_dir / "02-work-analysis.md").write_text(work_analysis, encoding="utf-8")
    persona_analysis = step("persona_analyzer", family("persona_analyzer"), material, args, stats)
    (work_dir / "03-persona-analysis.md").write_text(persona_analysis, encoding="utf-8")

    # Step 3 · 两条构建轨道
    work_md = step("work_builder", distilly / SHARED_PROMPTS["work_builder"],
                   f"## 分析结果\n\n{work_analysis}", args, stats)
    (work_dir / "04-work.md").write_text(work_md, encoding="utf-8")
    persona_md = step("persona_builder", family("persona_builder"),
                      f"## 分析结果\n\n{persona_analysis}", args, stats)
    (work_dir / "05-persona.md").write_text(persona_md, encoding="utf-8")

    # Step 4 · 元数据 + 组装
    meta = {
        "name": args.name,
        "display_name": args.name,
        "character": args.character,
        "slug": args.slug,
        "synthetic": True,
        "classification": {"language": "zh-CN", "tags": [], "gallery_category": "Colleague"},
        "knowledge_sources": [str(args.fixture)],
        "generation": {
            "engine": "distilly",
            "character": args.character,
            "created_from": [str(args.fixture)],
            "pipeline": "programmatic (run_distilly_pipeline.py)",
            "model": args.model,
            "temperature": args.temperature,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        },
        "source_context": {"domain": "work", "relationship_to_user": "colleague",
                           "is_real_person": False, "is_public_figure": False, "is_fictional": False},
    }
    meta_path = work_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    cmd = [
        sys.executable, str(distilly / "tools" / "skill_writer.py"),
        "--action", "create",
        "--character", args.character,
        "--slug", args.slug,
        "--name", args.name,
        "--meta", str(meta_path),
        "--work", str(work_dir / "04-work.md"),
        "--persona", str(work_dir / "05-persona.md"),
        "--base-dir", str(args.out / "skills" / args.character),
    ]
    written = subprocess.run(cmd, capture_output=True, text=True)
    log(f"  skill_writer exit={written.returncode}")
    if written.returncode != 0:
        log(written.stdout[-800:])
        log(written.stderr[-800:])
        return 1

    produced = args.out / "skills" / args.character / args.slug
    if produced.is_dir():
        for item in produced.iterdir():
            if item.is_file():
                (args.out / item.name).write_text(item.read_text(encoding="utf-8"), encoding="utf-8")

    run_meta = {
        "model": args.model,
        "character": args.character,
        "slug": args.slug,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "fixture": str(args.fixture),
        "duration_s": round(time.time() - started, 1),
        "steps": stats,
        "skill_writer_exit": written.returncode,
        "skill_writer_tail": written.stdout.strip().splitlines()[-3:],
    }
    (args.out / "run.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"done in {run_meta['duration_s']}s → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
