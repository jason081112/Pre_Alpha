# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""接缝守卫：确保外壳对上游的 5 处运行时注入在启动时真的生效（#93）。

背景（见 docs/adr/0002-shell-as-product-layer.md）：
`shell/app.py` 复用上游 FastAPI 实例，靠运行时替换 5 个对象接入产品能力。
上游是 external 代码（`vendor/OpenHer`，替换目录即升级），一旦它改了函数名、
签名或模块结构，`getattr` 会拿不到目标，而现有代码大量写成：

    loader = getattr(upstream, "persona_loader", None)
    if loader is None:
        return

——静默跳过。结果是「服务能起、能聊，但人格改了不生效 / 记录没隔离」。
这类故障不会报错，只会表现成「怎么感觉不太对」。

本模块在 startup 时把这 5 条接缝逐条验证，缺失即 fail-fast。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "Seam",
    "SeamReport",
    "SEAM_NAMES",
    "check_seams",
]

SEAM_NAMES = (
    "get_or_create_session",
    "_persist_agent",
    "llm_client",
    "chat_log_store",
    "persona_loader.reload",
)


@dataclass
class Seam:
    """一条接缝的检查结果。"""

    name: str
    ok: bool
    detail: str = ""
    fatal: bool = True


@dataclass
class SeamReport:
    seams: list[Seam] = field(default_factory=list)

    @property
    def missing(self) -> list[Seam]:
        return [s for s in self.seams if not s.ok and s.fatal]

    @property
    def ok(self) -> bool:
        return not self.missing

    def lines(self) -> list[str]:
        out = []
        for s in self.seams:
            mark = "✓" if s.ok else ("✗" if s.fatal else "⚠")
            out.append(f"  {mark} {s.name}{(' — ' + s.detail) if s.detail else ''}")
        return out

    def fail_message(self) -> str:
        names = ", ".join(s.name for s in self.missing)
        return (
            "接缝缺失，拒绝启动："
            f"{names}\n"
            "上游（vendor/OpenHer）可能已重构。请检查 shell/seams.py 的检查项与\n"
            "docs/adr/0002-shell-as-product-layer.md 的接缝表；确认无害后可设\n"
            "ALPHA_SEAM_STRICT=0 降级为告警。\n"
            + "\n".join(self.lines())
        )


def _is_installed(upstream: Any, attr: str, expected: Any) -> tuple[bool, str]:
    """属性存在，且已被替换成我们注入的对象（不是上游原版）。"""
    if not hasattr(upstream, attr):
        return False, f"上游没有 {attr}"
    actual = getattr(upstream, attr)
    if actual is None:
        return False, f"{attr} 为 None"
    if expected is not None and actual is not expected:
        return (
            False,
            f"{attr} 仍是上游原版（未注入）",
        )
    return True, ""


def check_seams(
    upstream: Any,
    *,
    get_or_create_session: Any | None = None,
    persist_agent: Any | None = None,
    llm_client: Any | None = None,
    chat_log_store: Any | None = None,
    persona_reload_probe: Callable[[], bool] | None = None,
) -> SeamReport:
    """逐条验证 5 个接缝。

    参数传入「我们期望装上去的那个对象」，用于 identity 比对 ——
    只判断 `hasattr` 不够：上游原版函数也在，能拿到就以为装上了。
    """

    report = SeamReport()

    ok, detail = _is_installed(
        upstream, "get_or_create_session", get_or_create_session
    )
    report.seams.append(Seam("get_or_create_session", ok, detail))

    ok, detail = _is_installed(upstream, "_persist_agent", persist_agent)
    report.seams.append(Seam("_persist_agent", ok, detail))

    ok, detail = _is_installed(upstream, "llm_client", llm_client)
    report.seams.append(Seam("llm_client", ok, detail))

    # chat_log_store 允许上游本来就没有持久化（None），此时不算缺失，
    # 但要记一条 warn —— 没有它，跨用户历史隔离靠 session 前缀兜底。
    if not hasattr(upstream, "chat_log_store"):
        report.seams.append(
            Seam(
                "chat_log_store",
                False,
                "上游没有 chat_log_store（未启用持久化）",
                fatal=False,
            )
        )
    elif getattr(upstream, "chat_log_store") is None:
        report.seams.append(
            Seam(
                "chat_log_store",
                False,
                "上游未启用持久化，历史隔离退化",
                fatal=False,
            )
        )
    else:
        ok, detail = _is_installed(upstream, "chat_log_store", chat_log_store)
        report.seams.append(Seam("chat_log_store", ok, detail))

    # persona_loader 是对象而非函数，检查 reload 可调用。
    loader = getattr(upstream, "persona_loader", None)
    if loader is None:
        report.seams.append(
            Seam("persona_loader.reload", False, "上游没有 persona_loader")
        )
    elif not callable(getattr(loader, "reload", None)):
        report.seams.append(
            Seam(
                "persona_loader.reload",
                False,
                "persona_loader 没有 reload()",
            )
        )
    else:
        if persona_reload_probe is not None:
            try:
                ok_probe = bool(persona_reload_probe())
            except Exception as exc:  # noqa: BLE001
                report.seams.append(
                    Seam(
                        "persona_loader.reload",
                        False,
                        f"探针调用失败：{type(exc).__name__}",
                    )
                )
            else:
                report.seams.append(
                    Seam(
                        "persona_loader.reload",
                        ok_probe,
                        "" if ok_probe else "reload() 返回非真值",
                    )
                )
        else:
            report.seams.append(Seam("persona_loader.reload", True))

    return report
