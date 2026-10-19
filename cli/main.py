"""`alpha` 命令行入口（骨架）。

子命令与实现阶段对应：
- `profile2soul` → P3（看板 #42）
- `chat`         → P4（看板 #47）
- `purge`        → P5（看板 #53）
"""

from __future__ import annotations

import argparse
import sys


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    print(f"[alpha] {args.command}: 尚未实现（骨架阶段，P1-2）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
