# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#75 账号注销：申请/撤销/到期执行 + 级联清除。

零依赖，直接跑：
    python3 tests/test_account.py

重点不是「接口返回 200」，而是「注销后按 user_id 查全空」。
"""

import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shell.account import (  # noqa: E402
    CONFIRM_WORD,
    DeletionStore,
    purge_user,
    run_due,
)
from shell.auth.scope import session_prefix  # noqa: E402

passed = 0
failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


tmp = Path(tempfile.mkdtemp(prefix="alpha-account-"))
clock = {"t": 1_800_000_000.0}
store = DeletionStore(tmp / "account.db", clock=lambda: clock["t"])
USER = "u-11111111-2222-3333-4444-555555555555"
OTHER = "u-99999999-8888-7777-6666-555555555555"

print("\n[1] 申请 / 幂等 / 撤销")
row = store.request(USER, cooling_days=7)
check("申请后状态 pending", row["status"] == "pending", row)
check("到期时间 = 现在 + 7 天",
      abs(row["execute_at"] - (clock["t"] + 7 * 86400)) < 1, row["execute_at_iso"])
check("确认字是「删除」", CONFIRM_WORD == "删除", CONFIRM_WORD)

clock["t"] += 3600
row2 = store.request(USER, cooling_days=7)
check("重复申请仍只有一条 pending",
      store.get(USER)["status"] == "pending"
      and row2["requested_at"] > row["requested_at"], row2["requested_at_iso"])

check("未到期不进 due", store.due() == [])
clock["t"] += 8 * 86400
due = store.due()
check("到期后进 due", len(due) == 1 and due[0]["user_id"] == USER, due)

check("撤销 pending 返回 True", store.cancel(USER) is True)
check("撤销后状态 cancelled", store.get(USER)["status"] == "cancelled")
check("重复撤销返回 False", store.cancel(USER) is False)
check("撤销后不再进 due", store.due() == [])

print("\n[2] 级联清除：建一个真实的用户数据面")
chat_db = tmp / "chat.db"
eng_db = tmp / "openher.db"
mem_db = tmp / "memory.db"

conn = sqlite3.connect(chat_db)
conn.execute("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, client_id TEXT, content TEXT)")
prefix = session_prefix(USER)
conn.executemany(
    "INSERT INTO chat_messages (client_id, content) VALUES (?, ?)",
    [
        (prefix, "hello"),
        (prefix + ":abcd1234", "我的第一条"),
        (prefix + ":abcd1234", "我的第二条"),
        (USER, "开场白按原始 user_id 存"),
        (session_prefix(OTHER) + ":deadbeef", "别人的"),
        ("raw-no-prefix", "没前缀的历史"),
    ],
)
conn.commit()
conn.close()

conn = sqlite3.connect(eng_db)
for table in ("chat_summary", "genome_state", "proactive_outbox", "style_memory"):
    conn.execute(f"CREATE TABLE {table} (user_id TEXT, v TEXT)")
    conn.executemany(
        f"INSERT INTO {table} (user_id, v) VALUES (?, ?)",
        [(USER, "x"), (USER, "y"), (OTHER, "z")],
    )
conn.execute("CREATE TABLE genesis_seed (v TEXT)")
conn.execute("INSERT INTO genesis_seed (v) VALUES ('全局种子')")
conn.commit()
conn.close()

conn = sqlite3.connect(mem_db)
conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, user_id TEXT, text TEXT)")
conn.executemany(
    "INSERT INTO memories (user_id, text) VALUES (?, ?)",
    [(USER, "喜欢热牛奶"), (USER, "怕黑"), (OTHER, "别人")],
)
conn.commit()
conn.close()

persona_root = tmp / "users"
(Path(persona_root) / USER / "personas" / "xiaolan").mkdir(parents=True)
(Path(persona_root) / USER / "personas" / "xiaolan" / "genome.json").write_text("{}")
(Path(persona_root) / OTHER).mkdir(parents=True)

backup_dir = tmp / "backups"
backup_dir.mkdir()
(backup_dir / f"alpha-{USER}-20261120.tar.gz").write_text("x")
(backup_dir / "alpha-other-20261120.tar.gz").write_text("x")
stale = backup_dir / "alpha-old-20260101.tar.gz"
stale.write_text("x")
old = time.time() - 90 * 86400
import os  # noqa: E402

os.utime(stale, (old, old))


class FakeAuth:
    def __init__(self):
        self.calls = []

    def delete_user(self, uid):
        self.calls.append(uid)
        return {"auth_user": 1, "auth_identity": 1, "auth_token": 2, "auth_consent": 1}


class FakeUsage:
    def __init__(self):
        self.calls = []

    def clear(self, uid):
        self.calls.append(uid)
        return 3


class FakeLimits:
    def __init__(self):
        self.usage = FakeUsage()


class FakeModeration:
    def __init__(self):
        self.calls = []

    def delete_user(self, uid):
        self.calls.append(uid)
        return {"moderation_report": 1, "moderation_action": 2, "moderation_ban": 0}


class FakeBilling:
    def __init__(self):
        self.calls = []

    def delete_user(self, uid):
        self.calls.append(uid)
        return {"billing_trial": 1, "billing_subscription": 1, "billing_checkout": 1}


class FakeCustom:
    def __init__(self):
        self.calls = []

    def delete_user(self, uid):
        self.calls.append(uid)
        return {"user_provider": 1, "provider_audit": 3}


class FakeExtra:
    def __init__(self):
        self.calls = []

    def delete_user(self, uid):
        self.calls.append(uid)
        return 2


class FakeRoutes:
    def __init__(self):
        self.calls = []

    def delete(self, uid):
        self.calls.append(uid)
        return True


auth, limits, moderation = FakeAuth(), FakeLimits(), FakeModeration()
billing, custom, extra, routes = FakeBilling(), FakeCustom(), FakeExtra(), FakeRoutes()

receipt = purge_user(
    USER,
    auth_store=auth,
    limits=limits,
    moderation=moderation,
    billing=billing,
    custom_store=custom,
    agent_extra=extra,
    routes=routes,
    chat_db=chat_db,
    engine_db=eng_db,
    memory_db=mem_db,
    persona_root=persona_root,
    backup_dir=backup_dir,
    backup_retention_days=30,
    now=time.time(),
)

check("回执含 user_id 与时间", receipt["user_id"] == USER and receipt["deleted_at"], receipt)
check("auth 删除计数入库", receipt["scope"]["auth"]["auth_token"] == 2, receipt["scope"]["auth"])
check("billing 删除计数", receipt["scope"]["billing"]["billing_subscription"] == 1)
check("chat.db 删掉本人 4 条（含无冒号前缀与原始 user_id）",
      receipt["files"]["chat.db"].get("chat_messages") == 4, receipt["files"]["chat.db"])
check("引擎库四表各删 2 条",
      all(v == 2 for v in receipt["files"]["openher.db"].values()),
      receipt["files"]["openher.db"])
check("memory.db 删 2 条", receipt["files"]["memory.db"].get("memories") == 2)
check("人格目录被删除", receipt["persona_dirs"] == [str(Path(persona_root) / USER)],
      receipt["persona_dirs"])
check("备份删除含 user_id 的文件",
      receipt["backups"]["removed"] == [f"alpha-{USER}-20261120.tar.gz"],
      receipt["backups"])
check("过期备份被轮换清除", receipt["backups"]["expired_rotated"] == 1, receipt["backups"])

print("\n[3] 注销后按 user_id 查全空")
conn = sqlite3.connect(chat_db)
left = conn.execute(
    "SELECT COUNT(*) FROM chat_messages WHERE client_id IN (?, ?) OR client_id LIKE ?",
    (prefix, USER, prefix + ":%"),
).fetchone()[0]
total = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
conn.close()
check("chat.db 本人残留 0", left == 0, left)
check("他人消息未被误删", total == 2, total)

conn = sqlite3.connect(eng_db)
left_engine = sum(
    conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (USER,)).fetchone()[0]
    for t in ("chat_summary", "genome_state", "proactive_outbox", "style_memory")
)
seed = conn.execute("SELECT COUNT(*) FROM genesis_seed").fetchone()[0]
other_engine = conn.execute("SELECT COUNT(*) FROM chat_summary WHERE user_id = ?", (OTHER,)).fetchone()[0]
conn.close()
check("引擎库本人残留 0", left_engine == 0, left_engine)
check("全局人格种子保留", seed == 1, seed)
check("他人引擎数据保留", other_engine == 1, other_engine)

conn = sqlite3.connect(mem_db)
left_mem = conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", (USER,)).fetchone()[0]
other_mem = conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", (OTHER,)).fetchone()[0]
conn.close()
check("memory.db 本人残留 0", left_mem == 0, left_mem)
check("他人记忆保留", other_mem == 1, other_mem)

check("本人人格目录已消失", not (Path(persona_root) / USER).exists())
check("他人人格目录保留", (Path(persona_root) / OTHER).exists())
check("非本人备份保留", (backup_dir / "alpha-other-20261120.tar.gz").exists())

print("\n[4] 到期执行：回执落库 + 逐 scope 审计")
clock["t"] += 10 * 86400
store.request(USER, cooling_days=0)
done = run_due(store, lambda uid: receipt, now=clock["t"])
check("到期任务执行一次", len(done) == 1 and done[0]["user_id"] == USER, done)
stored = store.get(USER)
check("状态 done 且存了回执", stored["status"] == "done" and stored["receipt"]["user_id"] == USER)
logs = store.logs(USER)
scopes = [r["scope"] for r in logs]
check("审计含各来源", "auth" in scopes and "file:chat.db" in scopes and "file:memory.db" in scopes, scopes)
check("审计记录了删除行数", any(r["deleted"] > 0 for r in logs), logs)
check("执行后不再进 due", store.due(now=clock["t"]) == [])


def boom(uid):
    raise RuntimeError("库打不开")


store.request(OTHER, cooling_days=0)
done = run_due(store, boom, now=clock["t"])
check("单个失败不中断批次", len(done) == 1 and "error" in done[0], done)
check("失败写 error 审计且保持 pending",
      any(r["scope"] == "error" for r in store.logs(OTHER))
      and store.get(OTHER)["status"] == "pending", store.logs(OTHER))

print("\n[5] 未配置备份时如实说明")
empty = purge_user(USER, chat_db=None, engine_db=None, memory_db=None, now=time.time())
check("没备份目录不假装有", empty["backups"]["removed"] == [] and empty["backups"]["note"], empty["backups"])
check("库不存在时跳过而不报错", empty["files"]["chat.db"] == {}, empty["files"])

store.close()
print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
