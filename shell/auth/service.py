# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""账号业务层：密码、邀请码、access/refresh token 生命周期。

依赖只使用标准库，方便在没有 FastAPI 的环境里做单元测试。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from .store import AuthStore, AuthUser, hash_secret

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024


class AuthError(Exception):
    """带 HTTP 语义的认证错误。"""

    def __init__(self, code: str, message: str, status_code: int = 401):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_in: int
    user: AuthUser

    def as_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": "bearer",
            "expires_in": self.expires_in,
            "user": self.user.as_public(),
        }


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def hash_password(password: str) -> str:
    """scrypt 密码哈希，格式自带参数，未来可升级。"""
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}"
        f"${_b64encode(salt)}${_b64encode(digest)}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n_raw, r_raw, p_raw, salt_raw, digest_raw = encoded.split("$")
        if algo != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        # 防止被恶意哈希参数拖死
        if not (2**12 <= n <= 2**18) or not (1 <= r <= 16) or not (1 <= p <= 8):
            return False
        salt = _b64decode(salt_raw)
        expected = _b64decode(digest_raw)
        if not (16 <= len(salt) <= 64) or not (16 <= len(expected) <= 64):
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=128 * 1024 * 1024,
        )
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < 8:
        raise AuthError("password_too_short", "密码至少 8 位", 422)
    if len(password) > 128:
        raise AuthError("password_too_long", "密码最多 128 位", 422)


def validate_email(email: str) -> str:
    normalized = normalize_email(email)
    if not normalized or len(normalized) > 254 or not _EMAIL_RE.match(normalized):
        raise AuthError("email_invalid", "邮箱格式不正确", 422)
    return normalized


class AuthService:
    """注册、登录、刷新、鉴权。

    `require_invite=True` 时注册必须带有效邀请码；关闭后进入开放注册阶段。
    `clock` 可注入，便于测试过期逻辑。
    """

    def __init__(
        self,
        store: AuthStore,
        *,
        access_ttl: float = 900,
        refresh_ttl: float = 30 * 24 * 3600,
        require_invite: bool = True,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.access_ttl = float(access_ttl)
        self.refresh_ttl = float(refresh_ttl)
        self.require_invite = bool(require_invite)
        self.clock = clock

    def register(self, email: str, password: str, invite_code: str | None = None) -> TokenBundle:
        normalized = validate_email(email)
        validate_password(password)
        if self.store.get_identity("email", normalized) is not None:
            raise AuthError("email_taken", "该邮箱已注册", 409)

        if self.require_invite:
            code = (invite_code or "").strip()
            if not code:
                raise AuthError("invite_required", "需要邀请码", 403)
            if not self.store.consume_invite(code, now=self.clock()):
                raise AuthError("invite_invalid", "邀请码无效、已过期或已用完", 403)

        password_hash = hash_password(password)
        try:
            user = self.store.create_user_with_identity(
                kind="email",
                identifier=normalized,
                normalized=normalized,
                password_hash=password_hash,
            )
        except ValueError as exc:
            if "identity_exists" in str(exc):
                raise AuthError("email_taken", "该邮箱已注册", 409) from exc
            raise
        return self._issue(user)

    def login(self, email: str, password: str) -> TokenBundle:
        normalized = normalize_email(email)
        identity = (
            self.store.get_identity("email", normalized) if normalized else None
        )
        if identity is None or not verify_password(password, identity.password_hash):
            raise AuthError("invalid_credentials", "邮箱或密码错误", 401)
        if identity.user.disabled:
            raise AuthError("account_disabled", "账号已停用", 403)
        return self._issue(identity.user)

    def refresh(self, refresh_token: str) -> TokenBundle:
        token = (refresh_token or "").strip()
        if not token:
            raise AuthError("invalid_refresh", "refresh token 无效或已过期", 401)
        now = self.clock()
        record = self.store.consume_refresh(hash_secret(token), now=now)
        if record is None:
            raise AuthError("invalid_refresh", "refresh token 无效或已过期", 401)
        user = self.store.get_user(record.user_id)
        if user is None or user.disabled:
            raise AuthError("account_disabled", "账号已停用", 403)
        return self._issue(user, parent_hash=record.token_hash)

    def authenticate(self, access_token: str | None) -> AuthUser | None:
        token = (access_token or "").strip()
        if not token:
            return None
        record = self.store.get_token(hash_secret(token), "access")
        if record is None or record.revoked_at is not None:
            return None
        if record.expires_at <= self.clock():
            return None
        user = self.store.get_user(record.user_id)
        if user is None or user.disabled:
            return None
        return user

    def logout(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        if access_token:
            self.store.revoke_token(hash_secret(access_token.strip()))
        if refresh_token:
            self.store.revoke_token(hash_secret(refresh_token.strip()))

    def get_user(self, user_id: str) -> AuthUser | None:
        return self.store.get_user(user_id)

    def _issue(self, user: AuthUser, parent_hash: str | None = None) -> TokenBundle:
        now = self.clock()
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        self.store.add_token(
            token_hash=hash_secret(access),
            user_id=user.id,
            kind="access",
            expires_at=now + self.access_ttl,
        )
        self.store.add_token(
            token_hash=hash_secret(refresh),
            user_id=user.id,
            kind="refresh",
            expires_at=now + self.refresh_ttl,
            parent_hash=parent_hash,
        )
        return TokenBundle(
            access_token=access,
            refresh_token=refresh,
            expires_in=int(self.access_ttl),
            user=user,
        )


def service_from_env(store: AuthStore) -> AuthService:
    """从环境变量构造，便于 app 与脚本共用默认值。"""
    return AuthService(
        store,
        access_ttl=float(os.getenv("ALPHA_ACCESS_TTL", "900")),
        refresh_ttl=float(os.getenv("ALPHA_REFRESH_TTL", str(30 * 24 * 3600))),
        require_invite=_env_bool("ALPHA_REQUIRE_INVITE", True),
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}
