"""用户 Key 的落盘加密（AES-GCM）。

主密钥来源优先级：
1. `ALPHA_MASTER_KEY`：64 位 hex / base64(32B) / 任意口令（SHA-256 派生）
2. 本地 `.data/master.key`：首次自动生成，权限 0600

加密结果 = base64(nonce || ciphertext)，可以直接存 TEXT 列。
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path


def load_master_key(data_dir: Path) -> bytes:
    env = os.getenv("ALPHA_MASTER_KEY", "").strip()
    if env:
        if len(env) == 64:
            try:
                return bytes.fromhex(env)
            except ValueError:
                pass
        try:
            decoded = base64.urlsafe_b64decode(env + "=" * (-len(env) % 4))
            if len(decoded) == 32:
                return decoded
        except (ValueError, TypeError):
            pass
        return hashlib.sha256(env.encode("utf-8")).digest()

    path = Path(data_dir) / "master.key"
    if path.exists():
        key = path.read_bytes()
        if len(key) == 32:
            return key

    key = os.urandom(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


class SecretBox:
    """AES-GCM 加解密；密钥只驻留内存。"""

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("master key 必须是 32 字节")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover - 运行环境问题
            raise RuntimeError(
                "缺少 cryptography，无法加密用户 Key；请安装 cryptography>=41"
            ) from exc
        self._aes = AESGCM(key)

    @classmethod
    def from_env(cls, data_dir: str | Path) -> "SecretBox":
        return cls(load_master_key(Path(data_dir)))

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, token: str) -> str:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        nonce, ciphertext = raw[:12], raw[12:]
        return self._aes.decrypt(nonce, ciphertext, None).decode("utf-8")
