# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""邮件通知（零依赖，只用标准库 smtplib）。

现在用于「新举报」通知运营邮箱；配好 SMTP 就会发，没配就静默跳过。
163 邮箱必须用授权码而不是登录密码，且发件人必须等于登录账号。

环境变量：

    ALPHA_SMTP_HOST    smtp.163.com
    ALPHA_SMTP_PORT    465（SSL）或 587（STARTTLS）
    ALPHA_SMTP_USER    prealpha@163.com
    ALPHA_SMTP_PASS    授权码
    ALPHA_SMTP_FROM    可选，默认同 USER
    ALPHA_NOTIFY_EMAIL 收件人，默认同 USER
"""

from __future__ import annotations

import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate

__all__ = ["EmailNotifier", "from_env", "report_message"]

_BANNED_CATEGORIES = {
    "sexual": "色情",
    "violence": "暴力",
    "hate": "仇恨",
    "csam": "儿童性剥削",
    "impersonation": "冒充真人",
    "copyright": "侵权",
    "self_harm": "自伤/自杀",
    "other": "其他",
}


def _stamp(ts: float | None) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts or time.time())))
    except (TypeError, ValueError):
        return time.strftime("%Y-%m-%d %H:%M:%S")


def report_message(report: dict, *, to: str) -> EmailMessage:
    """把举报记录变成一封邮件；不碰网络，方便测试。"""
    rid = report.get("id")
    cat = str(report.get("category") or "other")
    label = _BANNED_CATEGORIES.get(cat, cat)
    subject = f"[Alpha] 新举报 #{rid} · {label}"
    detail = str(report.get("detail") or "").strip() or "（未填写）"
    lines = [
        f"编号：#{rid}",
        f"分类：{label}（{cat}）",
        f"时间：{_stamp(report.get('created_at'))}",
        f"用户：{report.get('user_id') or '-'}",
        f"对象：{report.get('target') or '-'}",
        "",
        "举报内容：",
        detail,
        "",
        "处理：管理端 → /api/admin/moderation/reports",
        "      ssh 后可用 sqlite3 /var/lib/prealpha/moderation.db 查询",
    ]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg.set_content("\n".join(lines), charset="utf-8")
    return msg


class EmailNotifier:
    """最小 SMTP 发信器；每次发送新建连接，失败抛异常由调用方吞掉。"""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        *,
        sender: str = "",
        to: str = "",
        timeout: float = 12.0,
    ) -> None:
        self.host = host.strip()
        self.port = int(port)
        self.user = user.strip()
        self.password = password
        self.sender = (sender or user).strip()
        self.to = (to or sender or user).strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.host and self.port and self.user and self.password and self.to)

    def send(self, msg: EmailMessage) -> None:
        msg["From"] = formataddr(("Alpha", self.sender))
        if not msg["To"]:
            msg["To"] = self.to
        if self.port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=ctx) as s:
                s.login(self.user, self.password)
                s.send_message(msg)
            return
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            s.login(self.user, self.password)
            s.send_message(msg)

    def send_report(self, report: dict) -> None:
        self.send(report_message(report, to=self.to))


def from_env(env: dict | None = None) -> EmailNotifier | None:
    """读环境变量；缺关键项返回 None（不报错，静默降级）。"""
    e = os.environ if env is None else env
    host = (e.get("ALPHA_SMTP_HOST") or "").strip()
    user = (e.get("ALPHA_SMTP_USER") or "").strip()
    password = (e.get("ALPHA_SMTP_PASS") or "").strip()
    if not (host and user and password):
        return None
    try:
        port = int((e.get("ALPHA_SMTP_PORT") or "465").strip())
    except ValueError:
        port = 465
    notifier = EmailNotifier(
        host,
        port,
        user,
        password,
        sender=(e.get("ALPHA_SMTP_FROM") or user).strip(),
        to=(e.get("ALPHA_NOTIFY_EMAIL") or e.get("ALPHA_SMTP_FROM") or user).strip(),
    )
    return notifier if notifier.configured else None
