"""骨架冒烟测试：包可导入、CLI 可解析、目录结构就位。

零第三方依赖，可直接运行：

    python3 tests/test_smoke.py

装了 pytest 时也能被自动收集（函数名均为 `test_*`）。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_packages_import() -> None:
    for mod in (
        "bridge",
        "bridge.profile2soul",
        "cli",
        "cli.main",
        "shell",
        "shell.auth",
        "shell.auth.middleware",
        "shell.auth.scope",
        "shell.auth.service",
        "shell.auth.store",
        "shell.crypto",
        "shell.custom",
        "shell.providers",
        "shell.routing",
        "shell.store",
    ):
        importlib.import_module(mod)


def test_cli_parses() -> None:
    from cli.main import build_parser, main

    parser = build_parser()
    assert parser.prog == "alpha"

    args = parser.parse_args(["chat", "--persona", "luna"])
    assert args.command == "chat" and args.persona == "luna"

    # 未实现子命令返回非零退出码，但不抛异常
    assert main(["chat", "--persona", "luna"]) == 2
    assert main([]) == 0


def test_skeleton_dirs() -> None:
    for rel in ("bridge", "cli", "docs", "tests", "tests/fixtures", "scripts"):
        assert (ROOT / rel).is_dir(), f"缺少目录: {rel}"


def test_profile2soul_identity_mapping() -> None:
    """#32：身份字段应能映射为 frontmatter；正文仍留待 #33–#38。"""
    from bridge.model import Asserted, Identity, PersonProfile
    from bridge.profile2soul import profile_to_soul

    profile = PersonProfile(persona_id="luna", identity=Identity(name=Asserted.source("Luna")))
    result = profile_to_soul(profile)
    assert result["frontmatter"]["name"] == "Luna"
    assert result["body"] == {}
    assert result["markdown"].startswith("---")


if __name__ == "__main__":
    test_packages_import()
    test_cli_parses()
    test_skeleton_dirs()
    test_profile2soul_identity_mapping()
    print("smoke ok")
