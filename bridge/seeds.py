# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""口头禅 / 例句 → OpenHer `seeds.bin`（看板 #35）。

## seeds.bin 的真实格式（已核实，commit ef5b214）

    gzip(JSON)，结构为 {persona_id: [seed, ...]}，每条 seed：

    {"user_input": "...", "monologue": "...", "reply": "...",
     "vector": [8 个 0..1 的小数], "mass": 1.0 或 2.0, "lang": "zh"}

`vector` 的 8 维顺序由 `engine/genome/style_memory.py: CONTEXT_KEYS` 写死：

    conflict_level, user_emotion, user_engagement, user_vulnerability,
    topic_intimacy, conversation_depth, novelty_level, time_of_day

`user_emotion` 可为负（-1..1），其余 0..1。检索时按 L2 距离 + 引力质量取 top_k，
`build_few_shot_prompt()` 会把 `【内心独白】{monologue}` 与 `【最终回复】{reply}`
一起塞进 prompt —— 所以 **monologue 不能为空**，否则会留下一句空的内心独白壳。

## 素材来源

`monologue` 在 Distilly 产物里没有对应字段（`#28 §4` 已记录），因此本模块只接受
**人工撰写**的 seed 素材（四行文本格式），不生成、不猜测。素材入库到
`tests/fixtures/seeds/<persona>.txt`，便于追溯与复核。
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.model import PersonProfile, Provenance

# CONTEXT_KEYS 顺序（与 OpenHer 对齐，不可改动）
CONTEXT_KEYS = (
    "conflict_level", "user_emotion", "user_engagement", "user_vulnerability",
    "topic_intimacy", "conversation_depth", "novelty_level", "time_of_day",
)

# 情境标签 → 8 维坐标（人工设计，依据 #28 的 Layer 3/4 语义）
SITUATION_VECTORS: dict[str, list[float]] = {
    "日常闲聊": [0.0, 0.2, 0.5, 0.1, 0.4, 0.2, 0.2, 0.5],
    "被忽略": [0.5, -0.5, 0.4, 0.2, 0.5, 0.4, 0.1, 0.5],
    "对方道歉": [0.3, -0.2, 0.6, 0.4, 0.6, 0.5, 0.1, 0.5],
    "表达关心": [0.0, 0.4, 0.7, 0.3, 0.8, 0.5, 0.1, 0.5],
    "亲密靠近": [0.0, 0.7, 0.8, 0.5, 0.9, 0.6, 0.2, 0.5],
    "冲突升级": [0.8, -0.7, 0.3, 0.3, 0.5, 0.5, 0.1, 0.5],
    "深夜低落": [0.2, -0.3, 0.4, 0.6, 0.6, 0.6, 0.1, 0.9],
    # 职场向（colleague）：同一套 8 维坐标，只是情境含义不同（#87）
    "进度压力": [0.4, -0.3, 0.6, 0.2, 0.3, 0.7, 0.2, 0.4],
    "被甩锅": [0.8, -0.6, 0.7, 0.1, 0.3, 0.6, 0.1, 0.5],
    "被质疑专业": [0.7, -0.4, 0.8, 0.2, 0.4, 0.8, 0.2, 0.5],
    "被请托": [0.2, 0.0, 0.7, 0.3, 0.4, 0.6, 0.3, 0.6],
    "对方认错": [0.2, -0.1, 0.6, 0.5, 0.4, 0.7, 0.1, 0.5],
    "方案含糊": [0.5, -0.3, 0.6, 0.1, 0.3, 0.7, 0.2, 0.4],
    "日常协作": [0.0, 0.2, 0.5, 0.1, 0.3, 0.3, 0.2, 0.4],
}

# 情境按领域分组：同一套坐标，不同角色的必备覆盖集不同（#87）
SITUATION_GROUPS: dict[str, tuple[str, ...]] = {
    "relationship": ("日常闲聊", "被忽略", "对方道歉", "表达关心", "亲密靠近", "冲突升级", "深夜低落"),
    "work": ("进度压力", "被甩锅", "被质疑专业", "被请托", "对方认错", "方案含糊", "日常协作"),
}

# 需要更高召回权重的场景（冲突/被冷落是人格的核心反应区）
HIGH_MASS_SITUATIONS = {"被忽略", "冲突升级", "被甩锅", "被质疑专业"}

MONOLOGUE_MIN, MONOLOGUE_MAX = 15, 80
REPLY_MAX = 40
SCENE_MAX = 120  # 长倾诉场景（对方发一大段话）本身就是长句，60 字会误杀

FORBIDDEN_PATTERNS = {
    "action_paren": re.compile(r"[（(][^）)]{1,20}[）)]"),
    "action_star": re.compile(r"[*＊][^*＊]{1,20}[*＊]"),
    "bracket_marker": re.compile(r"【[^】]{1,20}】"),
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}


@dataclass
class SeedDraft:
    scene: str
    monologue: str
    reply: str
    situation: str


def parse_seed_text(text: str) -> tuple[list[SeedDraft], list[str]]:
    """解析四行格式（场景 / 心里 / 回复 / 情境），用 `---` 分隔。"""
    drafts: list[SeedDraft] = []
    errors: list[str] = []
    blocks = [block.strip() for block in re.split(r"^-{3,}\s*$", text, flags=re.M) if block.strip()]
    for index, block in enumerate(blocks, start=1):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if not line.strip():
                continue
            if ":" not in line and "：" not in line:
                errors.append(f"第 {index} 条：无法解析的行 {line[:20]!r}")
                continue
            key, value = re.split(r"[:：]", line, maxsplit=1)
            fields[key.strip()] = value.strip()
        missing = [k for k in ("场景", "心里", "回复", "情境") if not fields.get(k)]
        if missing:
            errors.append(f"第 {index} 条：缺少 {', '.join(missing)}")
            continue
        drafts.append(
            SeedDraft(
                scene=fields["场景"],
                monologue=fields["心里"],
                reply=fields["回复"],
                situation=fields["情境"],
            )
        )
    return drafts, errors


def _infer_domain(drafts: list[SeedDraft]) -> str:
    """按情境标签判断素材属于哪个领域（关系向 / 职场向）。"""
    counts = {name: sum(1 for d in drafts if d.situation in group)
              for name, group in SITUATION_GROUPS.items()}
    return max(counts, key=lambda k: counts[k]) if drafts else "relationship"


def validate_seeds(drafts: list[SeedDraft], *, domain: str | None = None,
                   min_coverage: int = 5) -> list[str]:
    """逐条校验；返回错误列表（中文，便于直接回给用户）。

    `domain` 不给时按情境标签自动推断；覆盖要求是「该领域至少 `min_coverage` 类」，
    不再硬编码「必须含被忽略/冲突升级」—— 那只是关系向的核心区，职场向不适用（#87）。
    """
    errors: list[str] = []
    for index, draft in enumerate(drafts, start=1):
        prefix = f"第 {index} 条"
        if draft.situation not in SITUATION_VECTORS:
            errors.append(f"{prefix}：情境 {draft.situation!r} 不在预设表内")
        if len(draft.scene) > SCENE_MAX:
            errors.append(f"{prefix}：场景 {len(draft.scene)} 字，超过 {SCENE_MAX}")
        if not (MONOLOGUE_MIN <= len(draft.monologue) <= MONOLOGUE_MAX):
            errors.append(
                f"{prefix}：心里 {len(draft.monologue)} 字，应为 {MONOLOGUE_MIN}–{MONOLOGUE_MAX}"
            )
        if len(draft.reply) > REPLY_MAX:
            errors.append(f"{prefix}：回复 {len(draft.reply)} 字，超过 {REPLY_MAX}")
        if draft.reply.strip() == draft.monologue.strip():
            errors.append(f"{prefix}：心里与回复字面相同，缺少落差")
        for label, pattern in FORBIDDEN_PATTERNS.items():
            hit = pattern.search(draft.scene + draft.monologue + draft.reply)
            if hit:
                errors.append(f"{prefix}：含禁止内容 {label}（{hit.group(0)[:12]}）")
    situations = {d.situation for d in drafts}
    resolved = domain or _infer_domain(drafts)
    group = SITUATION_GROUPS.get(resolved, ())
    covered = [s for s in group if s in situations]
    if len(covered) < min_coverage:
        missing = [s for s in group if s not in situations]
        errors.append(
            f"整体：{resolved} 领域只覆盖 {len(covered)}/{len(group)} 类"
            f"（至少要 {min_coverage} 类），缺：{'、'.join(missing)}"
        )
    return errors


def to_openher_seeds(drafts: list[SeedDraft], *, lang: str = "zh") -> list[dict[str, Any]]:
    """转成 OpenHer seed 结构。"""
    seeds = []
    for draft in drafts:
        seeds.append(
            {
                "user_input": draft.scene,
                "monologue": draft.monologue,
                "reply": draft.reply,
                "vector": list(SITUATION_VECTORS[draft.situation]),
                "mass": 2.0 if draft.situation in HIGH_MASS_SITUATIONS else 1.0,
                "lang": lang,
            }
        )
    return seeds


def read_seeds_bin(path: Path) -> dict[str, list[dict]]:
    if not path.is_file():
        return {}
    with open(path, "rb") as handle:
        return json.loads(gzip.decompress(handle.read()).decode("utf-8"))


def write_seeds_bin(path: Path, persona_id: str, seeds: list[dict], *, merge: bool = True) -> dict:
    """把某个人格的 seed 写入 seeds.bin（gzip JSON），默认保留其他人格。"""
    data = read_seeds_bin(path) if merge else {}
    previous = len(data.get(persona_id, []))
    data[persona_id] = seeds
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    path.write_bytes(gzip.compress(payload))
    return {"persona_id": persona_id, "added": len(seeds), "replaced": previous, "personas": len(data)}


def build_from_files(materials: list[Path], *, persona_id: str,
                    lang: str = "zh", domain: str | None = None
                    ) -> tuple[list[dict], list[str], list[SeedDraft]]:
    """合并多份素材文件后统一校验。

    一个人格的素材会拆成多份（人工原文 / 变体 / 多段示范），单份文件往往覆盖不全，
    所以覆盖率只对**合并后**的全集判定。
    """
    drafts: list[SeedDraft] = []
    parse_errors: list[str] = []
    for material in materials:
        parsed, errs = parse_seed_text(material.read_text(encoding="utf-8"))
        drafts.extend(parsed)
        parse_errors.extend(f"{material.name}: {e}" for e in errs)

    errors = parse_errors + validate_seeds(drafts, domain=domain)
    if errors:
        return [], errors, drafts
    return to_openher_seeds(drafts, lang=lang), [], drafts


def build_from_file(material: Path, *, persona_id: str, lang: str = "zh",
                    domain: str | None = None) -> tuple[list[dict], list[str], list[SeedDraft]]:
    """单文件入口，保留兼容。"""
    return build_from_files([material], persona_id=persona_id, lang=lang, domain=domain)


def persona_lang(profile: PersonProfile) -> str:
    lang = profile.identity.lang or "zh"
    return "zh" if lang.startswith("zh") else "en"


__all__ = [
    "CONTEXT_KEYS", "SITUATION_VECTORS", "HIGH_MASS_SITUATIONS",
    "SeedDraft", "parse_seed_text", "validate_seeds", "to_openher_seeds",
    "read_seeds_bin", "write_seeds_bin", "build_from_file", "build_from_files", "persona_lang",
]
