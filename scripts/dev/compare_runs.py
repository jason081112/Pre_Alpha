#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""对比多次 Distilly 产物的稳定性与幻觉候选（#29）。

用法:

    scripts/dev/compare_runs.py runs/gemma4-1 runs/gemma4-2 runs/gemma4-3 \
        --fixture tests/fixtures/chats/colleague_zh.canonical.json \
        --out docs/data/p2-29.json

输出 JSON（stdout 或 --out），含：

- files          每个 run 的产物齐全性
- structure      H2/H3 集合、两两 Jaccard、缺失/新增标题
- entries        各层条目数与波动（极差、变异系数）
- text_drift     同章节 3-gram Jaccard
- meta           关键元数据是否一致
- hallucination  无源实体 / 无源数字候选（启发式，需人工确认）
- verdict        按 #29 阈值给出 pass / warn / fail

判定阈值来自 issue #29：结构 Jaccard ≥ 0.8、条目数波动 ±30%、文件齐全与元数据一致须 100%。
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import statistics
import sys
from pathlib import Path

REQUIRED = {
    "SKILL.md", "work.md", "persona.md", "work_skill.md",
    "persona_skill.md", "manifest.json", "meta.json", "PROVENANCE.md",
}
LAYER_RE = re.compile(r"^## Layer (\d+)")
H2_RE = re.compile(r"^## (.+)$")
H3_RE = re.compile(r"^### (.+)$")

STRUCT_JACCARD_MIN = 0.8
ENTRY_VARIATION_MAX = 0.30

# 幻觉候选的启发式模式。刻意收窄：宁可漏报，也不要给出上百条噪声。
ENTITY_PATTERNS = [
    re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z]+\b"),               # 驼峰专名：ByteDance / RetryAfter
    re.compile(r"\b[A-Za-z]+(?:-[A-Za-z]+)+\b"),                # 连字符技术词：Retry-After
    re.compile(r"[\u4e00-\u9fa5]{2,10}(?:公司|集团|研究院|实验室)"),  # 机构（不收泛化的"系统/平台"）
]
NUMBER_PATTERNS = [
    re.compile(r"\d+\s*(?:年|岁)(?:以上)?(?:经验|工作|开发)"),
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月"),
    re.compile(r"\d+\s*(?:万|亿|千)\s*元"),
]
# 常见噪声词：不是专有的技术栈描述，避免误报
STOPWORDS = {
    "API", "HTTP", "HTTPS", "JSON", "SQL", "URL", "ID", "OK", "TODO",
    "Layer", "README", "SKILL", "Markdown", "Skill", "Persona", "Work",
    "Correction", "Log", "PR", "CI", "SDK", "CLI", "Well", "Okay",
    "Retry-After", "Last4", "Layer-0", "UTF-8",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_meta(run: Path) -> dict:
    meta_file = run / "meta.json"
    if not meta_file.is_file():
        return {}
    try:
        return json.loads(read(meta_file))
    except json.JSONDecodeError:
        return {}


def sections(text: str, pattern: re.Pattern) -> list[str]:
    out = []
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            out.append(match.group(1).strip().lower())
    return out


def split_h2(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    current = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                blocks[current] = "\n".join(lines)
            current = line[3:].strip().lower()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        blocks[current] = "\n".join(lines)
    return blocks


def trigrams(text: str) -> set[str]:
    cleaned = re.sub(r"\s+", "", text)
    return {cleaned[i:i + 3] for i in range(max(0, len(cleaned) - 2))}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return round(len(a & b) / len(union), 4) if union else 1.0


def count_entries(text: str) -> dict[str, int]:
    """按层统计 bullet 条数，附口头禅与边界。"""
    result: dict[str, int] = {}
    current_layer = None
    for line in text.splitlines():
        stripped = line.strip()
        match = LAYER_RE.match(stripped)
        if match:
            current_layer = f"Layer {match.group(1)}"
            result.setdefault(current_layer, 0)
            continue
        if stripped.startswith("## "):
            current_layer = None
            continue
        if current_layer and stripped.startswith(("- ", "* ")):
            result[current_layer] += 1
    blocks = split_h2(text)
    for key, needle in (("catchphrases", "signature phrases"), ("catchphrases", "catchphrases")):
        if needle in blocks:
            result[key] = sum(1 for line in blocks[needle].splitlines() if line.strip().startswith("- "))
    boundary = blocks.get("boundaries and sore spots", "")
    if boundary:
        result["boundaries"] = sum(1 for line in boundary.splitlines() if line.strip().startswith("- "))
    return result


def size_stats(values: list[int]) -> dict:
    if not values:
        return {"values": [], "range": 0, "cv": 0.0}
    mean = statistics.mean(values)
    cv = (statistics.pstdev(values) / mean) if mean else 0.0
    return {"values": values, "range": max(values) - min(values), "cv": round(cv, 4)}


def hallucination_candidates(run: Path, fixture_text: str) -> list[dict]:
    found: dict[tuple[str, str], dict] = {}
    for path in sorted(run.glob("*.md")):
        text = read(path)
        current = None
        for line in text.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
            for pattern in ENTITY_PATTERNS + NUMBER_PATTERNS:
                for match in pattern.finditer(line):
                    token = match.group(0).strip()
                    if token in STOPWORDS or len(token) < 2:
                        continue
                    # 产物自身的命名（slug / 文件 stem）不算幻觉
                    if any(marker in token.lower() for marker in
                           ("demo-", "qa-", "-persona", "-work", "skill.md", "persona.md")):
                        continue
                    if token.lower() in fixture_text.lower():
                        continue
                    key = (path.name, token)
                    found.setdefault(
                        key,
                        {"file": path.name, "section": current, "text": token,
                         "context": line.strip()[:80],
                         "kind": "number" if pattern in NUMBER_PATTERNS else "entity"},
                    )
    return sorted(found.values(), key=lambda item: (item["file"], item["text"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--label", action="append", default=[])
    args = parser.parse_args()

    if len(args.runs) < 2:
        print("ERROR: 至少两个 run 才能比较", file=sys.stderr)
        return 2

    runs = args.runs
    labels = args.label or [r.name for r in runs]
    fixture_text = read(args.fixture) if args.fixture and args.fixture.is_file() else ""

    report: dict = {
        "issues": [29],
        "runs": labels,
        "fixture": str(args.fixture) if args.fixture else None,
        "files": {},
        "structure": {},
        "entries": {},
        "text_drift": {},
        "meta": {},
        "hallucination": {},
        "verdict": {},
    }

    # 文件齐全性
    for label, run in zip(labels, runs):
        present = {p.name for p in run.iterdir() if p.is_file()} if run.is_dir() else set()
        report["files"][label] = {
            "complete": REQUIRED <= present,
            "missing": sorted(REQUIRED - present),
            "extra": sorted(present - REQUIRED),
        }

    # 结构：persona.md 与 SKILL.md 的标题集合
    persona_sets = {}
    for label, run in zip(labels, runs):
        persona_file = run / "persona.md"
        if not persona_file.is_file():
            continue
        text = read(persona_file)
        h2 = sections(text, H2_RE)
        persona_sets[label] = h2
    pairwise = {}
    for a, b in itertools.combinations(persona_sets, 2):
        pairwise[f"{a}|{b}"] = {
            "h2_jaccard": jaccard(set(persona_sets[a]), set(persona_sets[b])),
            "missing_in_b": sorted(set(persona_sets[a]) - set(persona_sets[b])),
            "extra_in_b": sorted(set(persona_sets[b]) - set(persona_sets[a])),
        }
    report["structure"] = {
        "h2_sets": {k: v for k, v in persona_sets.items()},
        "pairwise": pairwise,
        "min_h2_jaccard": min((v["h2_jaccard"] for v in pairwise.values()), default=1.0),
    }

    # 条目数
    per_run_entries = {}
    for label, run in zip(labels, runs):
        persona_file = run / "persona.md"
        if persona_file.is_file():
            per_run_entries[label] = count_entries(read(persona_file))
    keys = sorted({k for entries in per_run_entries.values() for k in entries})
    report["entries"] = {
        "per_run": per_run_entries,
        "stats": {
            key: size_stats([entries.get(key, 0) for entries in per_run_entries.values()])
            for key in keys
        },
    }

    # 文本漂移：同章节 3-gram Jaccard
    drift = {}
    for a, b in itertools.combinations(labels, 2):
        pa, pb = runs[labels.index(a)] / "persona.md", runs[labels.index(b)] / "persona.md"
        if not (pa.is_file() and pb.is_file()):
            continue
        ba, bb = split_h2(read(pa)), split_h2(read(pb))
        shared = set(ba) & set(bb)
        drift[f"{a}|{b}"] = {
            section: jaccard(trigrams(ba[section]), trigrams(bb[section])) for section in sorted(shared)
        }
    report["text_drift"] = drift

    # 元数据一致性
    metas = {label: load_meta(run) for label, run in zip(labels, runs)}
    keys_to_check = ["preset", "character", "schema_version"]
    consistent = {}
    for key in keys_to_check:
        values = {label: meta.get(key) for label, meta in metas.items()}
        consistent[key] = {"values": values, "consistent": len({str(v) for v in values.values()}) == 1}
    report["meta"] = consistent

    # 幻觉候选：跨 run 聚合，只在少数 run 出现的排前面（更可疑）
    if fixture_text:
        per_run = {label: hallucination_candidates(run, fixture_text)
                   for label, run in zip(labels, runs)}
        counter: dict[str, int] = {}
        for items in per_run.values():
            for item in items:
                counter[item["text"]] = counter.get(item["text"], 0) + 1
        aggregated = []
        for label, items in per_run.items():
            for item in items:
                item["runs_present"] = counter[item["text"]]
                aggregated.append(item)
        # 去重（同 token 同文件保留一条），按 出现次数升序 → 长度降序
        seen = set()
        deduped = []
        for item in sorted(aggregated, key=lambda x: (x["runs_present"], -len(x["text"]))):
            key = (item["file"], item["text"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        report["hallucination"] = {
            "per_run_counts": {label: len(items) for label, items in per_run.items()},
            "total_unique": len(deduped),
            "singletons": [item for item in deduped if item["runs_present"] == 1][:25],
            "recurrent": [item for item in deduped if item["runs_present"] > 1][:25],
            "note": "启发式候选，需人工确认；singletons 指仅在 1 个 run 出现，更可能是幻觉",
        }

    # 判定
    reasons = []
    if not all(v["complete"] for v in report["files"].values()):
        reasons.append("文件不齐全")
    if report["structure"]["min_h2_jaccard"] < STRUCT_JACCARD_MIN:
        reasons.append(f"结构 Jaccard {report['structure']['min_h2_jaccard']} < {STRUCT_JACCARD_MIN}")
    over = [k for k, v in report["entries"]["stats"].items() if v["cv"] > ENTRY_VARIATION_MAX and v["range"] > 1]
    if over:
        reasons.append(f"条目波动超限：{', '.join(over)}")
    if not all(v["consistent"] for v in report["meta"].values()):
        reasons.append("元数据不一致")
    report["verdict"] = {
        "status": "pass" if not reasons else "warn",
        "reasons": reasons,
        "thresholds": {
            "structure_jaccard_min": STRUCT_JACCARD_MIN,
            "entry_variation_max": ENTRY_VARIATION_MAX,
        },
        "hallucination_unique_candidates": report["hallucination"].get("total_unique", 0),
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"written: {args.out}")
        print(f"verdict: {report['verdict']['status']} {report['verdict']['reasons']}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
