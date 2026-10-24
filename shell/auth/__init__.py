# SPDX-FileCopyrightText: 2026 jason081112
# SPDX-License-Identifier: AGPL-3.0-or-later
"""最小账号与会话隔离层。

设计边界：
- 不修改 vendor/OpenHer；
- 认证、token、会话命名空间、聊天历史命名空间全部在 shell/auth/；
- 原始聊天记录仍由上游 chat_log_store 落本地 SQLite，不经过认证层上传。
"""

from .scope import current_auth_user

__all__ = ["current_auth_user"]
