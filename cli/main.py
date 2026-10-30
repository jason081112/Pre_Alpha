"""`alpha` 命令行入口。

子命令与实现阶段对应：
- `profile2soul` → P3（看板 #42）
- `chat`         → P4（看板 #47）
- `purge`        → P5（看板 #53）
- `route`        → P1（看板 #70），开发态设置当前模型
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpha", description="Pre_Alpha 命令行入口")
    parser.add_argument("--version", action="version", version="alpha 0.0.0")
    sub = parser.add_subparsers(dest="command")

    p2s = sub.add_parser("profile2soul", help="把 Person Profile 转成 OpenHer 种子（P3 #42）")
    p2s.add_argument("path", nargs="?", help="profile 目录")

    chat = sub.add_parser("chat", help="与指定 persona 对话（P4 #47）")
    chat.add_argument("--persona", help="persona id，如 luna")

    purge = sub.add_parser("purge", help="清空本地数据（P5 #53）")
    purge.add_argument("--all", action="store_true", help="清空全部本地数据")

    route = sub.add_parser("route", help="查看/设置当前模型与回退链（P1 #70）")
    route_sub = route.add_subparsers(dest="route_command")

    route_set = route_sub.add_parser("set", help="设置当前模型")
    route_set.add_argument("--provider", required=True, help="provider id，如 ollama-cloud")
    route_set.add_argument("--model", required=True, help="模型名，如 gemma4:31b")
    route_set.add_argument("--user", default="local", help="用户标识（默认 local）")
    route_set.add_argument(
        "--fallback",
        default="",
        help='回退链，逗号分隔的 provider/model，如 "openai/gpt-4o,deepseek/deepseek-chat"',
    )

    route_show = route_sub.add_parser("show", help="查看当前模型")
    route_show.add_argument("--user", default="local", help="用户标识（默认 local）")

    from cli.persona import add_parser as add_persona_parser

    add_persona_parser(sub)

    return parser


def _store():
    from shell.store import RoutingStore

    db = os.getenv("ALPHA_ROUTING_DB") or str(ROOT / ".data" / "routing.db")
    return RoutingStore(db)


def _route_command(args: argparse.Namespace) -> int:
    from shell.providers import Registry, Route, RouteError

    if args.route_command == "show":
        record = _store().get(args.user)
        if record is None:
            print(f"[alpha] 用户 {args.user!r} 尚未选择模型（route_not_configured）", file=sys.stderr)
            return 2
        print(f"当前模型: {record.route.as_str()}")
        chain = ", ".join(r.as_str() for r in record.fallback) or "（空）"
        print(f"回退链:   {chain}")
        return 0

    if args.route_command != "set":
        print("[alpha] 用法: alpha route {set,show}", file=sys.stderr)
        return 2

    registry = Registry.load()
    try:
        primary = registry.resolve(args.provider, args.model)
        fallback = []
        for raw in filter(None, (part.strip() for part in args.fallback.split(","))):
            route = Route.parse(raw)
            registry.resolve(route.provider, route.model)
            if route == primary:
                raise RouteError(f"回退链不能包含当前模型: {raw}")
            if route in fallback:
                raise RouteError(f"回退链重复: {raw}")
            fallback.append(route)
        if len(fallback) > 4:
            raise RouteError("回退链最多 4 跳")
    except RouteError as exc:
        print(f"[alpha] {exc}", file=sys.stderr)
        return 2

    _store().put(args.user, primary, fallback)
    print(f"[alpha] 已设置 {args.user!r} 当前模型: {primary.as_str()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "route":
        return _route_command(args)
    if args.command == "persona":
        from cli.persona import run as run_persona

        return run_persona(args)
    print(f"[alpha] {args.command}: 尚未实现（骨架阶段，P1-2）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
