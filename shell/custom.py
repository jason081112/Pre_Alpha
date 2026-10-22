"""用户自定义 provider：任意 OpenAI 兼容端点 + 自带 Key。

安全边界
- 只接受 http(s)；非本机地址强制 https
- 拒绝 URL 内嵌账号密码
- 拒绝字面量内网 IP；创建时再做一次 DNS 解析检查（API 层调用）
- Key 用 AES-GCM 加密后落库，读取时才解密
"""

from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .providers import ProviderSpec

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_provider (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    label        TEXT NOT NULL,
    base_url     TEXT NOT NULL,
    api_key_enc  TEXT NOT NULL,
    models_json  TEXT NOT NULL DEFAULT '[]',
    caps_json    TEXT NOT NULL DEFAULT '[]',
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_provider_user ON user_provider(user_id);
"""


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


def assert_public_host(base_url: str, *, allow_private: bool = False) -> None:
    """创建 provider 前做 DNS 解析，防止域名指向内网（SSRF）。"""
    if allow_private:
        return
    host = urlparse(base_url).hostname
    if not host or host in _LOCAL_HOSTS:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"域名解析失败: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"域名 {host} 解析到内网地址，已拒绝")


@dataclass(frozen=True)
class CustomProvider:
    id: str
    user_id: str
    label: str
    base_url: str
    models: tuple[str, ...]
    caps: tuple[str, ...]
    api_key_enc: str
    created_at: float
    api_key: str = ""

    @property
    def has_key(self) -> bool:
        return bool(self.api_key_enc)

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
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _row_to_provider(self, row: sqlite3.Row, decrypt: bool) -> CustomProvider:
        return CustomProvider(
            id=row["id"],
            user_id=row["user_id"],
            label=row["label"],
            base_url=row["base_url"],
            models=tuple(json.loads(row["models_json"] or "[]")),
            caps=tuple(json.loads(row["caps_json"] or "[]")),
            api_key_enc=row["api_key_enc"],
            created_at=row["created_at"],
            api_key=self._cipher.decrypt(row["api_key_enc"]) if decrypt else "",
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
        if not api_key.strip():
            raise ValueError("api_key 不能为空")
        provider_id = f"custom-{secrets.token_hex(4)}"
        now = time.time()
        models_clean = [m.strip() for m in models if m and m.strip()]
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_provider "
                "(id, user_id, label, base_url, api_key_enc, models_json, caps_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    provider_id,
                    user_id,
                    label.strip(),
                    url,
                    self._cipher.encrypt(api_key.strip()),
                    json.dumps(models_clean, ensure_ascii=False),
                    json.dumps(list(caps), ensure_ascii=False),
                    now,
                ),
            )
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

    def delete(self, provider_id: str, user_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM user_provider WHERE id = ? AND user_id = ?",
                (provider_id, user_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
