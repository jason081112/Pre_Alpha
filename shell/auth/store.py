# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""认证数据持久化。

表结构刻意按「用户 + 身份」拆开：
- auth_user：账号主体
- auth_identity：email / phone 等登录身份，手机号后置时不用改表
- auth_token：只存 token 哈希，不存明文
- auth_invite：只存邀请码哈希，支持次数、过期、撤销

所有外部输入在 service 层归一化；本层只负责原子读写。
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS auth_user (
    id         TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    disabled   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS auth_identity (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('email', 'phone')),
    identifier    TEXT NOT NULL,
    normalized    TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    verified_at   REAL,
    created_at    REAL NOT NULL,
    UNIQUE (kind, normalized),
    FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_auth_identity_user
    ON auth_identity(user_id);

CREATE TABLE IF NOT EXISTS auth_token (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    expires_at  REAL NOT NULL,
    created_at  REAL NOT NULL,
    revoked_at  REAL,
    parent_hash TEXT,
    FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_auth_token_user
    ON auth_token(user_id, kind);

CREATE TABLE IF NOT EXISTS auth_invite (
    code_hash    TEXT PRIMARY KEY,
    label        TEXT NOT NULL DEFAULT '',
    max_uses     INTEGER NOT NULL DEFAULT 1,
    used_count   INTEGER NOT NULL DEFAULT 0,
    expires_at   REAL,
    revoked_at   REAL,
    created_at   REAL NOT NULL,
    last_used_at REAL
);
"""


def hash_secret(value: str) -> str:
    """token / 邀请码只落 SHA-256；原文不进入数据库。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthUser:
    id: str
    created_at: float
    email: str | None = None
    phone: str | None = None
    disabled: bool = False

    def as_public(self) -> dict:
        data: dict = {"id": self.id, "email": self.email, "phone": self.phone}
        return data


@dataclass(frozen=True)
class IdentityRecord:
    id: str
    user: AuthUser
    kind: str
    identifier: str
    normalized: str
    password_hash: str = field(repr=False)
    verified_at: float | None = None


@dataclass(frozen=True)
class TokenRecord:
    token_hash: str
    user_id: str
    kind: str
    expires_at: float
    created_at: float
    revoked_at: float | None = None
    parent_hash: str | None = None


@dataclass(frozen=True)
class InviteRecord:
    code_hash: str
    label: str
    max_uses: int
    used_count: int
    expires_at: float | None
    revoked_at: float | None
    created_at: float
    last_used_at: float | None


class AuthStore:
    """认证 SQLite 存储。线程安全，单连接。"""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ── 用户与身份 ────────────────────────────────────────
    def create_user_with_identity(
        self,
        *,
        kind: str,
        identifier: str,
        normalized: str,
        password_hash: str,
        verified_at: float | None = None,
    ) -> AuthUser:
        if kind not in ("email", "phone"):
            raise ValueError("unsupported identity kind")
        now = time.time()
        user_id = str(uuid.uuid4())
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO auth_user (id, created_at) VALUES (?, ?)",
                    (user_id, now),
                )
                self._conn.execute(
                    "INSERT INTO auth_identity "
                    "(id, user_id, kind, identifier, normalized, password_hash, verified_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        user_id,
                        kind,
                        identifier,
                        normalized,
                        password_hash,
                        verified_at,
                        now,
                    ),
                )
                self._conn.commit()
        except sqlite3.IntegrityError as exc:
            with self._lock:
                self._conn.rollback()
                self._conn.execute("DELETE FROM auth_user WHERE id = ?", (user_id,))
                self._conn.commit()
            raise ValueError("identity_exists") from exc
        return self.get_user(user_id)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> AuthUser | None:
        with self._lock:
            user_row = self._conn.execute(
                "SELECT id, created_at, disabled FROM auth_user WHERE id = ?",
                (user_id,),
            ).fetchone()
            if user_row is None:
                return None
            identities = self._conn.execute(
                "SELECT kind, identifier FROM auth_identity WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        email = next((r["identifier"] for r in identities if r["kind"] == "email"), None)
        phone = next((r["identifier"] for r in identities if r["kind"] == "phone"), None)
        return AuthUser(
            id=user_row["id"],
            created_at=user_row["created_at"],
            email=email,
            phone=phone,
            disabled=bool(user_row["disabled"]),
        )

    def get_identity(self, kind: str, normalized: str) -> IdentityRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, user_id, kind, identifier, normalized, password_hash, verified_at "
                "FROM auth_identity WHERE kind = ? AND normalized = ?",
                (kind, normalized),
            ).fetchone()
        if row is None:
            return None
        user = self.get_user(row["user_id"])
        if user is None:
            return None
        return IdentityRecord(
            id=row["id"],
            user=user,
            kind=row["kind"],
            identifier=row["identifier"],
            normalized=row["normalized"],
            password_hash=row["password_hash"],
            verified_at=row["verified_at"],
        )

    # ── 邀请码 ────────────────────────────────────────────
    def add_invite(
        self,
        code: str,
        *,
        label: str = "",
        max_uses: int = 1,
        expires_at: float | None = None,
    ) -> InviteRecord:
        code = code.strip()
        if not code:
            raise ValueError("invite code 不能为空")
        if max_uses < 1:
            raise ValueError("max_uses 必须 >= 1")
        now = time.time()
        code_hash = hash_secret(code)
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO auth_invite "
                    "(code_hash, label, max_uses, used_count, expires_at, created_at) "
                    "VALUES (?, ?, ?, 0, ?, ?)",
                    (code_hash, label, max_uses, expires_at, now),
                )
                self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("invite_exists") from exc
        return InviteRecord(
            code_hash=code_hash,
            label=label,
            max_uses=max_uses,
            used_count=0,
            expires_at=expires_at,
            revoked_at=None,
            created_at=now,
            last_used_at=None,
        )

    def consume_invite(self, code: str, now: float | None = None) -> bool:
        """原子消费一次邀请码；无效、过期、撤销、用完都返回 False。"""
        now = time.time() if now is None else now
        code_hash = hash_secret(code.strip())
        with self._lock:
            cur = self._conn.execute(
                "UPDATE auth_invite SET used_count = used_count + 1, last_used_at = ? "
                "WHERE code_hash = ? AND revoked_at IS NULL "
                "AND used_count < max_uses "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (now, code_hash, now),
            )
            self._conn.commit()
        return cur.rowcount == 1

    def get_invite(self, code: str) -> InviteRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT code_hash, label, max_uses, used_count, expires_at, "
                "revoked_at, created_at, last_used_at "
                "FROM auth_invite WHERE code_hash = ?",
                (hash_secret(code.strip()),),
            ).fetchone()
        if row is None:
            return None
        return InviteRecord(
            code_hash=row["code_hash"],
            label=row["label"],
            max_uses=row["max_uses"],
            used_count=row["used_count"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
        )

    def revoke_invite(self, code: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE auth_invite SET revoked_at = ? "
                "WHERE code_hash = ? AND revoked_at IS NULL",
                (time.time(), hash_secret(code.strip())),
            )
            self._conn.commit()
        return cur.rowcount == 1

    # ── token ────────────────────────────────────────────
    def add_token(
        self,
        *,
        token_hash: str,
        user_id: str,
        kind: str,
        expires_at: float,
        parent_hash: str | None = None,
    ) -> None:
        if kind not in ("access", "refresh"):
            raise ValueError("unsupported token kind")
        with self._lock:
            self._conn.execute(
                "INSERT INTO auth_token "
                "(token_hash, user_id, kind, expires_at, created_at, parent_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash, user_id, kind, expires_at, time.time(), parent_hash),
            )
            self._conn.commit()

    def get_token(self, token_hash: str, kind: str) -> TokenRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT token_hash, user_id, kind, expires_at, created_at, "
                "revoked_at, parent_hash FROM auth_token "
                "WHERE token_hash = ? AND kind = ?",
                (token_hash, kind),
            ).fetchone()
        if row is None:
            return None
        return TokenRecord(
            token_hash=row["token_hash"],
            user_id=row["user_id"],
            kind=row["kind"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
            parent_hash=row["parent_hash"],
        )

    def consume_refresh(self, token_hash: str, now: float | None = None) -> TokenRecord | None:
        """原子轮换 refresh token：成功返回旧记录，失败返回 None。"""
        now = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT token_hash, user_id, kind, expires_at, created_at, "
                "revoked_at, parent_hash FROM auth_token "
                "WHERE token_hash = ? AND kind = 'refresh'",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["revoked_at"] is not None or row["expires_at"] <= now:
                return None
            cur = self._conn.execute(
                "UPDATE auth_token SET revoked_at = ? "
                "WHERE token_hash = ? AND revoked_at IS NULL",
                (now, token_hash),
            )
            self._conn.commit()
            if cur.rowcount != 1:
                return None
        return TokenRecord(
            token_hash=row["token_hash"],
            user_id=row["user_id"],
            kind=row["kind"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            revoked_at=now,
            parent_hash=row["parent_hash"],
        )

    def revoke_token(self, token_hash: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE auth_token SET revoked_at = ? "
                "WHERE token_hash = ? AND revoked_at IS NULL",
                (time.time(), token_hash),
            )
            self._conn.commit()
        return cur.rowcount == 1

    def revoke_user_tokens(self, user_id: str, kind: str | None = None) -> int:
        now = time.time()
        with self._lock:
            if kind is None:
                cur = self._conn.execute(
                    "UPDATE auth_token SET revoked_at = ? "
                    "WHERE user_id = ? AND revoked_at IS NULL",
                    (now, user_id),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE auth_token SET revoked_at = ? "
                    "WHERE user_id = ? AND kind = ? AND revoked_at IS NULL",
                    (now, user_id, kind),
                )
            self._conn.commit()
        return cur.rowcount

    def purge_expired(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM auth_token WHERE expires_at <= ?", (now,)
            )
            self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
