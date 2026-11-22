"""试用身份令牌：Cookie 里放随机串，库里只存 sha256（BE-102）。"""
from __future__ import annotations

import hashlib
import secrets

TOKEN_BYTES = 32


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """格式：pbkdf2_sha256$迭代次数$盐$哈希。"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (PBKDF2_ITERATIONS, salt, dk.hex())


def verify_password(password: str, stored: str) -> bool:
    import hmac

    try:
        algo, iterations, salt, expected = (stored or "").split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(dk.hex(), expected)
