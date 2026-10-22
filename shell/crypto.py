"""用户 Key 的落盘加密（AES-GCM）。

主密钥来源优先级：
1. `ALPHA_MASTER_KEY`：
   - 64 位 hex → 直接当作 32 字节
   - base64 → 必须是 32 字节
   - 其他 → 视为口令，scrypt 派生（salt 存 `.data/master.salt`，0600）
2. 未设置且 `ALPHA_REQUIRE_MASTER_KEY=1` → 直接拒绝启动
3. 本地 `.data/master.key`：首次自动生成 32 字节随机密钥，权限 0600

加密结果 = `v1:` + base64(nonce || ciphertext)，AAD 绑定 `user_id:provider_id`，
防止密文在行之间被搬运。旧格式（无前缀、无 AAD）仍可解密。

安全边界：
- 防的是数据库/备份被单独拿走；整机沦陷时主密钥也在，防不住
- 浏览器永远拿不到长期 Key，XSS 无法从 localStorage 偷 Key
- 主密钥与 DB 应分开放（生产用环境变量，不要和 DB 同目录备份）
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

_PREFIX = "v1:"
_SALT_BYTES = 16
_KEY_BYTES = 32


def _write_private(path: Path, data: bytes) -> None:
    """原子创建 0600 文件，避免先写后 chmod 的窗口。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _load_or_create_salt(data_dir: Path) -> bytes:
    path = data_dir / "master.salt"
    if path.exists():
        salt = path.read_bytes()
        if len(salt) == _SALT_BYTES:
            return salt
    salt = os.urandom(_SALT_BYTES)
    _write_private(path, salt)
    return path.read_bytes() if path.exists() else salt


def _load_or_create_key(data_dir: Path) -> bytes:
    path = data_dir / "master.key"
    if path.exists():
        key = path.read_bytes()
        if len(key) == _KEY_BYTES:
            return key
    key = os.urandom(_KEY_BYTES)
    _write_private(path, key)
    return path.read_bytes()


def load_master_key(data_dir: str | Path) -> bytes:
    data_dir = Path(data_dir)
    env = os.getenv("ALPHA_MASTER_KEY", "").strip()

    if env:
        if len(env) == 64:
            try:
                return bytes.fromhex(env)
            except ValueError:
                pass
        try:
            decoded = base64.urlsafe_b64decode(env + "=" * (-len(env) % 4))
            if len(decoded) == _KEY_BYTES:
                return decoded
        except (ValueError, TypeError):
            pass
        # 口令 → scrypt（比 SHA-256 更能抵抗离线爆破）
        salt = _load_or_create_salt(data_dir)
        return hashlib.scrypt(
            env.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=_KEY_BYTES
        )

    if os.getenv("ALPHA_REQUIRE_MASTER_KEY", "").strip().lower() in {"1", "true", "yes"}:
        raise RuntimeError(
            "已设置 ALPHA_REQUIRE_MASTER_KEY，但 ALPHA_MASTER_KEY 为空；拒绝启动"
        )
    return _load_or_create_key(data_dir)


class SecretBox:
    """AES-GCM 加解密；密钥只驻留内存。"""

    def __init__(self, key: bytes):
        if len(key) != _KEY_BYTES:
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

    def encrypt(self, plaintext: str, aad: str = "") -> str:
        nonce = os.urandom(12)
        ciphertext = self._aes.encrypt(
            nonce, plaintext.encode("utf-8"), aad.encode("utf-8") if aad else None
        )
        return _PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, token: str, aad: str = "") -> str:
        if token.startswith(_PREFIX):
            raw = base64.urlsafe_b64decode(token[len(_PREFIX) :].encode("ascii"))
            associated = aad.encode("utf-8") if aad else None
        else:  # 兼容旧格式：无 AAD
            raw = base64.urlsafe_b64decode(token.encode("ascii"))
            associated = None
        nonce, ciphertext = raw[:12], raw[12:]
        return self._aes.decrypt(nonce, ciphertext, associated).decode("utf-8")
