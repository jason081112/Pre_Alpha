# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#59 仓库与运行库的「无原文、无密钥」扫描。

    python3 scripts/dev/check_repo_hygiene.py
    python3 scripts/dev/check_repo_hygiene.py --data-dir /var/lib/prealpha

仓库部分（默认，CI 跑）：
- 没有跟踪私钥、API Key、云厂商 AccessKey、GitHub token
- 没有跟踪 SQLite 数据库
- 没有跟踪 .env / 凭据文件
- 没有跟踪聊天记录导出（fixtures 里的测试语料除外）

运行库部分（--data-dir，部署后手工跑）：
- 数据目录里的 .db 不含明文 Key / 私钥（BYOK Key 必须是密文）
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 测试语料是刻意提交的，不算泄漏。
FIXTURE_PREFIXES = ("tests/fixtures/",)

SECRET_PATTERNS = (
    ("私钥", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")),
    ("API Key", re.compile(rb"sk-[A-Za-z0-9_-]{20,}")),
    ("阿里云 AccessKey", re.compile(rb"(?:AKID|LTAI)[A-Za-z0-9]{12,}")),
    ("GitHub token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{25,}")),
    ("Google API Key", re.compile(rb"AIza[A-Za-z0-9_-]{30,}")),
)

FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".pem", ".key", ".p12", ".pfx")
FORBIDDEN_NAMES = (".env", ".env.local", ".env.production", "credentials.json", "secrets.json")

CHAT_DUMP = re.compile(rb'"role"\s*:\s*"(?:user|assistant)"[\s\S]{0,400}?"content"\s*:\s*"', re.I)
CHAT_SQL = re.compile(rb"INSERT INTO\s+chat_messages", re.I)


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def check_repo() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    files = _tracked()
    results.append(("扫描 git 跟踪文件", bool(files), f"{len(files)} 个文件"))

    bad_db = [f for f in files if f.lower().endswith(FORBIDDEN_SUFFIXES)]
    results.append(("没有跟踪数据库/私钥文件", not bad_db, ", ".join(bad_db[:5])))

    bad_env = [f for f in files if Path(f).name in FORBIDDEN_NAMES]
    results.append(("没有跟踪 .env / 凭据文件", not bad_env, ", ".join(bad_env[:5])))

    leaks: list[str] = []
    dumps: list[str] = []
    for rel in files:
        if rel.startswith(FIXTURE_PREFIXES):
            continue
        path = ROOT / rel
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(data):
                leaks.append(f"{rel}（{label}）")
        # 只查「数据文件」：文档里出现协议示例 JSON 是正常的
        if rel.endswith((".json", ".jsonl", ".sql")) and (
            CHAT_DUMP.search(data) or CHAT_SQL.search(data)
        ):
            dumps.append(rel)

    results.append(("没有明文密钥/私钥进入仓库", not leaks, "; ".join(leaks[:5])))
    results.append(("没有跟踪聊天记录导出", not dumps, ", ".join(dumps[:5])))
    return results


def check_data_dir(target: Path) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    if not target.is_dir():
        return [("数据目录存在", False, str(target))]

    dbs = sorted(p for p in target.glob("*.db") if p.is_file())
    results.append(("找到运行库", bool(dbs), ", ".join(p.name for p in dbs)))

    plain: list[str] = []
    for db in dbs:
        try:
            data = db.read_bytes()
        except OSError as exc:
            results.append((f"读取 {db.name}", False, str(exc)))
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(data):
                plain.append(f"{db.name}（{label}）")

    results.append(("运行库不含明文 Key / 私钥", not plain, "; ".join(plain)))
    return results


def main(argv: list[str]) -> int:
    results = check_repo()
    if "--data-dir" in argv:
        idx = argv.index("--data-dir")
        raw = argv[idx + 1] if idx + 1 < len(argv) else ""
        if not raw:
            print("✗ --data-dir 需要一个路径")
            return 2
        results.extend(check_data_dir(Path(raw).resolve()))

    failed = 0
    for name, ok, detail in results:
        if not ok:
            failed += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
