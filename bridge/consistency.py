# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""一致性校验（#41）：生成物 ↔ 引擎契约。

三项契约：

1. **文本契约** —— 只读 SOUL.md 就能判。4 个 H2 是否齐全、frontmatter 是否有
   `name`/`lang`、有没有 `Layer N` 或空壳标记、段落是否为空。**不需要 vendor**，
   所以 CI 里也能真跑（不是一律 skipped）。
2. **加载契约** —— 交给真 `PersonaLoader`，断言 4 段解析结果非空。
   `raw_content` 非空而三核心段为空 = 人格静默丢失，判 fail。
3. **注册契约** —— `PersonaStore` 往返（save → load），字段不丢。

重要事实（#41 必须写清，否则后人误解）：`PersonaLoader`（SOUL.md）与 `PersonaStore`
（profile.json）是**两套并行体系**，对话链路走 loader。store 成功不代表"能被对话使用"，
它只是动态生成路径的持久化。因此注册契约判 fail 不应阻断对话可用性，只提示。
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

SOUL_SECTIONS = {
    "zh": ("性格", "说话风格", "背景故事", "行为规则"),
    "en": ("Personality", "Speaking Style", "Background", "Behavioral Rules"),
}
SHELL_MARKERS = ("【内心独白】", "【最终回复】")
GENDER_VALUES = ("female", "male")
LANG_VALUES = ("zh", "en")


@dataclass
class Result:
    name: str
    status: str  # pass / warn / fail / skipped
    reasons: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "reasons": self.reasons,
            "detail": self.detail,
        }


def _worst(statuses: list[str]) -> str:
    for level in ("fail", "warn", "skipped", "pass"):
        if level in statuses:
            return level
    return "pass"


# ---------------------------------------------------------------- 1. 文本契约


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """极简 YAML frontmatter 读取（只支持 key: value 与缩进列表，够用且无依赖）。"""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text

    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.startswith((" ", "\t", "-")):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields, "\n".join(lines[end + 1:])


def check_text_contract(soul_text: str, *, persona_id: str = "") -> Result:
    reasons: list[str] = []
    fields, body = split_frontmatter(soul_text)

    if not fields:
        return Result("text", "fail", ["缺少 frontmatter"], {"persona_id": persona_id})

    for key in ("name", "lang"):
        if not fields.get(key):
            reasons.append(f"frontmatter 缺 `{key}`（loader 会用默认值，属静默偏差）")

    lang = fields.get("lang", "zh")
    if lang not in LANG_VALUES:
        reasons.append(f"lang={lang!r} 不在 {LANG_VALUES}，loader 只会按 zh 处理")

    gender = fields.get("gender")
    if gender and gender not in GENDER_VALUES:
        reasons.append(f"gender={gender!r} 不在 {GENDER_VALUES}")

    age = fields.get("age")
    if age and not age.isdigit():
        reasons.append(f"age={age!r} 不是整数")

    if re.search(r"\bLayer\s*\d", soul_text):
        reasons.append("正文含 `Layer N` 字样（loader 不识别该标题，会落 raw_content）")

    for marker in SHELL_MARKERS:
        if marker in soul_text:
            reasons.append(f"正文含空壳标记 `{marker}`（few-shot 拼装残留）")

    sections = SOUL_SECTIONS.get(lang, SOUL_SECTIONS["zh"])
    present = {}
    for section in sections:
        match = re.search(rf"^##\s*{re.escape(section)}\s*$(.*?)(?=^##\s|\Z)",
                          body, flags=re.M | re.S)
        content = match.group(1).strip() if match else ""
        present[section] = bool(content)

    core = sections[:3]  # 性格 / 说话风格 / 背景故事（随 lang 切换字面量）
    missing = [s for s in core if not present.get(s)]
    if len(missing) == 3:
        reasons.append("三个核心段全空 → loader 会退到 raw_content，人格不进 prompt")
    elif missing:
        for section in missing:
            reasons.append(f"段落 `{section}` 为空")

    if not present.get(sections[3]):
        reasons.append(f"段落 `{sections[3]}` 为空（冲突反应会退回通用语气，不阻断加载）")

    if not any(present.values()):
        reasons.append("正文没有任何可识别段落")

    status = "fail" if any("核心段全空" in r for r in reasons) else ("warn" if reasons else "pass")
    return Result("text", status, reasons, {"persona_id": persona_id, "sections": present,
                                            "frontmatter": fields})


# ---------------------------------------------------------------- 2. 加载契约


def _vendor_root() -> Path | None:
    for candidate in (ROOT / "vendor" / "OpenHer", Path("/sandbox/workspace/repos/Pre_Alpha/vendor/OpenHer")):
        if (candidate / "persona" / "loader.py").is_file():
            return candidate
    return None


def _import_persona_modules():
    vendor = _vendor_root()
    if vendor is None:
        raise FileNotFoundError("vendor/OpenHer 不存在")
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    from persona.loader import PersonaLoader  # type: ignore
    from persona.store import PersonaProfile, PersonaStore  # type: ignore

    return PersonaLoader, PersonaStore, PersonaProfile


def check_load_contract(personas_dir: Path, persona_id: str) -> Result:
    try:
        PersonaLoader, _, _ = _import_persona_modules()
    except (FileNotFoundError, ImportError, ModuleNotFoundError) as exc:
        return Result("load", "skipped", [f"无法导入 PersonaLoader：{exc}"])

    try:
        loader = PersonaLoader(str(personas_dir))
        loaded = loader.get(persona_id)
    except Exception as exc:  # noqa: BLE001 - 引擎异常不应中断校验
        return Result("load", "fail", [f"PersonaLoader 抛异常：{str(exc)[:120]}"])

    if loaded is None:
        return Result("load", "fail", ["PersonaLoader 未找到该人格（目录名或 SOUL.md 缺失）"])

    reasons: list[str] = []
    sections = {
        "personality": bool(getattr(loaded, "personality", "")),
        "speaking_style": bool(getattr(loaded, "speaking_style", "")),
        "background": bool(getattr(loaded, "background", "")),
        "behavioral_rules": bool(getattr(loaded, "behavioral_rules", "")),
    }
    raw_content = bool(getattr(loaded, "raw_content", ""))

    if not sections["personality"] and not sections["speaking_style"] and not sections["background"]:
        reasons.append("三核心段全空" + ("（落到了 raw_content）" if raw_content else ""))

    if not sections["behavioral_rules"]:
        reasons.append("行为规则段为空（loader 不注入，冲突反应会退回通用语气）")

    status = "fail" if any("三核心段全空" in r for r in reasons) else ("warn" if reasons else "pass")
    return Result("load", status, reasons, {
        "persona_id": persona_id,
        "sections": sections,
        "raw_content_present": raw_content,
        "name": getattr(loaded, "name", ""),
        "lang": getattr(loaded, "lang", ""),
        "gender": getattr(loaded, "gender", ""),
    })


# ---------------------------------------------------------------- 3. 注册契约


def to_store_profile(profile_data: dict) -> dict:
    """PersonProfile(dict) → vendor PersonaProfile 字段映射。

    `personality` / `speaking_style` / `backstory` 从 SOUL 正文同源字段取，
    保持两套体系内容一致（不是各写一份）。
    """
    identity = profile_data.get("identity") or {}

    def val(node: Any) -> Any:
        if isinstance(node, dict):
            return node.get("value")
        return node

    tags = (profile_data.get("meta") or {}).get("tags") or {}
    if isinstance(tags, dict):
        flat = tags.get("zh") or tags.get("en") or []
    else:
        flat = tags

    return {
        "id": profile_data.get("persona_id", ""),
        "name": val(identity.get("name")) or "",
        "age": int(val(identity.get("age")) or 0),
        "gender": val(identity.get("gender")) or "female",
        "tags": list(flat),
    }


def check_register_contract(profile_data: dict, *, soul_text: str) -> Result:
    try:
        _, PersonaStore, PersonaProfile = _import_persona_modules()
    except (FileNotFoundError, ImportError, ModuleNotFoundError) as exc:
        return Result("register", "skipped", [f"无法导入 PersonaStore：{exc}"])

    try:
        from cli.profile2soul_cmd import TITLES, body_from_profile
        from bridge.model import profile_from_dict

        profile = profile_from_dict(profile_data)
        body, _ = body_from_profile(profile)
        titles = TITLES["zh" if str(profile.identity.lang).startswith("zh") else "en"]
    except Exception as exc:  # noqa: BLE001
        return Result("register", "fail", [f"无法还原 PersonProfile：{str(exc)[:120]}"])

    mapped = to_store_profile(profile_data)
    store_profile = PersonaProfile(
        id=mapped["id"],
        gender=mapped["gender"],
        name=mapped["name"],
        age=mapped["age"],
        personality=body.get("personality", ""),
        speaking_style=body.get("speaking_style", ""),
        tags=mapped["tags"],
        backstory=body.get("background", ""),
        locked=True,
    )

    reasons: list[str] = []
    if not store_profile.personality:
        reasons.append("personality 为空（store 侧人格描述缺失）")
    if not store_profile.speaking_style:
        reasons.append("speaking_style 为空")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersonaStore(tmp)
            path = store.save(store_profile)
            if not Path(path).is_file():
                return Result("register", "fail", ["save 未落盘"])
            back = store.load(mapped["id"])
    except Exception as exc:  # noqa: BLE001
        return Result("register", "fail", [f"PersonaStore 往返异常：{str(exc)[:120]}"])

    if back is None:
        return Result("register", "fail", ["save 后 load 返回 None"])

    drift = []
    for key in ("id", "name", "gender", "age"):
        if getattr(back, key) != getattr(store_profile, key):
            drift.append(f"{key}: {getattr(store_profile, key)!r} → {getattr(back, key)!r}")
    if store_profile.tags and list(back.tags) != store_profile.tags:
        drift.append(f"tags: {store_profile.tags!r} → {list(back.tags)!r}")

    if drift:
        reasons.append("往返字段漂移：" + "；".join(drift))

    status = "fail" if any("失败" in r or "异常" in r for r in reasons) else ("warn" if reasons else "pass")
    return Result("register", status, reasons, {
        "persona_id": mapped["id"],
        "store_file": "profile.json",
        "roundtrip": True,
        "titles_used": list(titles.values()),
    })


# ---------------------------------------------------------------- 汇总


def verify_persona(personas_dir: Path, persona_id: str) -> dict[str, Any]:
    soul_path = Path(personas_dir) / persona_id / "SOUL.md"
    if not soul_path.is_file():
        return {
            "persona_id": persona_id,
            "status": "fail",
            "results": [Result("text", "fail", [f"缺文件 {soul_path}"]).to_dict()],
        }

    soul_text = soul_path.read_text(encoding="utf-8")
    text_result = check_text_contract(soul_text, persona_id=persona_id)
    load_result = check_load_contract(Path(personas_dir), persona_id)

    # 注册契约需要 PersonProfile；目录里没有 profile.json 时跳过而非失败
    profile_path = Path(personas_dir) / persona_id / "profile.json"
    if profile_path.is_file():
        profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
        register_result = check_register_contract(profile_data, soul_text=soul_text)
    else:
        register_result = Result("register", "skipped",
                                 ["目录内无 profile.json（store 路径未使用）"])

    results = [text_result, load_result, register_result]
    status = _worst([r.status for r in results])
    return {
        "persona_id": persona_id,
        "status": status,
        "results": [r.to_dict() for r in results],
    }


def verify_dir(root: Path, persona_ids: list[str] | None = None) -> dict[str, Any]:
    root = Path(root)
    if persona_ids is None:
        persona_ids = sorted(
            p.name for p in root.iterdir()
            if p.is_dir() and (p / "SOUL.md").is_file()
        ) if root.is_dir() else []

    reports = [verify_persona(root, pid) for pid in persona_ids]
    statuses = [r["status"] for r in reports]
    return {
        "root": str(root),
        "count": len(reports),
        "status": _worst(statuses) if statuses else "fail",
        "personas": reports,
    }


__all__ = [
    "Result", "SOUL_SECTIONS", "split_frontmatter", "check_text_contract",
    "check_load_contract", "check_register_contract", "to_store_profile",
    "verify_persona", "verify_dir",
]
