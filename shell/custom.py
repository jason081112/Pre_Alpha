"""用户自定义 provider：任意 OpenAI 兼容端点 + 自带 Key。

安全边界
- 只接受 http(s)；非本机地址强制 https
- 拒绝 URL 内嵌账号密码
- 拒绝字面量内网 IP；创建时做 DNS 检查，运行时固定解析出的 IP
- Key 用 AES-GCM 加密后落库，读取时才解密
- 轮换递增 key_version；撤销清空密文但保留 provider 元数据
- 审计只记事件与版本，不记 Key
"""

from __future__ import annotations

import ipaddress
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .providers import ProviderSpec

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _aad(user_id: str, provider_id: str) -> str:
    """把密文绑定到具体用户与 provider，防止行间搬运。"""
    return f"{user_id}:{provider_id}"


def _last4(value: str) -> str:
    """只保留末四位；短 Key 直接打码，避免“末四位”等于全文。"""
    value = (value or "").strip()
    return value[-4:] if len(value) >= 8 else "****"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_provider (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    label        TEXT NOT NULL,
    base_url     TEXT NOT NULL,
    api_key_enc  TEXT NOT NULL,
    models_json  TEXT NOT NULL DEFAULT '[]',
    caps_json    TEXT NOT NULL DEFAULT '[]',
    key_version  INTEGER NOT NULL DEFAULT 1,
    key_last4    TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL DEFAULT 0,
    revoked_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_user_provider_user ON user_provider(user_id);
CREATE TABLE IF NOT EXISTS provider_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    provider_id  TEXT NOT NULL,
    event        TEXT NOT NULL,
    key_version  INTEGER NOT NULL,
    at           REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_audit_provider
    ON provider_audit(user_id, provider_id, id);
"""

_MIGRATIONS = {
    "key_version": "INTEGER NOT NULL DEFAULT 1",
    "key_last4": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "REAL NOT NULL DEFAULT 0",
    "revoked_at": "REAL",
}


def validate_base_url(value: str, *, allow_private: bool = False) -> str:
    raw = (value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("base_url 只支持 http/https")
    if not parsed.hostname:
        raise ValueError("base_url 缺少主机名")
    if parsed.username or parsed.password:
        raise ValueError("base_url 不能内嵌账号密码")

    host = parsed.hostname
    if parsed.scheme == "http" and host not in _LOCAL_HOSTS:
        raise ValueError("非本机地址必须使用 https")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not allow_private and host not in _LOCAL_HOSTS:
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("不允许指向内网或保留地址")
    return raw


def assert_public_host(
    base_url: str,
    *,
    allow_private: bool = False,
    resolver=None,
):
    """创建/更新 provider 前执行与运行时相同的出站策略。

    解析失败、私网/环回/链路本地/保留地址都统一报通用错误，不回显内网 IP。
    `resolver` 仅用于测试注入。
    """
    from .outbound import plan_outbound

    return plan_outbound(
        base_url,
        allow_private=allow_private,
        resolver=resolver,
    )


@dataclass(frozen=True)
class CustomProvider:
    id: str
    user_id: str
    label: str
    base_url: str
    models: tuple[str, ...]
    caps: tuple[str, ...]
    api_key_enc: str = field(repr=False)
    created_at: float = 0.0
    key_version: int = 1
    key_last4: str = ""
    updated_at: float = 0.0
    revoked_at: float | None = None
    api_key: str = field(default="", repr=False)

    @property
    def has_key(self) -> bool:
        return bool(self.api_key_enc) and self.revoked_at is None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def to_spec(self) -> ProviderSpec:
        return ProviderSpec(
            id=self.id,
            upstream="openai",
            base_url=self.base_url,
            api_key_env="",
            models=self.models,
            caps=self.caps or ("zh",),
            api_key=self.api_key,
            has_key=self.has_key,
        )

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "base_url": self.base_url,
            "models": list(self.models),
            "caps": list(self.caps),
            "has_key": self.has_key,
            "available": self.has_key,
            "custom": True,
            "key_version": self.key_version,
            "key_last4": self.key_last4 if self.has_key else "",
            "revoked": self.revoked,
            "updated_at": self.updated_at,
        }

    def to_row(self) -> dict:
        data = self.to_public()
        data.pop("available", None)
        return data


class CustomProviderStore:
    def __init__(self, path: str | Path, cipher):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._cipher = cipher
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_locked()
            self._conn.commit()

    def _migrate_locked(self) -> None:
        """兼容旧库：补列、补 updated_at、尽量回填末四位。"""
        columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(user_provider)")
        }
        for name, ddl in _MIGRATIONS.items():
            if name not in columns:
                self._conn.execute(
                    f"ALTER TABLE user_provider ADD COLUMN {name} {ddl}"
                )
        self._conn.execute(
            "UPDATE user_provider SET updated_at = created_at WHERE updated_at = 0"
        )
        rows = self._conn.execute(
            "SELECT id, user_id, api_key_enc, key_last4 FROM user_provider"
        ).fetchall()
        for row in rows:
            if not row["api_key_enc"] or row["key_last4"]:
                continue
            try:
                key = self._cipher.decrypt(
                    row["api_key_enc"], _aad(row["user_id"], row["id"])
                )
            except Exception:  # noqa: BLE001 - 旧密钥/换主密钥时不应阻塞启动
                continue
            self._conn.execute(
                "UPDATE user_provider SET key_last4 = ? WHERE id = ?",
                (_last4(key), row["id"]),
            )

    def _row_to_provider(self, row: sqlite3.Row, decrypt: bool) -> CustomProvider:
        revoked_at = row["revoked_at"] if "revoked_at" in row.keys() else None
        key = ""
        if decrypt and row["api_key_enc"] and revoked_at is None:
            key = self._cipher.decrypt(
                row["api_key_enc"], _aad(row["user_id"], row["id"])
            )
        return CustomProvider(
            id=row["id"],
            user_id=row["user_id"],
            label=row["label"],
            base_url=row["base_url"],
            models=tuple(json.loads(row["models_json"] or "[]")),
            caps=tuple(json.loads(row["caps_json"] or "[]")),
            api_key_enc=row["api_key_enc"],
            created_at=row["created_at"],
            key_version=row["key_version"],
            key_last4=row["key_last4"],
            updated_at=row["updated_at"] or row["created_at"],
            revoked_at=revoked_at,
            api_key=key,
        )

    def _audit_locked(
        self,
        user_id: str,
        provider_id: str,
        event: str,
        key_version: int,
        at: float,
    ) -> None:
        self._conn.execute(
            "INSERT INTO provider_audit "
            "(user_id, provider_id, event, key_version, at) VALUES (?, ?, ?, ?, ?)",
            (user_id, provider_id, event, key_version, at),
        )

    def add(
        self,
        user_id: str,
        label: str,
        base_url: str,
        api_key: str,
        models: list[str] | tuple[str, ...] = (),
        caps: list[str] | tuple[str, ...] = ("zh",),
        *,
        allow_private: bool = False,
    ) -> CustomProvider:
        url = validate_base_url(base_url, allow_private=allow_private)
        if not label.strip():
            raise ValueError("label 不能为空")
        secret = api_key.strip()
        if not secret:
            raise ValueError("api_key 不能为空")
        provider_id = f"custom-{secrets.token_hex(4)}"
        now = time.time()
        models_clean = [m.strip() for m in models if m and m.strip()]
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_provider "
                "(id, user_id, label, base_url, api_key_enc, models_json, caps_json, "
                "key_version, key_last4, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    provider_id,
                    user_id,
                    label.strip(),
                    url,
                    self._cipher.encrypt(secret, _aad(user_id, provider_id)),
                    json.dumps(models_clean, ensure_ascii=False),
                    json.dumps(list(caps), ensure_ascii=False),
                    1,
                    _last4(secret),
                    now,
                    now,
                ),
            )
            self._audit_locked(user_id, provider_id, "created", 1, now)
            self._conn.commit()
        provider = self.get(provider_id, user_id, decrypt=False)
        assert provider is not None
        return provider

    def get(
        self, provider_id: str, user_id: str | None = None, *, decrypt: bool = False
    ) -> CustomProvider | None:
        sql = "SELECT * FROM user_provider WHERE id = ?"
        params: list[str] = [provider_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return self._row_to_provider(row, decrypt) if row else None

    def list(self, user_id: str) -> list[CustomProvider]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM user_provider WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()
        return [self._row_to_provider(row, False) for row in rows]

    def update(
        self,
        provider_id: str,
        user_id: str,
        *,
        label: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        models: list[str] | tuple[str, ...] | None = None,
        caps: list[str] | tuple[str, ...] | None = None,
        allow_private: bool = False,
    ) -> CustomProvider | None:
        """更新元数据或轮换 Key；返回 None 表示不属于该用户/不存在。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_provider WHERE id = ? AND user_id = ?",
                (provider_id, user_id),
            ).fetchone()
            if row is None:
                return None
            current = self._row_to_provider(row, False)

            changes: dict[str, object] = {}
            if label is not None:
                if not label.strip():
                    raise ValueError("label 不能为空")
                changes["label"] = label.strip()
            if base_url is not None:
                changes["base_url"] = validate_base_url(
                    base_url, allow_private=allow_private
                )
            if models is not None:
                changes["models_json"] = json.dumps(
                    [m.strip() for m in models if m and m.strip()],
                    ensure_ascii=False,
                )
            if caps is not None:
                changes["caps_json"] = json.dumps(list(caps), ensure_ascii=False)

            rotated = api_key is not None
            if rotated:
                secret = api_key.strip()
                if not secret:
                    raise ValueError("api_key 不能为空；撤销请调用 revoke")
                changes["api_key_enc"] = self._cipher.encrypt(
                    secret, _aad(user_id, provider_id)
                )
                changes["key_last4"] = _last4(secret)
                changes["key_version"] = current.key_version + 1
                changes["revoked_at"] = None

            if not changes:
                return current

            now = time.time()
            changes["updated_at"] = now
            assignments = ", ".join(f"{key} = ?" for key in changes)
            self._conn.execute(
                f"UPDATE user_provider SET {assignments} WHERE id = ? AND user_id = ?",
                (*changes.values(), provider_id, user_id),
            )
            version = int(changes.get("key_version", current.key_version))
            self._audit_locked(
                user_id,
                provider_id,
                "rotated" if rotated else "updated",
                version,
                now,
            )
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM user_provider WHERE id = ? AND user_id = ?",
                (provider_id, user_id),
            ).fetchone()
            assert updated is not None
            return self._row_to_provider(updated, False)

    def revoke(self, provider_id: str, user_id: str) -> CustomProvider | None:
        """撤销 Key：清空密文、递增版本，但保留 provider 以便重新配置。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_provider WHERE id = ? AND user_id = ?",
                (provider_id, user_id),
            ).fetchone()
            if row is None:
                return None
            current = self._row_to_provider(row, False)
            if current.revoked:
                return current
            now = time.time()
            version = current.key_version + 1
            self._conn.execute(
                "UPDATE user_provider SET api_key_enc = '', key_last4 = '', "
                "key_version = ?, revoked_at = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (version, now, now, provider_id, user_id),
            )
            self._audit_locked(user_id, provider_id, "revoked", version, now)
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM user_provider WHERE id = ? AND user_id = ?",
                (provider_id, user_id),
            ).fetchone()
            assert updated is not None
            return self._row_to_provider(updated, False)

    def delete(self, provider_id: str, user_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT key_version FROM user_provider WHERE id = ? AND user_id = ?",
                (provider_id, user_id),
            ).fetchone()
            if row is None:
                return False
            cur = self._conn.execute(
                "DELETE FROM user_provider WHERE id = ? AND user_id = ?",
                (provider_id, user_id),
            )
            self._audit_locked(
                user_id, provider_id, "deleted", int(row["key_version"]), time.time()
            )
            self._conn.commit()
        return cur.rowcount > 0

    def list_audit(
        self, provider_id: str, user_id: str, limit: int = 50
    ) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event, key_version, at FROM provider_audit "
                "WHERE provider_id = ? AND user_id = ? ORDER BY id ASC LIMIT ?",
                (provider_id, user_id, limit),
            ).fetchall()
        return [
            {"event": row["event"], "key_version": row["key_version"], "at": row["at"]}
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
