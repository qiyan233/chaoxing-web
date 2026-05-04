# -*- coding: utf-8 -*-
"""凭据加密 / 哈希工具

- 超星账号密码：用 Fernet 对称加密，可解密供登录使用
- 管理员密码：用 PBKDF2-SHA256 哈希，仅用于校验
"""
import base64
import hashlib
import hmac
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

from webapp.config import FERNET_KEY


_fernet = Fernet(FERNET_KEY)


# ---------- 超星账号密码 (可逆加密) ----------
def encrypt_password(plain: str) -> bytes:
    """加密密码为 bytes，可存到 LargeBinary 字段"""
    return _fernet.encrypt(plain.encode("utf-8"))


def decrypt_password(token: bytes) -> str:
    """解密密码"""
    try:
        return _fernet.decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("无法解密密码：密钥可能已变更") from exc


# ---------- 管理员密码 (单向哈希) ----------
PBKDF2_ITERATIONS = 200_000
PBKDF2_SALT_LEN = 16


def hash_admin_password(plain: str) -> str:
    """生成 PBKDF2 哈希字符串： pbkdf2_sha256$iterations$salt_b64$hash_b64"""
    salt = os.urandom(PBKDF2_SALT_LEN)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plain.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return "pbkdf2_sha256${iter}${salt}${hash}".format(
        iter=PBKDF2_ITERATIONS,
        salt=base64.b64encode(salt).decode("ascii"),
        hash=base64.b64encode(digest).decode("ascii"),
    )


def verify_admin_password(plain: str, hashed: str) -> bool:
    """校验管理员密码"""
    try:
        algo, iter_str, salt_b64, hash_b64 = hashed.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", plain.encode("utf-8"), salt, int(iter_str)
        )
        return hmac.compare_digest(expected, actual)
    except (ValueError, TypeError):
        return False


def random_token(length: int = 32) -> str:
    """生成 URL 安全的随机 token"""
    return secrets.token_urlsafe(length)
