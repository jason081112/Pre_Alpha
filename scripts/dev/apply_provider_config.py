#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""把 config/provider.yaml 应用到 vendor/OpenHer/providers/api.yaml（#92）。

为什么需要这个脚本：

上游 `providers/api_config.py` 第 39 行把配置路径写死成 `providers/api.yaml`，
且 `base_url` **只从 yaml 的 provider preset 读，没有任何环境变量覆盖**。于是：

* 默认 provider 是 `gemini`，无 key 时启动直接失败（不是降级）
* 想换 provider 必须手改 vendor 里的 yaml
* `vendor/` 已 gitignore → 换机器或重新 fetch 都要重配，且没留下任何改了什么 的记录

本脚本把「改 vendor」变成「改 config/provider.yaml 再跑一次」，配置本身入库、可 review。

Usage::

    python3 scripts/dev/apply_provider_config.py           # 写入
    python3 scripts/dev/apply_provider_config.py --check   # 只比对，不一致退出码 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "provider.yaml"
API_YAML = ROOT / "vendor" / "OpenHer" / "providers" / "api.yaml"

REQUIRED = ("provider", "base_url", "api_key_env")


def load_config(path: Path = CONFIG) -> dict:
    if not path.is_file():
        raise SystemExit(f"[config] 找不到 {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing = [k for k in REQUIRED if not data.get(k)]
    if missing:
        raise SystemExit(f"[config] {path.name} 缺字段: {', '.join(missing)}")
    return data


def build_document(base: dict, cfg: dict) -> dict:
    """在现有 api.yaml 基础上覆盖当前生效的 provider，保留其他 preset。"""
    doc = dict(base or {})
    llm = dict(doc.get("llm") or {})
    providers = dict(llm.get("providers") or {})

    name = cfg["provider"]
    preset = dict(providers.get(name) or {})
    preset["base_url"] = cfg["base_url"]
    preset["api_key_env"] = cfg["api_key_env"]
    if cfg.get("default_model"):
        preset["default_model"] = cfg["default_model"]
    providers[name] = preset

    llm["provider"] = name
    llm["providers"] = providers
    doc["llm"] = llm
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="应用 provider 配置到 vendor api.yaml")
    parser.add_argument("--check", action="store_true", help="只比对，不写入")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--target", type=Path, default=API_YAML)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)

    if not args.target.is_file():
        raise SystemExit(
            f"[config] 找不到 {args.target}\n"
            f"         先跑 scripts/dev/fetch_openher.sh 把上游拉下来"
        )

    base = yaml.safe_load(args.target.read_text(encoding="utf-8")) or {}
    document = build_document(base, cfg)
    rendered = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)

    if args.check:
        current = args.target.read_text(encoding="utf-8")
        if current.strip() == rendered.strip():
            print(f"[config] 一致（provider={cfg['provider']}）")
            return 0
        print(f"[config] 不一致：{args.target} 需要重新生成", file=sys.stderr)
        return 1

    args.target.write_text(rendered, encoding="utf-8")
    print(f"[config] provider={cfg['provider']} base_url={cfg['base_url']} "
          f"model={cfg.get('default_model', '(未指定)')}")
    print(f"[config] 已写入 {args.target.relative_to(ROOT)}")
    print(f"[config] 密钥不落盘：运行时从 ${cfg['api_key_env']} 读取")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
