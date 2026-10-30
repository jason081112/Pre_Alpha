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
    raw = os.getenv("ALPHA_PERSONA_MIRROR")
    path = Path(raw) if raw else BUILTIN_ROOT
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
