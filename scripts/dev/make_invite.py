#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""生成邀请码。只把哈希写入 auth.db，明文只打印一次。

用法：
    python scripts/dev/make_invite.py --label alpha --uses 5 --days 14
    python scripts/dev/make_invite.py --revoke ALPHA-XXXX-XXXX
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.auth.store import AuthStore  # noqa: E402

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_code() -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(10))
    return f"ALPHA-{body[:5]}-{body[5:]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成或撤销 Alpha 邀请码")
    parser.add_argument("--db", default=os.getenv("ALPHA_AUTH_DB", ""))
    parser.add_argument("--label", default="", help="备注，例如 alpha-invite")
    parser.add_argument("--uses", type=int, default=1, help="可用次数")
    parser.add_argument("--days", type=float, default=0, help="有效天数；0 表示不过期")
    parser.add_argument("--code", default="", help="自定义邀请码；默认随机生成")
    parser.add_argument("--revoke", default="", help="撤销指定邀请码")
    args = parser.parse_args(argv)

    db_path = Path(
        args.db or os.getenv("ALPHA_AUTH_DB", str(ROOT / ".data" / "auth.db"))
    )
    store = AuthStore(db_path)
    try:
        if args.revoke:
            ok = store.revoke_invite(args.revoke)
            print(f"{'revoked' if ok else 'not found'}: {args.revoke}")
            return 0 if ok else 1

        code = args.code.strip() or _new_code()
        expires_at = time.time() + args.days * 86400 if args.days > 0 else None
        store.add_invite(
            code,
            label=args.label,
            max_uses=args.uses,
            expires_at=expires_at,
        )
        print(f"invite: {code}")
        print(f"label: {args.label or '-'}  uses: {args.uses}  "
              f"expires: {args.days or 'never'} day(s)")
        print("只保存哈希；关闭终端后无法再次查看明文。")
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
