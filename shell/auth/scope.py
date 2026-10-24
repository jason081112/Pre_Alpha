# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""用户命名空间：会话 ID、聊天历史 client_id、请求上下文。

上游两个隐患：
- active_sessions 只按 session_id 查找，不校验 user；
- chat_log_store 只按前端 client_id 分区。

外壳不改 vendor，只把这两类 ID 在进入上游前映射到当前登录用户。
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from contextvars import ContextVar

current_auth_user: ContextVar[str] = ContextVar("alpha_auth_user", default="local")
# 认证关闭时保留旧脚本的 session_id / client_id 语义；生产认证开启时默认隔离。
current_auth_scope_enabled: ContextVar[bool] = ContextVar(
    "alpha_auth_scope_enabled", default=True
)

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]+\.[0-9a-f]{8}$")


def _user_key(user: str) -> str:
    return hashlib.sha256((user or "local").encode("utf-8")).hexdigest()[:16]


def session_prefix(user: str) -> str:
    return "u" + _user_key(user)


def new_session_id(user: str) -> str:
    if not current_auth_scope_enabled.get() or not user or user == "local":
        return uuid.uuid4().hex[:8]
    return f"{session_prefix(user)}.{secrets.token_hex(4)}"


def validate_session_id(session_id: str | None, user: str) -> str | None:
    """空值放行；非空值必须属于当前用户。"""
    if not session_id:
        return None
    session_id = str(session_id).strip()
    if not session_id:
        return None
    if not current_auth_scope_enabled.get() or not user or user == "local":
        return session_id
    prefix = session_prefix(user)
    if not _SESSION_RE.match(session_id) or not session_id.startswith(prefix + "."):
        raise ValueError("session 不存在或不属于当前用户")
    return session_id


def prepare_session_id(session_id: str | None, user: str) -> str:
    return validate_session_id(session_id, user) or new_session_id(user)


def scoped_client_id(user: str, client_id: str | None) -> str | None:
    """把前端 client_id 映射成用户私有命名空间。"""
    if not client_id:
        return None
    raw = str(client_id).strip()
    if not raw:
        return None
    if not current_auth_scope_enabled.get() or not user or user == "local":
        return raw
    if len(raw) > 128:
        raw = "h:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{session_prefix(user)}:{raw}"


class ScopedChatLog:
    """ChatLogStore 的用户隔离代理。

    只包装公开读写方法；上游 greeting 直接访问 `_conn` 的旁路无法拦截，
    但那条路径只写入 raw client_id，不会读回其他用户数据。
    """

    def __init__(self, inner):
        self._inner = inner

    def _client(self, client_id: str | None) -> str | None:
        return scoped_client_id(current_auth_user.get(), client_id)

    def save_turn(
        self,
        client_id: str,
        persona_id: str,
        user_msg: str,
        agent_reply: str,
        modality: str = "文字",
        image_url: str | None = None,
    ) -> None:
        client = self._client(client_id)
        if client is None:
            return
        self._inner.save_turn(
            client_id=client,
            persona_id=persona_id,
            user_msg=user_msg,
            agent_reply=agent_reply,
            modality=modality,
            image_url=image_url,
        )

    def save_message(
        self,
        client_id: str,
        persona_id: str,
        role: str,
        content: str,
        modality: str = "文字",
        image_url: str | None = None,
    ) -> None:
        client = self._client(client_id)
        if client is None:
            return
        self._inner.save_message(
            client_id=client,
            persona_id=persona_id,
            role=role,
            content=content,
            modality=modality,
            image_url=image_url,
        )

    def load_messages(
        self,
        client_id: str,
        persona_id: str,
        limit: int = 50,
        before_id: int | None = None,
    ) -> list[dict]:
        client = self._client(client_id)
        if client is None:
            return []
        return self._inner.load_messages(client, persona_id, limit, before_id)

    def count_messages(self, client_id: str, persona_id: str) -> int:
        client = self._client(client_id)
        if client is None:
            return 0
        return self._inner.count_messages(client, persona_id)

    def close(self) -> None:
        self._inner.close()

    def __getattr__(self, name):
        return getattr(self._inner, name)
