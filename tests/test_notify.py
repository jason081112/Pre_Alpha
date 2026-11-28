# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SMTP 通知的离线测试：只验证消息内容与环境解析，不发信。

    python3 tests/test_notify.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shell.notify import EmailNotifier, from_env, report_message  # noqa: E402

ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name}{' — ' + detail if detail else ''}")


REPORT = {
    "id": 12,
    "category": "self_harm",
    "user_id": "u-1",
    "target": "xiaolan",
    "detail": "测试内容",
    "created_at": 1_795_000_000,
}

msg = report_message(REPORT, to="ops@example.com")
body = msg.get_content()

check("主题含编号与中文分类", "新举报 #12" in msg["Subject"] and "自伤" in msg["Subject"], msg["Subject"])
check("收件人写入", msg["To"] == "ops@example.com")
check("正文含用户与内容", "u-1" in body and "测试内容" in body)
check("正文含对象", "xiaolan" in body)
check("正文含处理入口", "/api/admin/moderation/reports" in body)
check("UTF-8 编码", msg.get_content_charset() == "utf-8")

unknown = report_message({**REPORT, "category": "weird"}, to="a@b.c")
check("未知分类原样展示", "weird" in unknown["Subject"], unknown["Subject"])

empty = report_message({"id": 1, "category": "other"}, to="a@b.c")
check("缺内容时的占位", "（未填写）" in empty.get_content())
check("缺时间不崩", "编号：#1" in empty.get_content())

base = {
    "ALPHA_SMTP_HOST": "smtp.163.com",
    "ALPHA_SMTP_USER": "prealpha@163.com",
    "ALPHA_SMTP_PASS": "secret",
}
n = from_env(base)
check("缺省端口 465", n is not None and n.port == 465)
check("发件人默认同账号", n.sender == "prealpha@163.com")
check("收件人默认同账号", n.to == "prealpha@163.com")
check("configured 为真", n.configured)

n2 = from_env({**base, "ALPHA_SMTP_PORT": "587", "ALPHA_NOTIFY_EMAIL": "ops@example.com",
               "ALPHA_SMTP_FROM": "noreply@example.com"})
check("端口可改 587", n2.port == 587)
check("收件人可覆盖", n2.to == "ops@example.com")
check("发件人可覆盖", n2.sender == "noreply@example.com")

check("缺密码返回 None", from_env({"ALPHA_SMTP_HOST": "h", "ALPHA_SMTP_USER": "u"}) is None)
check("缺主机返回 None", from_env({"ALPHA_SMTP_USER": "u", "ALPHA_SMTP_PASS": "p"}) is None)
check("端口非法回落 465", from_env({**base, "ALPHA_SMTP_PORT": "abc"}).port == 465)
check("显式空 env 不读进程环境", from_env({}) is None)

manual = EmailNotifier("h", 465, "u@x", "p", sender="", to="")
check("手工构造默认收件人", manual.to == "u@x")

print(f"\n{ok}/{ok + fail} passed")
sys.exit(1 if fail else 0)
