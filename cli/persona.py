# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""`alpha persona` 子命令（#85 A 部分）：创建/编辑/预览人格的统一入口。

设计原则（与 #85 的交互原则一致）：

- 默认 dry-run，落盘要 `--yes`；
- 缺材料显式标 `missing`，不填默认值；
- 校验失败逐条中文提示，不抛栈；
- 危险操作（删除）必须 `--yes`。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.model import (  # noqa: E402
    Asserted,
    Expression,
    Identity,
    PersonProfile,
    RelationContext,
    validate_profile,
)
from bridge.profile2soul import profile_to_soul  # noqa: E402
from bridge.soul2profile import profile_from_soul  # noqa: E402
from shell.persona_repo import (  # noqa: E402
    PersonaError,
    PersonaRepository,
)

BUILTIN_ROOT = ROOT / "vendor" / "OpenHer" / "persona" / "personas"

# `--set` 允许的路径：只开放可确定含义的字段，避免误改结构
SETTABLE = {
    "identity.name": ("identity", "name", "text"),
    "identity.name_zh": ("identity", "name_zh", "text"),
    "identity.age": ("identity", "age", "int"),
    "identity.gender": ("identity", "gender", "text"),
    "identity.mbti": ("identity", "mbti", "text"),
    "expression.rhythm": ("expression", "rhythm", "text"),
    "relation.label": ("relation", "label", "text"),
    "relation.status": ("relation", "status", "text"),
}


def _data_root() -> Path:
    return Path(os.getenv("ALPHA_PERSONA_ROOT", str(ROOT / ".data" / "users")))


def _mirror() -> Path | None:
    """只有显式设置 ALPHA_PERSONA_MIRROR 才镜像到引擎目录。

    默认不写 `vendor/OpenHer/persona/personas`：那是上游源码目录，
    CLI 误写会污染内置人格（曾把测试人格写进去）。
    """
    raw = os.getenv("ALPHA_PERSONA_MIRROR")
    if not raw:
        return None
    path = Path(raw)
    return path if path.parent.is_dir() else None


def _repo() -> PersonaRepository:
    return PersonaRepository(_data_root(), mirror_dir=_mirror())


def _asserted(value: Any, kind: str) -> dict:
    if kind == "int":
        parsed: Any = int(value)
    else:
        parsed = str(value)
    return {"kind": "asserted", "value": parsed, "provenance": "source"}


def _scratch_profile(persona_id: str, name: str) -> PersonProfile:
    return PersonProfile(
        persona_id=persona_id,
        identity=Identity(
            name=Asserted.source(name),
            age=Asserted.missing("用户尚未填写年龄"),
            gender=Asserted.missing("用户尚未填写性别"),
            mbti=Asserted.missing("用户尚未填写 MBTI"),
        ),
        relation=RelationContext(
            label=Asserted.missing("尚未填写：这个人和你是什么关系"),
            status=Asserted.missing("尚未填写"),
        ),
        expression=Expression(rhythm=Asserted.missing("尚未填写：他/她怎么说话")),
    )


def _builtin_profile(persona_id: str, name: str, builtin_id: str) -> PersonProfile:
    soul = BUILTIN_ROOT / builtin_id / "SOUL.md"
    if not soul.is_file():
        available = sorted(p.name for p in BUILTIN_ROOT.iterdir() if p.is_dir()) if BUILTIN_ROOT.is_dir() else []
        raise PersonaError(
            f"找不到内置人格 {builtin_id!r}；可用: {', '.join(available) or '（无）'}"
        )
    profile = profile_from_soul(soul.read_text(encoding="utf-8"),
                                persona_id=persona_id, source=f"builtin:{builtin_id}")
    if name:
        profile.identity.name = Asserted.source(name)
    return profile


def _print_report(report: dict, *, show_markdown: bool) -> None:
    front = report["frontmatter"]
    print(f"[alpha] 将写入 frontmatter: {json.dumps(front, ensure_ascii=False)}")
    for warning in report["warnings"]:
        print(f"[alpha] 警告: {warning}")
    if show_markdown:
        print("--- SOUL.md ---")
        print(report["markdown"].rstrip())


def _cmd_new(args: argparse.Namespace) -> int:
    spec = args.from_spec
    persona_id = args.persona_id
    if not persona_id:
        print("[alpha] 需要 --id <slug>（小写字母/数字/连字符）", file=sys.stderr)
        return 2
    try:
        if spec == "scratch":
            profile = _scratch_profile(persona_id, args.name or persona_id)
            source = "manual"
        elif spec.startswith("builtin:"):
            profile = _builtin_profile(persona_id, args.name or "", spec.split(":", 1)[1])
            source = spec
        elif spec.startswith("distill:"):
            print("[alpha] 从蒸馏创建依赖 #42（profile2soul 实现），当前未就绪", file=sys.stderr)
            return 2
        else:
            print(f"[alpha] 未知来源 {spec!r}；支持 scratch / builtin:<id> / distill:<path>", file=sys.stderr)
            return 2

        errors = validate_profile(profile)
        if errors:
            print("[alpha] 校验未通过：", file=sys.stderr)
            for line in errors:
                print(f"  - {line}", file=sys.stderr)
            return 2

        report = profile_to_soul(profile)
        _print_report(report, show_markdown=not args.yes)

        if not args.yes:
            print("[alpha] dry-run：未写入。加 --yes 落盘。")
            return 0

        created = _repo().create(args.user, profile.to_dict(), source=source)
        print(f"[alpha] 已创建 {created['persona_id']}（v{created['version']}，来源 {created['source']}）")
        return 0
    except PersonaError as exc:
        print(f"[alpha] {exc.message}", file=sys.stderr)
        return 2
    except (ValueError, TypeError) as exc:
        print(f"[alpha] {exc}", file=sys.stderr)
        return 2


def _cmd_list(args: argparse.Namespace) -> int:
    items = _repo().list(args.user, include_deleted=args.include_deleted)
    if not items:
        print("[alpha] 没有可编辑的人格。用 `alpha persona new --from scratch --id <slug>` 创建。")
        return 0
    print(f"{'ID':<20}{'名字':<12}{'版本':<6}{'来源':<18}{'更新时间'}")
    for item in items:
        print(
            f"{item['persona_id']:<20}{str(item.get('display_name') or '-'):<12}"
            f"{item.get('version', '-'):<6}{str(item.get('source') or '-'):<18}"
            f"{item.get('updated_at') or '-'}"
        )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        record = _repo().get(args.user, args.persona_id)
    except PersonaError as exc:
        print(f"[alpha] {exc.message}", file=sys.stderr)
        return 2
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    try:
        record = _repo().get(args.user, args.persona_id)
    except PersonaError as exc:
        print(f"[alpha] {exc.message}", file=sys.stderr)
        return 2
    from bridge.model import profile_from_dict

    profile = profile_from_dict(record["profile"])
    report = profile_to_soul(profile)
    existing = _data_root() / args.user / "personas" / args.persona_id / "SOUL.md"
    if existing.is_file():
        diff = difflib.unified_diff(
            existing.read_text(encoding="utf-8").splitlines(),
            report["markdown"].splitlines(),
            fromfile="current SOUL.md",
            tofile="rendered SOUL.md",
            lineterm="",
        )
        printed = list(diff)
        print("\n".join(printed) if printed else "[alpha] 无差异")
    else:
        print(report["markdown"].rstrip())
    for warning in report["warnings"]:
        print(f"[alpha] 警告: {warning}")
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    repo = _repo()
    try:
        record = repo.get(args.user, args.persona_id)
    except PersonaError as exc:
        print(f"[alpha] {exc.message}", file=sys.stderr)
        return 2

    version = int(record["state"]["version"])
    patch: dict[str, Any] = {}
    for raw in args.set_values:
        if "=" not in raw:
            print(f"[alpha] --set 需要 key=value，收到 {raw!r}", file=sys.stderr)
            return 2
        key, value = raw.split("=", 1)
        key = key.strip()
        if key not in SETTABLE:
            print(f"[alpha] 不支持修改 {key!r}；可用: {', '.join(sorted(SETTABLE))}", file=sys.stderr)
            return 2
        container, field, kind = SETTABLE[key]
        try:
            patch.setdefault(container, {})[field] = _asserted(value, kind)
        except ValueError:
            print(f"[alpha] {key} 需要整数，收到 {value!r}", file=sys.stderr)
            return 2

    if not patch:
        print("[alpha] 没有改动（用 --set key=value）", file=sys.stderr)
        return 2

    merged = record["profile"]
    for container, fields in patch.items():
        merged.setdefault(container, {}).update(fields)
    merged["persona_id"] = args.persona_id

    from bridge.model import profile_from_dict

    errors = validate_profile(profile_from_dict(merged))
    if errors:
        print("[alpha] 校验未通过：", file=sys.stderr)
        for line in errors:
            print(f"  - {line}", file=sys.stderr)
        return 2

    if not args.yes:
        print(f"[alpha] dry-run：将更新 {args.persona_id}（v{version} → v{version + 1}）。加 --yes 落盘。")
        for key, fields in patch.items():
            for field, asserted in fields.items():
                print(f"  {key}.{field} = {asserted['value']!r}（{asserted['provenance']}）")
        return 0

    try:
        updated = repo.patch(args.user, args.persona_id, patch, version)
    except PersonaError as exc:
        print(f"[alpha] {exc.message}", file=sys.stderr)
        return 2
    print(f"[alpha] 已更新 {updated['persona_id']} → v{updated['version']}")
    return 0


def _cmd_seeds(args: argparse.Namespace) -> int:
    from bridge.model import profile_from_dict
    from bridge.seeds import build_from_file, persona_lang, read_seeds_bin, write_seeds_bin

    material = Path(args.material)
    if not material.is_file():
        print(f"[alpha] 找不到素材文件 {material}", file=sys.stderr)
        return 2

    lang = "zh"
    try:
        record = _repo().get(args.user, args.persona_id)
        lang = persona_lang(profile_from_dict(record["profile"]))
    except PersonaError:
        print(f"[alpha] 提示：没有找到已保存的 {args.persona_id}，按 lang=zh 处理")

    seeds, errors, drafts = build_from_file(material, persona_id=args.persona_id, lang=lang)
    if errors:
        print("[alpha] 素材校验未通过：", file=sys.stderr)
        for line in errors:
            print(f"  - {line}", file=sys.stderr)
        return 2

    print(f"[alpha] {args.persona_id}: {len(seeds)} 条 seed（lang={lang}）")
    print(f"{'#':<3}{'情境':<10}{'mass':<6}{'心里':<6}{'回复':<6}摘要")
    for index, (draft, seed) in enumerate(zip(drafts, seeds), start=1):
        print(f"{index:<3}{draft.situation:<10}{seed['mass']:<6}"
              f"{len(draft.monologue):<6}{len(draft.reply):<6}"
              f"{draft.reply[:18]}")

    out = Path(args.out) if args.out else (ROOT / ".data" / "seeds.bin")
    if not args.yes:
        print(f"[alpha] dry-run：未写入。加 --yes 写入 {out}")
        return 0

    result = write_seeds_bin(out, args.persona_id, seeds)
    total = len(read_seeds_bin(out)[args.persona_id])
    print(f"[alpha] 已写入 {out}（该人格 {total} 条，共 {result['personas']} 个人格）")
    if "vendor" not in str(out):
        print("[alpha] 提示：这是独立文件；要让引擎读到，需合并到 vendor/OpenHer/persona/seeds.bin"
              "（或部署时复制）")
    return 0


_STATUS_MARK = {"pass": "✓", "warn": "!", "fail": "✗", "skipped": "-"}


def _load_profile_arg(target: str):
    """target 可以是 persona_id（从仓库读）或 profile.json 路径。"""
    from bridge.model import profile_from_dict

    path = Path(target)
    if path.suffix == ".json" or path.is_file():
        if not path.is_file():
            raise PersonaError(f"文件不存在: {path}")
        return profile_from_dict(json.loads(path.read_text(encoding="utf-8")))
    return profile_from_dict(_repo().get(args_user_for_repo(), target)["profile"])


def args_user_for_repo() -> str:
    return os.getenv("ALPHA_USER") or "local"


def _correction_store():
    from shell.corrections import CorrectionStore

    db = os.getenv("ALPHA_CORRECTIONS_DB") or str(ROOT / ".data" / "corrections.db")
    return CorrectionStore(db)


def _memory_store():
    from shell.memory import MemoryStore

    db = os.getenv("ALPHA_MEMORY_DB") or str(ROOT / ".data" / "memory.db")
    return MemoryStore(db)


def _cmd_memory(args: argparse.Namespace) -> int:
    from shell.memory import MemoryError

    store = _memory_store()
    persona_id = args.persona_id
    action = args.action

    if action == "add":
        if not args.content:
            print("[alpha] add 需要 --content", file=sys.stderr)
            return 2
        try:
            record = store.add(
                persona_id, args.content, role=args.role, category=args.category,
                source="runtime", importance=args.importance, user_id=args.user,
            )
        except MemoryError as exc:
            print(f"[alpha] 条目非法：{exc}", file=sys.stderr)
            return 2
        print(f"[alpha] #{record.id} 已写入 [{record.category}/{record.role}] {record.content}")
        return 0

    if action == "search":
        if not args.query:
            print("[alpha] search 需要 --query", file=sys.stderr)
            return 2
        hits = store.search(persona_id, args.query, limit=args.limit, user_id=args.user)
        print(f"[alpha] 「{args.query}」命中 {len(hits)} 条")
        for record in hits:
            print(f"  #{record.id} [{record.category}] imp={record.importance:.2f} {record.content}")
        return 0

    if action == "list":
        rows = store.get_recent(persona_id, limit=args.limit, user_id=args.user)
        stats = store.stats(persona_id, user_id=args.user)
        print(f"[alpha] {persona_id}: 共 {stats['total']} 条"
              f"（fact {stats['fact']} / user {stats['user']} / assistant {stats['assistant']}）")
        for record in rows:
            print(f"  #{record.id} [{record.category}/{record.role}] "
                  f"imp={record.importance:.2f} {record.content}")
        return 0

    if action == "seed":
        from bridge.model import profile_from_dict

        target = Path(args.file).expanduser()
        if not target.is_file():
            print(f"[alpha] 找不到 {target}", file=sys.stderr)
            return 2
        profile = profile_from_dict(json.loads(target.read_text(encoding="utf-8")))
        data = json.loads(target.read_text(encoding="utf-8"))
        added = store.seed_from_profile(data, user_id=args.user)
        print(f"[alpha] 从 {target.name} 导入 {added} 条事实（重复内容自动吸收）")
        return 0

    if action == "context":
        text = store.build_context(persona_id, query=args.query or "", user_id=args.user)
        print(text if text else "（无记忆，不产生注入段）")
        return 0

    if action == "remove":
        if not args.yes:
            print(f"[alpha] 将删除 #{args.id}（不可恢复）。加 --yes 确认。")
            return 0
        ok = store.delete(int(args.id))
        print("[alpha] 已删除" if ok else "[alpha] 条目不存在")
        return 0 if ok else 2

    if action == "export":
        rows = store.export_for_engine(persona_id, user_id=args.user)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"[alpha] 未知操作 {action}", file=sys.stderr)
    return 2


def _cmd_corrections(args: argparse.Namespace) -> int:
    from shell.corrections import CorrectionError

    store = _correction_store()
    persona_id = args.persona_id
    action = args.action

    if action == "list":
        records = store.list(persona_id, user_id=args.user, include_inactive=args.all)
        stats = store.stats(persona_id, user_id=args.user)
        print(f"[alpha] {persona_id}: {stats['active']} 条生效 / 共 {stats['total']} 条"
              f"（运行期 {stats['runtime']}）")
        if not records:
            print("  （空）")
        for item in records:
            mark = "✓" if item.active else "·"
            scene = f"[{item.scene}] " if item.scene else ""
            print(f"  {mark} #{item.id} {scene}不要说「{item.wrong}」→「{item.correct}」"
                  f"  ({item.source})")
        return 0

    if action in ("disable", "enable"):
        try:
            record = store.set_active(int(args.id), action == "enable")
        except CorrectionError as exc:
            print(f"[alpha] {exc}", file=sys.stderr)
            return 2
        print(f"[alpha] #{record.id} 已{'启用' if record.active else '停用'}")
        return 0

    if action == "remove":
        if not args.yes:
            print(f"[alpha] 将删除条目 #{args.id}（不可恢复）。加 --yes 确认。")
            return 0
        ok = store.delete(int(args.id))
        print("[alpha] 已删除" if ok else "[alpha] 条目不存在")
        return 0 if ok else 2

    if action == "add":
        if not (args.wrong and args.correct):
            print("[alpha] add 需要 --wrong 与 --correct", file=sys.stderr)
            return 2
        try:
            record = store.add(
                persona_id, args.wrong, args.correct,
                scene=args.scene or "", user_id=args.user,
                source="runtime", note=args.note,
            )
        except CorrectionError as exc:
            print(f"[alpha] 条目非法：{exc}", file=sys.stderr)
            return 2
        print(f"[alpha] #{record.id} 已写入：不要说「{record.wrong}」→「{record.correct}」")
        return 0

    if action == "section":
        text = store.render_section(persona_id, user_id=args.user)
        print(text if text else "（无生效条目，不产生注入段）")
        return 0

    if action == "seed":
        from bridge.model import profile_from_dict

        target = Path(args.file).expanduser()
        if not target.is_file():
            print(f"[alpha] 找不到 {target}", file=sys.stderr)
            return 2
        profile = profile_from_dict(json.loads(target.read_text(encoding="utf-8")))
        added = store.seed_from_profile(profile.persona_id, profile.corrections,
                                        user_id=args.user)
        print(f"[alpha] 导入 {added} 条（已存在的跳过，不覆盖运行期修改）")
        return 0

    print(f"[alpha] 未知操作 {action}", file=sys.stderr)
    return 2


def _cmd_triggers(args: argparse.Namespace) -> int:
    from bridge.triggers import SIGNAL_LABELS, analyze

    try:
        profile = _load_profile_arg(args.target)
    except Exception as exc:  # noqa: BLE001 - 统一转成 CLI 错误
        print(f"[alpha] 无法加载 profile：{exc}", file=sys.stderr)
        return 2

    report = analyze(profile)
    print(f"[alpha] {report['persona_id']}  ·  可用于 critic: {'是' if report['usable'] else '否'}")
    print(f"\nphase_threshold = {report['phase_threshold']['value']}"
          f"（base {report['phase_threshold']['base']}，范围 {report['phase_threshold']['range']}）")
    if report["phase_threshold"]["basis"]:
        print(f"  {report['phase_threshold']['basis']}")

    print("\n信号先验：")
    for key, entry in report["signal_priors"].items():
        label = SIGNAL_LABELS[key][0]
        value = "—" if entry["value"] is None else f"{entry['value']:.2f}"
        flag = "" if entry["value"] is not None else "  (无依据)"
        print(f"  {label:<6}{value}{flag}")
        if entry["basis"]:
            print(f"        依据：{entry['basis']}")

    print(f"\n驱力敏感度：")
    for key, entry in report["drive_sensitivity"].items():
        value = "—" if entry["value"] is None else f"{entry['value']:.2f}"
        print(f"  {key:<12}{value}")

    cov = report["coverage"]
    print(f"\n覆盖：{cov['signals_with_evidence']}/{cov['signals_total']} 个信号有依据"
          + (f"，缺 {', '.join(cov['missing'])}" if cov["missing"] else ""))

    print("\ncritic persona_section：")
    for line in report["critic_persona_section"].splitlines():
        print(f"  {line}")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    import json as _json

    from bridge.consistency import verify_dir

    root = Path(args.root)
    if not root.is_dir():
        print(f"[alpha] 目录不存在: {root}", file=sys.stderr)
        return 2

    report = verify_dir(root, args.id or None)
    if not report["count"]:
        print(f"[alpha] {root} 下没有含 SOUL.md 的人格目录", file=sys.stderr)
        return 2

    print(f"[alpha] 校验 {root}（{report['count']} 个人格）")
    for persona in report["personas"]:
        mark = _STATUS_MARK.get(persona["status"], "?")
        print(f"\n{mark} {persona['persona_id']}  →  {persona['status']}")
        for item in persona["results"]:
            sub = _STATUS_MARK.get(item["status"], "?")
            print(f"   {sub} {item['name']:<9}{item['status']}")
            for reason in item["reasons"]:
                print(f"       - {reason}")

    print(f"\n[alpha] 整体: {report['status']}")
    if args.json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    if args.check:
        return 1 if report["status"] in ("fail", "warn") else 0
    return 1 if report["status"] == "fail" else 0


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from bridge.calibrate import calibrate
    from bridge.model import profile_from_dict

    repo = _repo()
    try:
        record = repo.get(args.user, args.persona_id)
    except PersonaError as exc:
        print(f"[alpha] {exc.message}", file=sys.stderr)
        return 2
    profile = profile_from_dict(record["profile"])
    hints = calibrate(profile)

    print(f"[alpha] {args.persona_id} 的标定建议（v1 启发式）")
    print("  drive_baseline:")
    for drive, asserted in hints.drive_baseline.items():
        mark = "缺失" if asserted.provenance.value == "missing" else f"{asserted.value}"
        print(f"    {drive:<12}{mark}")
    if hints.engine_params:
        print("  engine_params:")
        for key, asserted in hints.engine_params.items():
            mark = "缺失" if asserted.provenance.value == "missing" else f"{asserted.value}"
            print(f"    {key:<22}{mark}")
    if hints.calibration is not None:
        print(f"  依据: {hints.calibration.basis}")

    if not args.yes:
        print("[alpha] dry-run：未写入。加 --yes 写回 profile（engine 块整体替换）。")
        return 0

    version = int(record["state"]["version"])
    patch = {
        "engine": {
            "drive_baseline": {k: v.to_dict() | {"kind": "asserted"}
                               for k, v in hints.drive_baseline.items()},
            "engine_params": {k: v.to_dict() | {"kind": "asserted"}
                              for k, v in hints.engine_params.items()},
            "calibration": (hints.calibration.to_dict() | {"kind": "asserted"})
            if hints.calibration is not None else None,
        }
    }
    try:
        updated = repo.patch(args.user, args.persona_id, patch, version)
    except PersonaError as exc:
        print(f"[alpha] {exc.message}", file=sys.stderr)
        return 2
    print(f"[alpha] 已写回 {updated['persona_id']} → v{updated['version']}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from bridge.model import profile_from_dict
    from shell.persona_check import run_check

    try:
        record = _repo().get(args.user, args.persona_id)
    except PersonaError as exc:
        print(f"[alpha] {exc.message}", file=sys.stderr)
        return 2
    profile = profile_from_dict(record["profile"])
    report = run_check(
        profile,
        persona_id=args.persona_id,
        personas_dir=_mirror(),
        probe=args.probe,
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[alpha] 体检结果: {report['verdict']}")
        for name, section in report["sections"].items():
            status = section.get("status")
            detail = ""
            if name == "structure":
                detail = f"问题 {len(section.get('issues', []))} 项，缺口 {len(section.get('gaps', []))} 项"
                if section.get("metrics"):
                    detail += f"，口头禅 {section['metrics']['habits']} 条"
            elif name == "privacy":
                detail = f"{len(section.get('hits', []))} 处命中"
            elif section.get("reason"):
                detail = section["reason"]
            elif section.get("warnings"):
                detail = "；".join(section["warnings"])
            print(f"  {name:<10}{status:<9}{detail}")
        if report["fixes"]:
            print("建议：")
            for item in report["fixes"]:
                print(f"  - {item}")

    return 0 if report["verdict"] != "fail" else 1


def _cmd_delete(args: argparse.Namespace) -> int:
    if not args.yes:
        print(f"[alpha] dry-run：将软删除 {args.persona_id}。确认后加 --yes。")
        return 0
    try:
        _repo().delete(args.user, args.persona_id)
    except PersonaError as exc:
        print(f"[alpha] {exc.message}", file=sys.stderr)
        return 2
    print(f"[alpha] 已软删除 {args.persona_id}（可用 restore 恢复）")
    return 0


def _add_user(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user", default=argparse.SUPPRESS,
                        help="用户 id（默认取 ALPHA_USER 或 default）")


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[name-defined]
    persona = sub.add_parser("persona", help="创建/编辑人格（#85）")
    persona.add_argument("--user", default=os.getenv("ALPHA_USER", "default"), help="用户 id")
    psub = persona.add_subparsers(dest="persona_command")

    p_new = psub.add_parser("new", help="创建人格")
    p_new.add_argument("--from", dest="from_spec", default="scratch",
                       help="scratch | builtin:<id> | distill:<path>")
    p_new.add_argument("--id", dest="persona_id", help="persona slug（小写）")
    p_new.add_argument("--name", help="显示名")
    p_new.add_argument("--yes", action="store_true", help="落盘（默认 dry-run）")
    _add_user(p_new)
    p_new.set_defaults(func=_cmd_new)

    p_list = psub.add_parser("list", help="列出人格")
    p_list.add_argument("--include-deleted", action="store_true")
    _add_user(p_list)
    p_list.set_defaults(func=_cmd_list)

    p_show = psub.add_parser("show", help="查看人格 JSON")
    p_show.add_argument("persona_id")
    _add_user(p_show)
    p_show.set_defaults(func=_cmd_show)

    p_preview = psub.add_parser("preview", help="渲染 SOUL.md 并给出 diff")
    p_preview.add_argument("persona_id")
    _add_user(p_preview)
    p_preview.set_defaults(func=_cmd_preview)

    p_edit = psub.add_parser("edit", help="字段级修改")
    p_edit.add_argument("persona_id")
    p_edit.add_argument("--set", dest="set_values", action="append", default=[],
                        help="key=value，可重复")
    p_edit.add_argument("--yes", action="store_true")
    _add_user(p_edit)
    p_edit.set_defaults(func=_cmd_edit)

    p_cal = psub.add_parser("calibrate", help="按 v1 启发式标定 drive/engine_params")
    p_cal.add_argument("persona_id")
    p_cal.add_argument("--yes", action="store_true", help="写回 profile")
    _add_user(p_cal)
    p_cal.set_defaults(func=_cmd_calibrate)

    p_seeds = psub.add_parser("seeds", help="把口头禅素材打包成 seeds.bin")
    p_seeds.add_argument("persona_id")
    p_seeds.add_argument("--material", required=True, help="四行格式素材文件")
    p_seeds.add_argument("--out", help="输出路径（默认 .data/seeds.bin）")
    p_seeds.add_argument("--yes", action="store_true", help="写入文件")
    _add_user(p_seeds)
    p_seeds.set_defaults(func=_cmd_seeds)

    p_mem = psub.add_parser("memory", help="事实/共同记忆 → FTS5（#36）")
    p_mem.add_argument("persona_id")
    p_mem.add_argument("action", choices=["add", "search", "list", "seed", "context",
                                          "remove", "export"])
    p_mem.add_argument("--content", help="add 用：记忆内容")
    p_mem.add_argument("--query", help="search/context 用：检索词")
    p_mem.add_argument("--role", default="user", help="user/assistant/system/fact")
    p_mem.add_argument("--category", default="fact",
                       help="fact/event/preference/conversation")
    p_mem.add_argument("--importance", type=float, default=0.5)
    p_mem.add_argument("--limit", type=int, default=10)
    p_mem.add_argument("--id", help="remove 用：条目 id")
    p_mem.add_argument("--file", help="seed 用：profile.json 路径")
    p_mem.add_argument("--yes", action="store_true", help="remove 时确认")
    _add_user(p_mem)
    p_mem.set_defaults(func=_cmd_memory)

    p_corr = psub.add_parser("corrections", help="纠错层：运行期可写约束（#39）")
    p_corr.add_argument("persona_id")
    p_corr.add_argument("action", choices=["list", "add", "disable", "enable", "remove",
                                           "section", "seed"])
    p_corr.add_argument("--id", help="条目 id（disable/enable/remove 用）")
    p_corr.add_argument("--scene", help="触发场景")
    p_corr.add_argument("--wrong", help="不要说的话")
    p_corr.add_argument("--correct", help="改成的话")
    p_corr.add_argument("--note", help="备注")
    p_corr.add_argument("--file", help="seed 用：profile.json 路径")
    p_corr.add_argument("--all", action="store_true", help="list 时包含已停用")
    p_corr.add_argument("--yes", action="store_true", help="remove 时确认删除")
    _add_user(p_corr)
    p_corr.set_defaults(func=_cmd_corrections)

    p_trig = psub.add_parser("triggers", help="情绪触发 → critic 特征 + phase_threshold（#38）")
    p_trig.add_argument("target", help="persona_id 或 profile.json 路径")
    p_trig.add_argument("--json", action="store_true", help="输出机器可读报告")
    p_trig.set_defaults(func=_cmd_triggers)

    p_verify = psub.add_parser("verify", help="一致性校验：产物 ↔ loader / store 契约（#41）")
    p_verify.add_argument("root", nargs="?", default=".data/soul", help="含 <id>/SOUL.md 的目录")
    p_verify.add_argument("--id", action="append", help="只校验指定人格（可多次）")
    p_verify.add_argument("--json", action="store_true", help="输出机器可读报告")
    p_verify.add_argument("--check", action="store_true", help="warn 也返回非零（CI 模式）")
    p_verify.set_defaults(func=_cmd_verify)

    p_check = psub.add_parser("check", help="人格体检（结构/隐私/加载/探针）")
    p_check.add_argument("persona_id")
    p_check.add_argument("--json", action="store_true", help="输出 JSON")
    p_check.add_argument("--probe", action="store_true", help="启用风格探针（需引擎）")
    _add_user(p_check)
    p_check.set_defaults(func=_cmd_check)

    p_del = psub.add_parser("delete", help="软删除")
    p_del.add_argument("persona_id")
    p_del.add_argument("--yes", action="store_true")
    _add_user(p_del)
    p_del.set_defaults(func=_cmd_delete)

    return persona


def run(args: argparse.Namespace) -> int:
    handler = getattr(args, "func", None)
    if handler is None:
        print("[alpha] 用法: alpha persona {new,list,show,preview,edit,delete}", file=sys.stderr)
        return 2
    return handler(args)


__all__ = ["add_parser", "run"]
