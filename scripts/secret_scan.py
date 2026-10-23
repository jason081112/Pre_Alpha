#!/usr/bin/env python3
"""公开仓库守卫：禁止私有路径、扫描疑似密钥。

    python3 scripts/secret_scan.py

退出码 1 表示发现问题。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PATHS = re.compile(r"^(cloud/|prompts/pro/|evals/|\.data/)")
FORBIDDEN_FILES = {"master.key", "master.salt", ".env"}
SKIP_PREFIXES = ("vendor/", "LICENSES/")

PATTERNS = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github_pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "github_oauth": re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    "google_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "generic_secret": re.compile(
        r"(?i)\b(api[_-]?key|secret|password|access[_-]?token)\b\s*[:=]\s*"
        r"[\"'][^\"']{16,}[\"']"
    ),
}

ALLOW = (
    "sk-xxx",
    "sk-test",
    "sk-secret",
    "your-",
    "example",
    "placeholder",
    "***",
    "<token>",
    "test-key",
)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    findings: list[str] = []

    for rel in tracked_files():
        if rel.startswith(SKIP_PREFIXES):
            continue
        path = ROOT / rel
        if FORBIDDEN_PATHS.match(rel) or path.name in FORBIDDEN_FILES:
            findings.append(f"{rel}:0 forbidden path")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for name, pattern in PATTERNS.items():
                match = pattern.search(line)
                if not match:
                    continue
                snippet = match.group(0).lower()
                if any(token in snippet for token in ALLOW):
                    continue
                findings.append(f"{rel}:{line_no} {name}")

    if findings:
        print("发现疑似问题：", file=sys.stderr)
        for item in findings:
            print("  " + item, file=sys.stderr)
        return 1

    print(f"secret scan ok ({len(tracked_files())} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
