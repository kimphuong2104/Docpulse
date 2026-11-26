"""Mật khẩu và JWT HS256 bằng thư viện chuẩn, không ghi secret ra log."""

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from typing import Any

from app.config import JWT_SECRET

ACCESS_TTL = int(os.getenv("ACCESS_TTL_SECONDS", "900"))
REFRESH_TTL = int(os.getenv("REFRESH_TTL_SECONDS", str(14 * 24 * 3600)))
_PBKDF2_ROUNDS = 200_000


def redact(text: str) -> str:
    cleaned = re.sub(r"key=[^&\s\"']+", "key=***", text)
    cleaned = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", cleaned)
    return cleaned[:400]


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
    return hmac.compare_digest(digest.hex(), digest_hex)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_refresh_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def issue_access_token(user_id: str, email: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    now = int(time.time())
    payload = _b64(
        json.dumps(
            {"sub": user_id, "email": email, "iat": now, "exp": now + ACCESS_TTL},
            separators=(",", ":"),
        ).encode()
    )
    signing = f"{header}.{payload}".encode()
    signature = _b64(hmac.new(JWT_SECRET.encode(), signing, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        header, payload, signature = token.split(".")
    except ValueError as exc:
        raise ValueError("Token không hợp lệ.") from exc
    signing = f"{header}.{payload}".encode()
    expected = _b64(hmac.new(JWT_SECRET.encode(), signing, hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature):
        raise ValueError("Token không hợp lệ.")
    data = json.loads(_b64decode(payload))
    if int(data.get("exp", 0)) < int(time.time()):
        raise ValueError("Token đã hết hạn.")
    if not data.get("sub"):
        raise ValueError("Token không hợp lệ.")
    return data
